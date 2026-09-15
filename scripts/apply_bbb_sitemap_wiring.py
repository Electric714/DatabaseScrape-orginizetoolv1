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
if old in text:
    text = text.replace(old, new, 1)
if "BBB_SEARCH_ENDPOINT" in text:
    raise SystemExit("legacy BBB search endpoint still referenced by main.py")
if '"start_url": BBB_SITEMAP_INDEX,' not in text:
    raise SystemExit("BBB sitemap start URL was not wired")
main.write_text(text, encoding="utf-8")

crawler = Path("app/crawler.py")
crawl = crawler.read_text(encoding="utf-8")
old_check = '''                        self.engine._check_request(value, robots=False)
                        allowed = getattr(self.engine.adapter, "browser_allowed_url", self.engine.adapter.allowed_url)
'''
new_check = '''                        browser_robots = bool(getattr(self.engine.adapter, "browser_respect_robots", False))
                        self.engine._check_request(value, robots=browser_robots)
                        allowed = getattr(self.engine.adapter, "browser_allowed_url", self.engine.adapter.allowed_url)
'''
if old_check in crawl:
    crawl = crawl.replace(old_check, new_check, 1)
if "browser_robots = bool(getattr(self.engine.adapter" not in crawl:
    raise SystemExit("selective direct-browser robots guard was not installed")
old_fetch = '''    async def _fetch(self, client, url, cached):
        if getattr(self.adapter, "direct_browser", False) and self.transport is None:
            activity.emit(
                "INFO", "Fetching source page in Chromium session",
'''
new_fetch = '''    async def _fetch(self, client, url, cached):
        browser_fetch_url = getattr(self.adapter, "browser_fetch_url", None)
        use_direct_browser = bool(getattr(self.adapter, "direct_browser", False)) or bool(
            browser_fetch_url and browser_fetch_url(url)
        )
        if use_direct_browser and self.transport is None:
            activity.emit(
                "INFO", "Fetching source page in Chromium session",
'''
if old_fetch in crawl:
    crawl = crawl.replace(old_fetch, new_fetch, 1)
if "browser_fetch_url = getattr(self.adapter" not in crawl:
    raise SystemExit("selective direct-browser fetch hook was not installed")
crawler.write_text(crawl, encoding="utf-8")

readme = Path("README.md")
body = readme.read_text(encoding="utf-8")
replacement = '''## BBB Business Profiles / Complaints — Source 3

BBB is a built-in targeted collector, but it no longer uses BBB's interactive `/search?...` route. Live Windows diagnostics showed that route could receive an access challenge before any business profile was parsed, and BBB's current `robots.txt` disallows generic query-string crawling while publishing a dedicated business-profile sitemap index.

The current acquisition path is:

`master bidder → BBB-published business-profile sitemap index → relevant state sitemap blocks → plausible profile URL slug/state → exact profile identity + location verification → matched /complaints page → aggregate complaint evidence`

The published sitemap index is `https://www.bbb.org/sitemap-business-profiles-index.xml`. A one-time live structure probe on 2026-09-15 confirmed 575 child business-profile sitemaps with HTTP range support and strong geographic clustering. The POC maps the six states represented in the supplied bidder database (FL, IL, MN, MO, OH, WI) to their verified sitemap clusters and includes one neighboring sitemap on each boundary. No `/search` URL is generated or allowed by the BBB adapter.

Sitemap index/child XML stays on ordinary HTTP. Sitemap URLs are only discovery hints: a URL slug is never enough to update the database. For an already-discovered profile or `/complaints` document, the local application may use Chromium's normal document navigation, while still enforcing robots policy and the adapter's exact BBB profile boundary. It does not solve CAPTCHAs or bypass an explicit challenge; a 401/403/429 or challenge remains an incomplete run.

The collector opens a plausible profile and requires exact normalized business identity plus location corroboration from the actual profile. Only then does it read the direct `/complaints` page.

BBB is authoritative for exactly one master field:

- `better_business_bureau_complaints`

The field semantics are:

- `Y` — an exact company/location BBB profile reports one or more complaints in BBB's rolling three-year complaint summary.
- `N` — an exact matched BBB profile was successfully reached and reports zero complaints in the three-year summary.
- blank — no exact profile was found, the selected state is not mapped by this POC, identity is ambiguous, the complaint summary could not be parsed reliably, or any sitemap/profile/complaint request was blocked or incomplete.

A missing sitemap match is deliberately **not** treated as zero complaints. Consumer complaint narratives are not retained; only the summary counts and limited date/type/status metadata needed for review are kept.

'''
pattern = re.compile(
    r"## BBB Business Profiles / Complaints — Source 3\n.*?(?=## Query-focused source integrations)",
    re.S,
)
body, count = pattern.subn(replacement, body, count=1)
if count != 1:
    raise SystemExit("README BBB section not found exactly once")
readme.write_text(body, encoding="utf-8")

# The legacy BBB parser unit tests remain useful for HTML parsing, but the old
# CrawlEngine integration test intentionally exercised /search. The new sitemap
# integration test supersedes that network-path test.
test_path = Path("tests/test_bbb_integration.py")
tests = test_path.read_text(encoding="utf-8")
if "from app.bbb_sitemap_adapter import BBB_SITEMAP_INDEX" not in tests:
    tests = tests.replace(
        "from app.bidder_schema import BIDDER_COLUMNS, bidder_row\n",
        "from app.bbb_sitemap_adapter import BBB_SITEMAP_INDEX\nfrom app.bidder_schema import BIDDER_COLUMNS, bidder_row\n",
    )
tests, removed = re.subn(
    r"\nasync def test_bbb_full_crawl_proposes_only_bbb_complaint_field\(database\):.*?(?=\nasync def test_builtin_bbb_source_is_created_once_reuses_legacy_and_is_locked)",
    "\n",
    tests,
    count=1,
    flags=re.S,
)
if removed not in {0, 1}:
    raise SystemExit("unexpected legacy BBB integration-test count")
tests = tests.replace('assert first["start_url"] == BBB_SEARCH_ENDPOINT', 'assert first["start_url"] == BBB_SITEMAP_INDEX')
tests = tests.replace('assert first["delay_ms"] == 1500', 'assert first["delay_ms"] == 500')
tests = tests.replace('assert first["render_mode"] == "auto"', 'assert first["render_mode"] == "http"')
marker = '''    duplicate = SourceCreate(
        name="Duplicate BBB",
        start_url=BBB_SEARCH_ENDPOINT,
'''
if marker in tests:
    tests = tests.replace(marker, '''    duplicate = SourceCreate(
        name="Duplicate BBB",
        start_url=BBB_SITEMAP_INDEX,
''', 1)
test_path.write_text(tests, encoding="utf-8")
