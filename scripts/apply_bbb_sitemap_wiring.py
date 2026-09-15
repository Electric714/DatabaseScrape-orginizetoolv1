from pathlib import Path
import re


main = Path("app/main.py")
text = main.read_text(encoding="utf-8")
text = text.replace(
    "from .bbb_adapter import BBB_SEARCH_ENDPOINT",
    "from .bbb_sitemap_adapter import BBB_SITEMAP_INDEX",
)
text = text.replace('"start_url": BBB_SEARCH_ENDPOINT,', '"start_url": BBB_SITEMAP_INDEX,')
old = '''        "concurrency": 1,
        "delay_ms": 1500,
        "render_mode": "auto",
        "respect_robots": True,
'''
new = '''        "concurrency": 1,
        "delay_ms": 500,
        "render_mode": "http",
        "respect_robots": True,
'''
if old not in text:
    raise SystemExit("BBB source settings block not found")
text = text.replace(old, new, 1)
if "BBB_SEARCH_ENDPOINT" in text:
    raise SystemExit("legacy BBB search endpoint still referenced by main.py")
main.write_text(text, encoding="utf-8")

readme = Path("README.md")
body = readme.read_text(encoding="utf-8")
replacement = '''## BBB Business Profiles / Complaints — Source 3

BBB is a built-in targeted collector, but it no longer uses BBB's interactive `/search?...` route. Live Windows diagnostics showed that route could receive an access challenge before any business profile was parsed, and BBB's current `robots.txt` disallows generic query-string crawling while publishing a dedicated business-profile sitemap index.

The current acquisition path is:

`master bidder → BBB-published business-profile sitemap index → relevant state sitemap blocks → plausible profile URL slug/state → exact profile identity + location verification → matched /complaints page → aggregate complaint evidence`

The published sitemap index is `https://www.bbb.org/sitemap-business-profiles-index.xml`. A one-time live structure probe on 2026-09-15 confirmed 575 child business-profile sitemaps with HTTP range support and strong geographic clustering. The POC maps the six states represented in the supplied bidder database (FL, IL, MN, MO, OH, WI) to their verified sitemap clusters and includes one neighboring sitemap on each boundary. No `/search` URL is generated or allowed by the BBB adapter.

Sitemap URLs are only discovery hints. A URL slug is never enough to update the database: the collector opens a plausible profile and requires exact normalized business identity plus location corroboration from the actual profile. Only then does it read the direct `/complaints` page.

BBB is authoritative for exactly one master field:

- `better_business_bureau_complaints`

The field semantics are:

- `Y` — an exact company/location BBB profile reports one or more complaints in BBB's rolling three-year complaint summary.
- `N` — an exact matched BBB profile was successfully reached and reports zero complaints in the three-year summary.
- blank — no exact profile was found, the selected state is not mapped by this POC, identity is ambiguous, the complaint summary could not be parsed reliably, or any sitemap/profile/complaint request was blocked or incomplete.

A missing sitemap match is deliberately **not** treated as zero complaints. A 401/403/429 or explicit access challenge also leaves the result unknown. Consumer complaint narratives are not retained; only the summary counts and limited date/type/status metadata needed for review are kept.

'''
pattern = re.compile(
    r"## BBB Business Profiles / Complaints — Source 3\n.*?(?=## Query-focused source integrations)",
    re.S,
)
body, count = pattern.subn(replacement, body, count=1)
if count != 1:
    raise SystemExit("README BBB section not found exactly once")
readme.write_text(body, encoding="utf-8")
