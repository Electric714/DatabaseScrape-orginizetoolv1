# Database Scrape & Organize Tool

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
