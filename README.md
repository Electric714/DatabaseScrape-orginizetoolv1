# Database Scrape & Organize Tool

## Audited foundation (September 2026)

Read [AUDIT.md](AUDIT.md) for the engineering findings, verification, and remaining limits.
This is a **single-process, local proof of concept**, with a tested bounded crawler.
It is not a universal exhaustive extractor or a production law-office deployment.

- Only the source hostname and public IP destinations on ports 80/443 are accepted.
  DNS is pinned to the actual HTTP connection; redirects and browser resources use
  the same boundary. Proxy environment settings are ignored.
- Crawls use breadth-first depth barriers and a bounded frontier. Start URLs and
  sitemap entries are depth-zero seeds. Page limits count selected HTML pages;
  robots/sitemap requests and retries are additional, bounded network work.
- A `partial` job means limits, denied pages, conflicts, parsing failures, or
  rendering errors prevented completion. Inspect `/api/sources/{id}/errors`
  for the latest retained page errors. `completed` means the configured traversal
  finished without reported limits/errors, not that every entity on a site exists
  in the database.
- Cached pages refresh sightings and replay known links. Rendered DOMs do not use
  HTTP-shell validators. Changing rendering mode invalidates validator reuse.
- Records missing from a complete traversal are marked inactive and timestamped.
  Partial/failed scans cannot declare records missing. Inactive means absent from
  this traversal, not proof that a person or business no longer exists.
- Name-only identities are scoped to the profile URL. Stable site IDs should be
  supplied in `external_id`. Conflicting same-scan representations are reported
  instead of silently accepting whichever concurrent fetch finishes last.
- SQLite work runs on a dedicated thread; history updates are transactional.
  One process may serve a database. Do not use multiple Uvicorn workers.
- Browser rendering supports GET-based same-host pages and cookies within a scan.
  Images/fonts/media, service workers, WebSockets, non-GET requests and external
  resources are restricted. Some real applications will need explicit adapter
  work. Rendering errors are surfaced.
- CSV/XLSX text is formula-neutralized. Exports include structured extra data and
  inactive state. More than 250,000 matches returns an explicit error; narrow the
  filter. The GUI shows up to 250 results; the API supports offset pagination.

Before upgrading an existing installation, stop the app and back up the SQLite
database (including any WAL state using SQLite backup facilities). Startup migrates
identity keys while retaining row IDs/history and invalidates old page caches.
Run a full scan afterward. People already merged by the old heuristic cannot be
reconstructed automatically.

Keep the default loopback binding. There is no user authentication, role management,
database encryption, or access audit trail. A shared deployment requires those
controls plus TLS, retention rules, backups, and operational monitoring.

### Verification

```bash
pip install -r requirements.txt
python -m playwright install chromium
pytest -q
# PowerShell, to include actual browser + local-server GUI tests:
$env:RUN_BROWSER_TESTS = "1"
pytest -q
# Linux/macOS:
RUN_BROWSER_TESTS=1 pytest -q
```

Fixtures use an injected HTTP transport and a temporary local GUI server. They
never disable the production public-IP policy through an API option. GitHub Actions
runs the browser tests and a dependency advisory audit.

A web-based crawler, organizer, change monitor, search tool, and exporter for **permitted public data sources**.

The browser is only the GUI. Crawling, JavaScript rendering, parsing, normalization, persistence, scheduling, change detection, search, and exports run on the Python backend.

## Current v0.1 foundation

- Multiple configurable website sources
- Recursive same-host crawling with URL canonicalization and deduplication
- `robots.txt` support and sitemap discovery
- Async HTTP fetching with bounded concurrency
- Retry/backoff for transient failures and `Retry-After` handling
- ETag / Last-Modified conditional requests on later scans
- Page content hashing to skip unchanged extraction work
- Playwright browser-rendering fallback for successful JavaScript shell pages
- No CAPTCHA solving or explicit anti-bot/access-control bypassing
- JSON-LD extraction for Person / Organization / LocalBusiness style records
- HTML table, definition-list, directory-card, phone, address, and date extraction
- Site-adapter layer for deterministic per-site scraping once the real sources are known
- SQLite database in WAL mode
- Record deduplication and first-seen / last-seen / last-changed timestamps
- Full record-change history
- Manual scans, force-full scans, and recurring scheduled scans
- Live job statistics in the browser
- Database search
- CSV, Excel (.xlsx), and JSON export

## Architecture

```text
Browser GUI
    ↓
FastAPI
    ↓
Crawl job manager + scheduler
    ↓
Async HTTP crawler ─────→ Playwright rendering fallback
    ↓
Source adapter
    ↓
Extraction + normalization + fingerprinting
    ↓
SQLite + record history
    ↓
Search + CSV/XLSX/JSON exports
```

The generic extractor gives us a broad starting point. The production-quality path is to add one adapter for each of the six real websites. Each adapter can define that site's exact pagination, directory/detail-page selectors, stable record IDs, field mappings, and any source-specific update behavior while reusing the crawler, scheduler, database, history, search, and export layers.

## Run locally

Python 3.11+ is recommended.

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
python -m playwright install chromium
python run.py
```

Open `http://127.0.0.1:8000`.

## Crawl strategy

The initial crawl discovers pages from the start URL, normal links, and sitemaps. It remains on the configured hostname, ignores obvious asset files, removes common tracking parameters, and stops at the configured page/depth limits.

Later scans reuse HTTP validators and stored content hashes. A `304 Not Modified` or identical content hash lets the engine reuse previously discovered links without re-extracting the page. Changed pages are extracted again. If an existing entity's structured data changes, the previous representation is preserved in `record_history` before the current record is updated.

`Force full` ignores the cache validators and forces reprocessing.

## Rendering

`Auto` mode uses ordinary HTTP first because it is dramatically faster for large directories. If a successful response looks like an empty JavaScript application shell, Playwright can render the page using headless Chromium. An explicit denial/challenge is logged rather than bypassed.

## Database

SQLite is appropriate for the demonstration and modest production loads. The schema separates:

- sources
- crawl jobs
- crawled pages and cache metadata
- current normalized records
- record-change history

If the law firm eventually needs multiple crawler workers or substantially higher concurrent database traffic, the persistence layer can be migrated to PostgreSQL while retaining the same source-adapter model.

## Site adapters

Site-specific adapters are selected in `app/adapters.py`. Once the six target sites are available, the next phase is to inspect each site's real structure and add deterministic extractors. That will be much more reliable than trying to make one heuristic parser pretend every website on Earth has the same HTML.

## Responsible operation

Use the software only for sources the operator is authorized to access and automate. Configure crawl rates appropriately for each source and comply with applicable terms, privacy obligations, data-retention requirements, and law-firm policies.
