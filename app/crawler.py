import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup
from lxml import etree

from . import database as db
from .adapters import adapter_for_url
from .config import ASSET_EXTENSIONS, DEFAULT_TIMEOUT_SECONDS, DEFAULT_USER_AGENT, MAX_BODY_BYTES

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid",
    "mc_cid", "mc_eid", "ref", "referrer"
}
DYNAMIC_HINTS = re.compile(
    r"enable javascript|javascript is required|please enable javascript|id=[\"'](?:app|root|__next)[\"']",
    re.IGNORECASE,
)


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    headers: dict[str, str]
    rendered: bool = False
    not_modified: bool = False


class BrowserRenderer:
    def __init__(self):
        self.playwright = None
        self.browser = None

    async def start(self):
        if self.browser:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError("Playwright is not installed") from exc
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)

    async def fetch(self, url: str, timeout_ms: int = 30000) -> FetchResult:
        await self.start()
        page = await self.browser.new_page(user_agent=DEFAULT_USER_AGENT)
        try:
            response = await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            try:
                await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10000))
            except Exception:
                pass
            html = await page.content()
            status = response.status if response else 200
            headers = await response.all_headers() if response else {}
            return FetchResult(url=page.url, status=status, text=html, headers=headers, rendered=True)
        finally:
            await page.close()

    async def close(self):
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None


class CrawlEngine:
    def __init__(self, source: dict, job_id: int, force_full: bool = False):
        self.source = source
        self.source_id = int(source["id"])
        self.job_id = job_id
        self.force_full = force_full
        self.start_url = canonicalize_url(source["start_url"])
        self.start_host = urlsplit(self.start_url).hostname or ""
        self.max_pages = int(source["max_pages"])
        self.max_depth = int(source["max_depth"])
        self.concurrency = int(source["concurrency"])
        self.delay = int(source["delay_ms"]) / 1000.0
        self.render_mode = source["render_mode"]
        self.respect_robots = bool(source["respect_robots"])
        self.queue: asyncio.Queue[tuple[str, int]] = asyncio.Queue()
        self.seen: set[str] = set()
        self.processed = 0
        self.stop_requested = False
        self.robot_parser: RobotFileParser | None = None
        self.robot_sitemaps: list[str] = []
        self.robot_delay: float | None = None
        self.renderer = BrowserRenderer()
        self.adapter = adapter_for_url(self.start_url)
        self._request_gate = asyncio.Lock()
        self._last_request_at = 0.0

    async def run(self):
        await db.update_job(self.job_id, status="running", started_at=db.utcnow(), message="Preparing crawl")
        timeout = httpx.Timeout(DEFAULT_TIMEOUT_SECONDS)
        limits = httpx.Limits(max_connections=max(self.concurrency * 2, 10), max_keepalive_connections=max(self.concurrency, 5))
        async with httpx.AsyncClient(
            timeout=timeout,
            limits=limits,
            follow_redirects=True,
            headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"},
        ) as client:
            try:
                await self._load_robots(client)
                await self._enqueue(self.start_url, 0)
                for sitemap_url in await self._discover_sitemap_urls(client):
                    await self._enqueue(sitemap_url, 0)
                workers = [asyncio.create_task(self._worker(client)) for _ in range(self.concurrency)]
                await self.queue.join()
                self.stop_requested = True
                for worker in workers:
                    worker.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
                await db.update_job(
                    self.job_id,
                    status="completed",
                    finished_at=db.utcnow(),
                    message=f"Completed. {self.processed} pages processed.",
                )
                await self._mark_source_scanned()
            except asyncio.CancelledError:
                await db.update_job(self.job_id, status="cancelled", finished_at=db.utcnow(), message="Crawl cancelled")
                raise
            except Exception as exc:
                await db.update_job(self.job_id, status="failed", finished_at=db.utcnow(), message=str(exc)[:1000])
                raise
            finally:
                await self.renderer.close()

    async def _mark_source_scanned(self):
        await db.mark_source_scanned(self.source_id)

    async def _worker(self, client: httpx.AsyncClient):
        while not self.stop_requested:
            url, depth = await self.queue.get()
            try:
                if self.processed >= self.max_pages:
                    continue
                if self.respect_robots and self.robot_parser and not self.robot_parser.can_fetch(DEFAULT_USER_AGENT, url):
                    await db.increment_job(self.job_id, errors=1)
                    await db.upsert_page(self.source_id, url, last_error="Blocked by robots.txt policy")
                    continue
                await self._process_url(client, url, depth)
            except Exception as exc:
                await db.increment_job(self.job_id, errors=1)
                await db.upsert_page(self.source_id, url, last_error=str(exc)[:1000])
            finally:
                self.queue.task_done()

    async def _process_url(self, client: httpx.AsyncClient, url: str, depth: int):
        cached = await db.get_page(self.source_id, url)
        result = await self._fetch(client, url, cached)
        self.processed += 1
        await db.increment_job(self.job_id, pages_processed=1)

        if result.not_modified and cached:
            links = json.loads(cached.get("discovered_links") or "[]")
            for link in links:
                await self._enqueue(link, depth + 1)
            return

        if result.status >= 400:
            await db.increment_job(self.job_id, errors=1)
            await db.upsert_page(
                self.source_id, url, status_code=result.status,
                last_error=f"HTTP {result.status}",
            )
            return

        content_hash = hashlib.sha256(result.text.encode("utf-8", errors="ignore")).hexdigest()
        if cached and cached.get("content_hash") == content_hash and not self.force_full:
            links = json.loads(cached.get("discovered_links") or "[]")
            await db.upsert_page(
                self.source_id, url, status_code=result.status, etag=result.headers.get("etag"),
                last_modified=result.headers.get("last-modified"), content_hash=content_hash,
                discovered_links=json.dumps(links), last_error=None,
            )
            for link in links:
                await self._enqueue(link, depth + 1)
            return

        links = [canonicalize_url(x) for x in self.adapter.links(result.text, result.url)]
        links = [x for x in links if self._allowed_url(x)]
        records = self.adapter.extract(result.text, result.url)
        await db.increment_job(self.job_id, records_found=len(records))
        for record in records:
            outcome = await db.upsert_record(self.source_id, record)
            if outcome == "new":
                await db.increment_job(self.job_id, records_new=1)
            elif outcome == "updated":
                await db.increment_job(self.job_id, records_updated=1)

        await db.upsert_page(
            self.source_id, url, status_code=result.status, etag=result.headers.get("etag"),
            last_modified=result.headers.get("last-modified"), content_hash=content_hash,
            discovered_links=json.dumps(links), last_error=None,
        )
        if depth < self.max_depth:
            for link in links:
                await self._enqueue(link, depth + 1)

    async def _fetch(self, client: httpx.AsyncClient, url: str, cached: dict | None) -> FetchResult:
        if self.render_mode == "browser":
            await self._throttle()
            return await self.renderer.fetch(url)

        headers: dict[str, str] = {}
        if cached and not self.force_full:
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]

        result = await self._http_fetch_with_retry(client, url, headers)
        if result.status == 304:
            result.not_modified = True
            return result

        # Browser rendering is a rendering fallback only. Explicit denials/challenges are never bypassed.
        if self.render_mode == "auto" and result.status == 200 and self._looks_dynamic(result.text):
            await self._throttle()
            try:
                rendered = await self.renderer.fetch(result.url)
                if rendered.status < 400 and len(BeautifulSoup(rendered.text, "lxml").get_text(" ", strip=True)) > 100:
                    return rendered
            except Exception:
                pass
        return result

    async def _http_fetch_with_retry(self, client: httpx.AsyncClient, url: str, headers: dict[str, str]) -> FetchResult:
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                await self._throttle()
                response = await client.get(url, headers=headers)
                if response.status_code in {429, 500, 502, 503, 504}:
                    retry_after = response.headers.get("retry-after")
                    if retry_after and retry_after.isdigit():
                        wait = min(float(retry_after), 60.0)
                    else:
                        wait = min(2 ** attempt, 20)
                    await asyncio.sleep(wait)
                    continue
                content_type = response.headers.get("content-type", "").lower()
                if response.status_code != 304 and not any(x in content_type for x in ("text/", "html", "xml", "json", "xhtml")):
                    return FetchResult(str(response.url), response.status_code, "", dict(response.headers))
                body = response.content[:MAX_BODY_BYTES]
                encoding = response.encoding or "utf-8"
                text = body.decode(encoding, errors="replace")
                return FetchResult(str(response.url), response.status_code, text, dict(response.headers))
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                await asyncio.sleep(min(2 ** attempt, 20))
        if last_error:
            raise last_error
        raise RuntimeError(f"Failed to fetch {url} after retries")

    async def _throttle(self):
        delay = max(self.delay, self.robot_delay or 0.0)
        async with self._request_gate:
            elapsed = time.monotonic() - self._last_request_at
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_request_at = time.monotonic()

    def _looks_dynamic(self, html: str) -> bool:
        if not html:
            return False
        soup = BeautifulSoup(html, "lxml")
        text_len = len(soup.get_text(" ", strip=True))
        anchors = len(soup.find_all("a", href=True))
        scripts = len(soup.find_all("script"))
        return bool(DYNAMIC_HINTS.search(html)) or (text_len < 250 and scripts >= 3 and anchors <= 2)

    async def _enqueue(self, url: str, depth: int):
        url = canonicalize_url(url)
        if not url or depth > self.max_depth or not self._allowed_url(url):
            return
        if len(self.seen) >= self.max_pages:
            return
        if url in self.seen:
            return
        self.seen.add(url)
        await self.queue.put((url, depth))
        await db.increment_job(self.job_id, pages_discovered=1)

    def _allowed_url(self, url: str) -> bool:
        try:
            parts = urlsplit(url)
        except ValueError:
            return False
        if parts.scheme not in {"http", "https"}:
            return False
        host = parts.hostname or ""
        if host.lower() != self.start_host.lower():
            return False
        lower_path = parts.path.lower()
        if any(lower_path.endswith(ext) for ext in ASSET_EXTENSIONS):
            return False
        return True

    async def _load_robots(self, client: httpx.AsyncClient):
        robots_url = urljoin(self.start_url, "/robots.txt")
        try:
            response = await client.get(robots_url)
            if response.status_code != 200:
                return
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(response.text.splitlines())
            self.robot_parser = parser
            delay = parser.crawl_delay(DEFAULT_USER_AGENT) or parser.crawl_delay("*")
            self.robot_delay = float(delay) if delay is not None else None
            for line in response.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    candidate = line.split(":", 1)[1].strip()
                    if candidate:
                        self.robot_sitemaps.append(candidate)
        except Exception:
            self.robot_parser = None

    async def _discover_sitemap_urls(self, client: httpx.AsyncClient) -> list[str]:
        candidates = list(dict.fromkeys([*self.robot_sitemaps, urljoin(self.start_url, "/sitemap.xml")]))
        discovered: list[str] = []
        visited_sitemaps: set[str] = set()
        for sitemap in candidates[:20]:
            await self._parse_sitemap(client, sitemap, discovered, visited_sitemaps, depth=0)
            if len(discovered) >= self.max_pages:
                break
        return discovered[: self.max_pages]

    async def _parse_sitemap(self, client: httpx.AsyncClient, sitemap_url: str, discovered: list[str], visited: set[str], depth: int):
        if depth > 3 or sitemap_url in visited or len(discovered) >= self.max_pages:
            return
        visited.add(sitemap_url)
        if not self._allowed_url(sitemap_url) and (urlsplit(sitemap_url).hostname or "").lower() != self.start_host.lower():
            return
        try:
            await self._throttle()
            response = await client.get(sitemap_url)
            if response.status_code != 200 or len(response.content) > MAX_BODY_BYTES:
                return
            root = etree.fromstring(response.content)
            local = etree.QName(root).localname.lower()
            locs = [clean_xml_text(x.text) for x in root.xpath("//*[local-name()='loc']") if clean_xml_text(x.text)]
            if local == "sitemapindex":
                for loc in locs[:1000]:
                    await self._parse_sitemap(client, loc, discovered, visited, depth + 1)
            else:
                for loc in locs:
                    url = canonicalize_url(loc)
                    if self._allowed_url(url):
                        discovered.append(url)
                        if len(discovered) >= self.max_pages:
                            break
        except Exception:
            return


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""
    if parts.scheme not in {"http", "https"}:
        return ""
    query_items = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    query_items.sort()
    path = re.sub(r"/{2,}", "/", parts.path or "/")
    netloc = parts.netloc.lower()
    return urlunsplit((parts.scheme.lower(), netloc, path, urlencode(query_items, doseq=True), ""))


def clean_xml_text(value: str | None) -> str:
    return (value or "").strip()
