import asyncio
import hashlib
import json
import re
import time
import weakref
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from bs4 import BeautifulSoup, UnicodeDammit
from lxml import etree

from . import database as db
from . import activity
from . import bidder_master as bidder_master_db
from .adapters import adapter_for_url
from .config import ASSET_EXTENSIONS, DEFAULT_TIMEOUT_SECONDS, DEFAULT_USER_AGENT, MAX_BODY_BYTES
from .security import PublicTransport
from .normalizer import entity_key, record_hash

_HOST_GATES = weakref.WeakKeyDictionary()

TRACKING_PARAMS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid", "fbclid", "mc_cid", "mc_eid"}
CHALLENGE = re.compile(r"captcha|verify (?:that )?you are human|checking your browser|access denied|cf-chl-|challenge-platform", re.I)


def canonicalize_url(url: str) -> str:
    if not url:
        return ""
    try:
        if "\\" in url or any(ord(c) < 32 for c in url):
            return ""
        parts = urlsplit(url.strip())
        if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username is not None or parts.password is not None:
            return ""
        host = parts.hostname.rstrip(".").encode("idna").decode().lower()
        if "%" in host:
            return ""
        host = f"[{host}]" if ":" in host else host
        port = parts.port
        if port and port != (443 if parts.scheme == "https" else 80):
            host += f":{port}"
        query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
        query.sort(key=lambda pair: pair[0])  # Preserve order of repeated values.
        path = re.sub(r"/{2,}", "/", parts.path or "/")
        path = re.sub(r"%([0-9a-fA-F]{2})",
            lambda m: chr(int(m[1], 16)) if chr(int(m[1], 16)) in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~" else m[0].upper(), path)
        segments = []
        for segment in path.split("/"):
            if segment == "..":
                if segments:
                    segments.pop()
            elif segment and segment != ".":
                segments.append(segment)
        trailing = path.endswith(("/", "/.", "/.."))
        path = "/" + "/".join(segments)
        if trailing and path != "/":
            path += "/"
        return urlunsplit((parts.scheme, host, path, urlencode(query), ""))
    except (ValueError, UnicodeError):
        return ""


@dataclass
class FetchResult:
    url: str
    status: int
    text: str
    headers: dict[str, str]
    rendered: bool = False
    not_modified: bool = False
    body: bytes = b""


class BrowserRenderer:
    """No direct browser egress: route all HTTP through the guarded client."""
    def __init__(self, engine):
        self.engine = engine
        self.playwright = self.browser = self.context = None
        self.direct_context = self.direct_page = None
        self.direct_primed = False
        self.lock = asyncio.Lock()

    async def fetch(self, client, url):
        async with self.lock:
            if self.context is None:
                from playwright.async_api import async_playwright
                self.playwright = await async_playwright().start()
                self.browser = await self.playwright.chromium.launch(headless=True)
                self.context = await self.browser.new_context(user_agent=DEFAULT_USER_AGENT, service_workers="block")
                await self.context.route_web_socket("**/*", lambda ws: ws.close())
            page = await self.context.new_page()
            failures = []

            async def route_request(route):
                request = route.request
                try:
                    if request.resource_type in {"image", "media", "font"}:
                        await route.abort()
                        return
                    if request.method != "GET":
                        raise ValueError("Browser non-GET request blocked")
                    self.engine._check_request(request.url)
                    for cookie in await self.context.cookies(request.url):
                        client.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"], path=cookie["path"])
                    result = await self.engine._http_fetch_with_retry(client, request.url, {}, redirects=False)
                    if result.status >= 400 or CHALLENGE.search(result.text):
                        raise ValueError(f"Browser resource denied: HTTP {result.status}")
                    headers = {k: v for k, v in result.headers.items() if k not in {"content-encoding", "content-length", "transfer-encoding"}}
                    if "content-type" in headers:
                        headers["content-type"] = headers["content-type"].split(";")[0] + "; charset=utf-8"
                    await route.fulfill(status=result.status, headers=headers, body=result.text.encode("utf-8"))
                except Exception as exc:
                    failures.append(str(exc))
                    await route.abort()

            await page.route("**/*", route_request)
            try:
                response = await page.goto(url, wait_until="networkidle", timeout=30000)
                if failures:
                    raise ValueError("Rendering incomplete: " + failures[0])
                html = await page.content()
                if len(html.encode()) > MAX_BODY_BYTES:
                    raise ValueError("Rendered page exceeds body limit")
                return FetchResult(page.url, response.status if response else 200, html, {}, rendered=True)
            finally:
                await page.close()

    async def fetch_direct(self, url):
        """Use Chromium's own network stack for a narrowly-scoped source adapter.

        This is intentionally separate from the generic guarded browser renderer.
        It is enabled only by adapters that explicitly opt in and define the exact
        public document URLs Chromium may navigate to.
        """
        async with self.lock:
            if self.playwright is None:
                from playwright.async_api import async_playwright
                self.playwright = await async_playwright().start()
                visible = bool(getattr(self.engine.adapter, "visible_browser", False))
                self.browser = await self.playwright.chromium.launch(headless=not visible)
                if visible:
                    activity.emit("INFO", "Opened visible Chromium window for source collection", source_id=self.engine.source_id, job_id=self.engine.job_id)
            if self.direct_context is None:
                self.direct_context = await self.browser.new_context(service_workers="block")
                await self.direct_context.route_web_socket("**/*", lambda ws: ws.close())
                self.direct_page = await self.direct_context.new_page()

                async def route_request(route):
                    request = route.request
                    try:
                        if request.resource_type != "document":
                            await route.abort()
                            return
                        if request.method != "GET":
                            raise ValueError("Direct browser non-GET request blocked")
                        value = canonicalize_url(request.url)
                        self.engine._check_request(value, robots=False)
                        allowed = getattr(self.engine.adapter, "browser_allowed_url", self.engine.adapter.allowed_url)
                        if not allowed(value):
                            raise ValueError("Direct browser navigation outside adapter boundary")
                        await route.continue_()
                    except Exception:
                        await route.abort()

                await self.direct_page.route("**/*", route_request)

            prime_url = getattr(self.engine.adapter, "browser_prime_url", None)
            if prime_url and not self.direct_primed:
                await self.engine._throttle()
                prime_response = await self.direct_page.goto(
                    prime_url, wait_until="domcontentloaded", timeout=60000
                )
                if not prime_response:
                    raise ValueError("OSHA browser session could not open the source landing page")
                prime_status = prime_response.status
                if prime_status >= 400:
                    raise ValueError(f"OSHA browser session landing page HTTP {prime_status}")
                self.direct_primed = True
                activity.emit(
                    "INFO", "Browser session established for source-specific collection",
                    source_id=self.engine.source_id, job_id=self.engine.job_id, url=prime_url,
                )

            await self.engine._throttle()
            response = await self.direct_page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=60000,
                referer=prime_url if prime_url and url != prime_url else None,
            )
            if not response:
                raise ValueError("Browser navigation returned no response")
            html = await self.direct_page.content()
            if len(html.encode()) > MAX_BODY_BYTES:
                raise ValueError("Rendered page exceeds body limit")
            headers = await response.all_headers()
            return FetchResult(
                self.direct_page.url,
                response.status,
                html,
                headers,
                rendered=True,
                body=html.encode("utf-8", errors="replace"),
            )

    async def close(self):
        if self.direct_page:
            await self.direct_page.close()
        if self.direct_context:
            await self.direct_context.close()
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()


class CrawlEngine:
    def __init__(self, source: dict, job_id: int, force_full: bool = False, *, transport=None):
        self.source, self.job_id, self.force_full = source, job_id, force_full
        self.source_id = int(source["id"])
        self.start_url = canonicalize_url(source["start_url"])
        self.start_host = urlsplit(self.start_url).hostname
        self.max_pages, self.max_depth = int(source["max_pages"]), int(source["max_depth"])
        self.concurrency = int(source["concurrency"])
        self.delay = int(source["delay_ms"]) / 1000
        self.render_mode = source["render_mode"]
        self.respect_robots = bool(source["respect_robots"])
        self.robot_parser = None
        self.robot_sitemaps = []
        self.robot_delay = 0
        self.seen = set()
        self.record_digests = {}
        self.processed = 0
        self.limited = False
        self.transport = transport  # Test dependency injection; never exposed by API.
        self.renderer = BrowserRenderer(self)
        self.adapter = adapter_for_url(self.start_url)
        canonical_start = getattr(self.adapter, "canonical_start_url", None)
        if canonical_start:
            self.start_url = canonicalize_url(canonical_start)
            self.start_host = urlsplit(self.start_url).hostname
        if getattr(self.adapter, "api_source", False):
            # API endpoints are not website crawls, so robots.txt does not apply.
            self.respect_robots = False
        elif getattr(self.adapter, "ignore_robots", False):
            # Legacy source-specific adapters may explicitly opt out of the
            # generic robots policy gate while retaining strict URL boundaries.
            self.respect_robots = False

    async def run(self):
        activity.emit("INFO", "Scan started", source_id=self.source_id, job_id=self.job_id, url=self.start_url)
        await db.update_job(self.job_id, status="running", started_at=db.utcnow(), message="Preparing crawl")
        try:
            async with httpx.AsyncClient(
                transport=self.transport or PublicTransport(), trust_env=False, follow_redirects=False,
                timeout=DEFAULT_TIMEOUT_SECONDS, headers={"User-Agent": DEFAULT_USER_AGENT},
            ) as client:
                if getattr(self.adapter, "api_source", False):
                    activity.emit(
                        "INFO", "Using authenticated public API source; robots.txt is not applicable",
                        source_id=self.source_id, job_id=self.job_id, url=self.start_url,
                    )
                elif getattr(self.adapter, "ignore_robots", False):
                    activity.emit(
                        "WARNING", "robots.txt policy check skipped for source-specific adapter",
                        source_id=self.source_id, job_id=self.job_id, url=self.start_url,
                    )
                else:
                    await self._load_robots(client)
                if getattr(self.adapter, "query_mode", False):
                    master_rows = await bidder_master_db.all_rows()
                    if not master_rows:
                        raise ValueError("Import the master bidder CSV before running the targeted contractor search")
                    frontier = list(self.adapter.seed_urls(master_rows))
                    if not frontier:
                        raise ValueError("The master bidder database contains no contractor names to search")
                    activity.emit(
                        "INFO", "Starting targeted contractor queries",
                        source_id=self.source_id, job_id=self.job_id,
                        contractors=len(master_rows), query_pages=len(frontier),
                    )
                else:
                    activity.emit("INFO", "Crawl policy checked; discovering sitemap links", source_id=self.source_id, job_id=self.job_id)
                    frontier = [self.start_url, *await self._discover_sitemap_urls(client)]
                # Level barriers prevent a fast deep path from hiding a shorter path.
                for depth in range(self.max_depth + 1):
                    batch = []
                    for candidate in frontier:
                        url = canonicalize_url(candidate)
                        if not self._allowed_url(url) or url in self.seen:
                            continue
                        if len(self.seen) >= self.max_pages:
                            self.limited = True
                            continue
                        self.seen.add(url)
                        batch.append(url)
                    await db.increment_job(self.job_id, pages_discovered=len(batch))
                    frontier = {}
                    for offset in range(0, len(batch), self.concurrency):
                        results = await asyncio.gather(*(self._process_safely(client, u) for u in batch[offset:offset + self.concurrency]))
                        for links in results:
                            for link in links:
                                if link in self.seen:
                                    continue
                                if len(frontier) >= self.max_pages and link not in frontier:
                                    self.limited = True
                                    continue
                                frontier[link] = None
                    if not frontier:
                        break
                    if depth == self.max_depth and any(self._allowed_url(u) and canonicalize_url(u) not in self.seen for u in frontier):
                        self.limited = True
                job = await db.get_job(self.job_id)
                complete = not self.limited and not job["errors"]
                if hasattr(self.adapter, "finalize_records"):
                    try:
                        final_records = self.adapter.finalize_records(complete=complete)
                        await self._store_final_records(final_records)
                    except Exception as exc:
                        await db.increment_job(self.job_id, errors=1)
                        activity.emit(
                            "ERROR", "Source adapter could not finalize contractor findings",
                            source_id=self.source_id, job_id=self.job_id, error=str(exc),
                        )
                job = await db.get_job(self.job_id)
                complete = not self.limited and not job["errors"]
                activity.emit("INFO" if complete else "WARNING", "Scan completed" if complete else "Scan finished with limits or errors",
                              source_id=self.source_id, job_id=self.job_id, pages=self.processed, errors=job["errors"],
                              records_new=job["records_new"], records_updated=job["records_updated"])
                if complete:
                    await db.finish_observations(self.source_id, self.job_id)
                await db.update_job(self.job_id, status="completed" if complete else "partial", finished_at=db.utcnow(),
                    message=f"{self.processed} pages processed. " + ("Crawl boundary exhausted." if complete else "Limits or errors prevented a complete scan; missing records were not marked inactive."))
        except asyncio.CancelledError:
            activity.emit("WARNING", "Scan cancelled", source_id=self.source_id, job_id=self.job_id)
            await db.update_job(self.job_id, status="cancelled", finished_at=db.utcnow(), message="Crawl cancelled")
            raise
        except Exception as exc:
            safe_error = activity.redact(str(exc))
            activity.emit("ERROR", "Scan failed", source_id=self.source_id, job_id=self.job_id, error=safe_error)
            await db.increment_job(self.job_id, errors=1)
            await db.update_job(self.job_id, status="failed", finished_at=db.utcnow(), message=safe_error[:1000])
            raise
        finally:
            await self.renderer.close()
            await db.mark_source_scanned(self.source_id)

    async def _process_safely(self, client, url):
        activity.emit("INFO", "Fetching page", source_id=self.source_id, job_id=self.job_id, url=url)
        try:
            links = await self._process_url(client, url)
            activity.emit("INFO", "Page processed", source_id=self.source_id, job_id=self.job_id, url=url, links=len(links))
            return links
        except Exception as exc:
            safe_error = activity.redact(str(exc))
            activity.emit("ERROR", "Page could not be processed", source_id=self.source_id, job_id=self.job_id, url=url, error=safe_error)
            await db.increment_job(self.job_id, errors=1)
            await db.upsert_page(self.source_id, url, last_error=safe_error[:1000])
            if getattr(self.adapter, "fail_fast_access_errors", False) and (
                "HTTP 401" in safe_error or "HTTP 403" in safe_error or "HTTP 429" in safe_error
                or "access challenge" in safe_error.lower()
            ):
                raise
            return []
        finally:
            self.processed += 1
            await db.increment_job(self.job_id, pages_processed=1)

    async def _process_url(self, client, url):
        cached = await db.get_page(self.source_id, url)
        result = await self._fetch(client, url, cached)
        if result.status == 304:
            if not cached or not cached.get("content_hash"):
                raise ValueError("304 without a usable cached page")
            await self._touch_cached(url)
            await db.upsert_page(self.source_id, url, last_error=None)
            return json.loads(cached.get("discovered_links") or "[]")
        if result.status >= 400:
            raise ValueError(f"HTTP {result.status}")
        if CHALLENGE.search(result.text):
            raise ValueError("Explicit access challenge detected; no rendering attempted")
        digest = hashlib.sha256(result.text.encode()).hexdigest()
        if cached and cached.get("content_hash") == digest and not self.force_full and not getattr(self.adapter, "always_parse", False):
            await self._touch_cached(url)
            links = json.loads(cached.get("discovered_links") or "[]")
        else:
            # Invalidate before persisting records. A crash cannot reuse an old
            # page cache with newly replaced record associations.
            await db.upsert_page(self.source_id, url, content_hash=None, etag=None, last_modified=None)
            links = list(dict.fromkeys(canonicalize_url(u) for u in self.adapter.links(result.text, result.url)))
            links = [u for u in links if self._allowed_url(u)]
            records = self.adapter.extract(result.text, result.url)
            await db.increment_job(self.job_id, records_found=len(records))
            for record in records:
                self._register_record(entity_key(record), record_hash(record))
                outcome = await db.upsert_record(self.source_id, record)
                if outcome in {"new", "updated"}:
                    await db.increment_job(self.job_id, **{f"records_{outcome}": 1})
            await db.observe_page_records(self.source_id, url, self.job_id, records)
        await db.upsert_page(self.source_id, url, status_code=result.status,
            etag=None if result.rendered else result.headers.get("etag"),
            last_modified=None if result.rendered else result.headers.get("last-modified"),
            content_hash=digest, discovered_links=json.dumps(links), last_error=None, rendered=int(result.rendered), fetch_mode=self.render_mode)
        return links

    async def _store_final_records(self, records):
        if not records:
            return
        await db.increment_job(self.job_id, records_found=len(records))
        for record in records:
            self._register_record(entity_key(record), record_hash(record))
            outcome = await db.upsert_record(self.source_id, record)
            if outcome in {"new", "updated"}:
                await db.increment_job(self.job_id, **{f"records_{outcome}": 1})
            # Query-oriented adapters produce one contractor-level aggregate.
            # Associate it with its real source query URL so complete scans can
            # still drive active/inactive observations safely.
            await db.observe_page_records(
                self.source_id, record.get("source_url") or self.start_url,
                self.job_id, [record],
            )

    def _register_record(self, key, value):
        if key in self.record_digests and self.record_digests[key] != value:
            raise ValueError("Conflicting representations of the same record; a site adapter must select the authoritative detail")
        self.record_digests[key] = value

    async def _touch_cached(self, url):
        observations = await db.touch_page_records(self.source_id, url, self.job_id)
        for observation in observations:
            self._register_record(observation["entity_key"], observation["payload_hash"])
        await db.increment_job(self.job_id, records_found=len(observations))

    async def _fetch(self, client, url, cached):
        if getattr(self.adapter, "direct_browser", False) and self.transport is None:
            activity.emit(
                "INFO", "Fetching source page in Chromium session",
                source_id=self.source_id, job_id=self.job_id, url=url,
            )
            return await self.renderer.fetch_direct(url)

        headers = {}
        request_headers = getattr(self.adapter, "request_headers", None)
        if request_headers:
            headers.update(request_headers(url))
        if cached and cached.get("fetch_mode") == self.render_mode and not self.force_full and not cached.get("rendered") and self.render_mode != "browser" and not getattr(self.adapter, "always_parse", False):
            if cached.get("etag"):
                headers["If-None-Match"] = cached["etag"]
            if cached.get("last_modified"):
                headers["If-Modified-Since"] = cached["last_modified"]
        result = await self._http_fetch_with_retry(client, url, headers)
        if result.status == 304:
            result.not_modified = True
            activity.emit("INFO", "Unchanged page; reusing saved links and sightings", job_id=self.job_id, url=url)
            return result
        if result.status == 200 and not CHALLENGE.search(result.text) and (
            self.render_mode == "browser" or self.render_mode == "auto" and self._looks_dynamic(result.text)
        ):
            activity.emit("INFO", "Rendering JavaScript page", job_id=self.job_id, url=result.url)
            return await self.renderer.fetch(client, result.url)
        return result

    def _looks_dynamic(self, html):
        soup = BeautifulSoup(html, "lxml")
        executable = any(t.get("src") or t.get("type", "").lower() in {"", "module", "text/javascript", "application/javascript"} for t in soup.find_all("script"))
        for tag in soup(["script", "style"]):
            tag.extract()
        return len(soup.get_text(" ", strip=True)) < 250 and (executable or bool(re.search(r"enable javascript|javascript is required", html, re.I)))

    def _allowed_url(self, url):
        url = canonicalize_url(url)
        if not url:
            return False
        parts = urlsplit(url)
        return parts.hostname == self.start_host and parts.port in {None, 80, 443} and not any(parts.path.lower().endswith(ext) for ext in ASSET_EXTENSIONS) and self.adapter.allowed_url(url)

    def _check_request(self, url, robots=True):
        value = canonicalize_url(url)
        if not value or urlsplit(value).hostname != self.start_host or urlsplit(value).port not in {None, 80, 443}:
            raise ValueError("Request outside source hostname/port boundary")
        if robots and self.respect_robots and self.robot_parser and not self.robot_parser.can_fetch(DEFAULT_USER_AGENT, value):
            raise ValueError("Blocked by robots.txt policy")

    async def _http_fetch_with_retry(self, client, url, headers, *, robots=True, redirects=True):
        for hop in range(6):
            self._check_request(url, robots)
            for attempt in range(4):
                await self._throttle()
                try:
                    request_url = url
                    request_builder = getattr(self.adapter, "request_url", None)
                    if request_builder:
                        request_url = request_builder(url)
                        self._check_request(request_url, robots)
                    async with asyncio.timeout(60), client.stream("GET", request_url, headers=headers) as response:
                        body = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=65536):
                            body.extend(chunk)
                            if len(body) > MAX_BODY_BYTES:
                                raise ValueError("Response exceeds body limit")
                        try:
                            kind = response.headers.get("content-type", "")
                            if "charset=" not in kind.lower() and ("html" in kind or "xml" in kind):
                                text = UnicodeDammit(bytes(body), is_html="html" in kind).unicode_markup or ""
                            else:
                                text = body.decode(response.encoding or "utf-8", errors="replace")
                        except LookupError:
                            text = body.decode("utf-8", errors="replace")
                        # Keep credential-bearing request URLs out of caches, logs, and records.
                        visible_url = url if request_builder else str(response.url)
                        result = FetchResult(visible_url, response.status_code, text, dict(response.headers), body=bytes(body))
                    if result.status not in {429, 500, 502, 503, 504} or attempt == 3:
                        break
                    wait = min(2 ** attempt, 20)
                    retry = result.headers.get("retry-after", "")
                    try:
                        wait = max(0, float(retry)) if retry.isdigit() else max(0, (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds())
                    except (ValueError, TypeError, OverflowError):
                        pass
                    if wait > 60:
                        raise ValueError("Server requested a longer retry delay; rescan later")
                    activity.emit("WARNING", "Temporary response; retrying after a delay", job_id=self.job_id, url=url, status=result.status, seconds=wait, attempt=attempt + 1)
                    await asyncio.sleep(wait)
                except (httpx.TimeoutException, httpx.NetworkError):
                    if attempt == 3:
                        raise
                    await asyncio.sleep(2 ** attempt)
            if redirects and result.status in {301, 302, 303, 307, 308}:
                location = result.headers.get("location")
                if not location:
                    raise ValueError("Redirect without Location")
                url = urljoin(url, location)
                headers = {}
                request_headers = getattr(self.adapter, "request_headers", None)
                if request_headers:
                    headers.update(request_headers(url))
                continue
            if result.status < 300 and result.status != 204:
                kind = result.headers.get("content-type", "").lower()
                if kind and not any(x in kind for x in ("text/", "html", "xml", "json", "javascript")):
                    raise ValueError("Unsupported response content type")
            return result
        raise ValueError("Too many redirects")

    async def _throttle(self):
        gates = _HOST_GATES.setdefault(asyncio.get_running_loop(), {})
        gate = gates.setdefault(self.start_host, {"lock": asyncio.Lock(), "last": 0.0, "delay": 0.0})
        async with gate["lock"]:
            delay = gate["delay"] = max(self.delay, self.robot_delay, gate["delay"])
            elapsed = time.monotonic() - gate["last"]
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            gate["last"] = time.monotonic()

    async def _load_robots(self, client):
        result = await self._http_fetch_with_retry(client, urljoin(self.start_url, "/robots.txt"), {}, robots=False)
        if result.status in {404, 410}:
            return
        if result.status != 200 or CHALLENGE.search(result.text):
            raise ValueError(f"Cannot establish robots.txt policy: HTTP {result.status}")
        parser = RobotFileParser()
        parser.parse(result.text.splitlines())
        self.robot_parser = parser
        self.robot_delay = float(parser.crawl_delay(DEFAULT_USER_AGENT) or parser.crawl_delay("*") or 0)
        rate = parser.request_rate(DEFAULT_USER_AGENT) or parser.request_rate("*")
        if rate and rate.requests:
            self.robot_delay = max(self.robot_delay, rate.seconds / rate.requests)
        self.robot_sitemaps = parser.site_maps() or []

    async def _discover_sitemap_urls(self, client):
        discovered, visited = [], set()
        for url in dict.fromkeys([*self.robot_sitemaps, urljoin(self.start_url, "/sitemap.xml")]):
            await self._parse_sitemap(client, url, discovered, visited, 0)
        return discovered

    async def _parse_sitemap(self, client, url, discovered, visited, depth):
        url = canonicalize_url(url)
        if not url or url in visited:
            return
        if depth > 3 or len(visited) >= 50 or len(discovered) >= self.max_pages:
            self.limited = True
            return
        visited.add(url)
        try:
            result = await self._http_fetch_with_retry(client, url, {})
            if result.status in {404, 410}:
                return
            if result.status != 200:
                raise ValueError(f"Sitemap HTTP {result.status}")
            parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False)
            root = etree.fromstring(result.body, parser)
            kind = etree.QName(root).localname
            if kind not in {"sitemapindex", "urlset"}:
                raise ValueError("Unrecognized sitemap root")
            child = "sitemap" if kind == "sitemapindex" else "url"
            for loc in root.xpath(f"./*[local-name()='{child}']/*[local-name()='loc']"):
                candidate = canonicalize_url(urljoin(result.url, (loc.text or "").strip()))
                if kind == "sitemapindex":
                    await self._parse_sitemap(client, candidate, discovered, visited, depth + 1)
                elif self._allowed_url(candidate) and candidate not in discovered:
                    if len(discovered) >= self.max_pages:
                        self.limited = True
                        break
                    discovered.append(candidate)
        except Exception as exc:
            await db.increment_job(self.job_id, errors=1)
            await db.upsert_page(self.source_id, url, last_error=f"Sitemap: {exc}"[:1000])
            activity.emit("ERROR", "Sitemap could not be read", job_id=self.job_id, source_id=self.source_id, url=url, error=str(exc))
