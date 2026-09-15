import asyncio
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from app.bbb_adapter import _complaint_summary
from app.bbb_sitemap_adapter import _xml_locs
from app.config import DEFAULT_USER_AGENT
from app.crawler import CHALLENGE
from app.security import PublicTransport


SITEMAP = "https://www.bbb.org/sitemap-business-profiles-327.xml"


def challenged(response: httpx.Response) -> bool:
    return response.status_code in {401, 403, 429} or bool(CHALLENGE.search(response.text[:500000]))


async def main() -> None:
    lines = []

    def note(name, value):
        lines.append(f"{name} {value}")

    async with httpx.AsyncClient(
        transport=PublicTransport(),
        trust_env=False,
        follow_redirects=False,
        timeout=30.0,
        headers={"User-Agent": DEFAULT_USER_AGENT, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.1"},
    ) as client:
        try:
            sitemap = await client.get(SITEMAP)
            note("SITEMAP_STATUS", sitemap.status_code)
            note("SITEMAP_CHALLENGE", challenged(sitemap))
            profile_urls = [
                url for url in _xml_locs(sitemap.text)
                if urlsplit(url).path.startswith("/us/wi/") and "/profile/" in urlsplit(url).path
            ]
            note("PROFILE_DISCOVERED", bool(profile_urls))
            if not profile_urls:
                raise RuntimeError("no profile URL discovered in mapped sitemap")

            profile_url = profile_urls[0]
            profile = await client.get(profile_url)
            note("PROFILE_STATUS", profile.status_code)
            note("PROFILE_CHALLENGE", challenged(profile))
            note("PROFILE_HAS_STRUCTURED_IDENTITY", "application/ld+json" in profile.text.lower())

            complaints_url = profile_url.rstrip("/") + "/complaints"
            complaints = await client.get(complaints_url)
            note("COMPLAINTS_STATUS", complaints.status_code)
            note("COMPLAINTS_CHALLENGE", challenged(complaints))
            summary = _complaint_summary(complaints.text, complaints_url)
            note("COMPLAINT_SUMMARY_PARSED", bool(summary.get("summary_parsed")))
            note("COMPLAINT_COUNT_PRESENT", summary.get("total_complaints_3y") is not None)
        except Exception as exc:
            note("PROBE_EXCEPTION_TYPE", type(exc).__name__)

    Path(".probe").mkdir(exist_ok=True)
    Path(".probe/bbb_live_access_summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
