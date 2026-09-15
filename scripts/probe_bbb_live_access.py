import asyncio
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from playwright.async_api import async_playwright

from app.bbb_adapter import _complaint_summary
from app.bbb_sitemap_adapter import _xml_locs
from app.config import DEFAULT_USER_AGENT
from app.crawler import CHALLENGE
from app.security import PublicTransport


SITEMAP = "https://www.bbb.org/sitemap-business-profiles-327.xml"


def challenged(status: int, text: str) -> bool:
    return status in {401, 403, 429} or bool(CHALLENGE.search(text[:500000]))


async def main() -> None:
    lines = []

    def note(name, value):
        lines.append(f"{name} {value}")

    profile_url = ""
    async with httpx.AsyncClient(
        transport=PublicTransport(),
        trust_env=False,
        follow_redirects=False,
        timeout=30.0,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "application/xml,text/xml;q=0.9,*/*;q=0.1"},
    ) as client:
        try:
            sitemap = await client.get(SITEMAP)
            note("SITEMAP_STATUS", sitemap.status_code)
            note("SITEMAP_CHALLENGE", challenged(sitemap.status_code, sitemap.text))
            profile_urls = [
                url for url in _xml_locs(sitemap.text)
                if urlsplit(url).path.startswith("/us/wi/") and "/profile/" in urlsplit(url).path
            ]
            note("PROFILE_DISCOVERED", bool(profile_urls))
            if profile_urls:
                profile_url = profile_urls[0]
        except Exception as exc:
            note("SITEMAP_EXCEPTION_TYPE", type(exc).__name__)

    if profile_url:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(service_workers="block")
                page = await context.new_page()

                async def route_request(route):
                    request = route.request
                    if request.resource_type != "document" or request.method != "GET":
                        await route.abort()
                    else:
                        await route.continue_()

                await page.route("**/*", route_request)

                response = await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
                profile_status = response.status if response else 0
                profile_html = await page.content()
                note("PROFILE_STATUS", profile_status)
                note("PROFILE_CHALLENGE", challenged(profile_status, profile_html))
                note("PROFILE_HAS_STRUCTURED_IDENTITY", "application/ld+json" in profile_html.lower())

                complaints_url = profile_url.rstrip("/") + "/complaints"
                response = await page.goto(complaints_url, wait_until="domcontentloaded", timeout=60000)
                complaints_status = response.status if response else 0
                complaints_html = await page.content()
                note("COMPLAINTS_STATUS", complaints_status)
                note("COMPLAINTS_CHALLENGE", challenged(complaints_status, complaints_html))
                summary = _complaint_summary(complaints_html, complaints_url)
                note("COMPLAINT_SUMMARY_PARSED", bool(summary.get("summary_parsed")))
                note("COMPLAINT_COUNT_PRESENT", summary.get("total_complaints_3y") is not None)
                await context.close()
                await browser.close()
        except Exception as exc:
            note("BROWSER_EXCEPTION_TYPE", type(exc).__name__)

    Path(".probe").mkdir(exist_ok=True)
    Path(".probe/bbb_live_access_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
