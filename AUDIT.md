# Technical audit — DatabaseScrape-orginizetoolv1

Audit date: September 5, 2026. Baseline commit: `d43b99a64aac2cac0d1d8d9adbab8d716e149f8a`.

## Verdict

The original architecture was a reasonable proof-of-concept outline, but its generic
crawler was not reliable enough to treat as an exhaustive collection system.
The revised implementation is a tested, bounded foundation for the six adapters.
It deliberately reports incomplete work. It cannot guarantee complete extraction
from an arbitrary site or replace site-specific entity identity and validation.

All 19 baseline files were fetched and read through the GitHub connector, including
the crawler, persistence, extraction, normalization, API, GUI, configuration,
workflow, and tests. A local checkout of that exact commit was used for execution.
The implementation was evaluated independently of README claims.

## What was already good

- Clear separation of API, crawler, database, extraction, normalization, and adapters.
- Actual recursive link discovery, sitemap discovery, conditional requests and hashing.
- Parameterized user values in SQL and unique source/entity constraints.
- SQLite WAL, foreign keys, and record plus history writes on the same connection.
- Existing JSON-LD, table, definition-list and card extraction.
- Basic escaped HTML rendering, real XLSX generation, and all three export endpoints.
- A scheduler, restart marking, default loopback server binding, and an existing CI workflow.
- The five original tests passed unchanged before development.

The original queue's `join()` was not itself a premature-stop bug. Nor was a
max-pages overrun reproduced: the original enqueue-side seen-set cap bounded
admissions. The important queue weakness was depth: a fast deep route could claim
a URL before a slower, shorter route and prevent discovery of its descendants.

## Findings and changes

| Area | Original finding | Change |
| --- | --- | --- |
| Crawl depth | First discovery won regardless of shortest depth | Breadth-first level barriers with bounded concurrent batches |
| Completion | Limits/errors could still end with “completed” | Explicit partial result; no inactive inference from partial scans |
| Resource limits | Whole response buffered before truncation; sitemap recursion could fan out | Streamed body cap, request deadline, bounded sitemap count/depth and crawl frontier |
| URL identity | Default ports, dot segments and equivalent escapes not normalized; meaningful ref parameters dropped | Default-port, IDNA, fragment, unreserved escape and dot-segment normalization; retain meaningful query parameters and repeated-value order |
| SSRF | Arbitrary sources and automatic redirects could hit internal networks | Public DNS/IP checks at source creation and every HTTP connection; validated numeric IP used for connection with original Host/TLS name |
| Redirects | Followed automatically before scope checks | Manual bounded redirect handling; destination checked before request |
| Robots | Errors/denials could fail open; direct robots fetch bypassed normal request handling | Guarded/throttled fetch, explicit handling of missing files, fail closed on other failures, crawl delay/request rate |
| Host throttling | Independent engines could exceed an aggregate host rate | Shared host gate within the application's event loop |
| Sitemaps | Broad descendant loc selector, default XML parser, repeated entries | Direct sitemap/url loc children, secure XML parser, raw XML encoding, relative locations and deduplication |
| Conditional scans | Cached pages did not refresh last_seen | Page-to-record observations replayed on 304/identical content |
| Rendered caching | HTTP shell validators could hide changed JavaScript data | Rendered DOMs never reuse shell validators; rendering-mode changes prevent validator reuse |
| Entity identity | Name/company alone merged different profiles | Scope fallback identities to source URL; preserve stable external IDs |
| Conflicting copies | Competing list/detail data could cause unstable overwrites | Conflicting same-scan payloads are reported as errors requiring authoritative adapter rules |
| Record hash | Raw text/provenance could create noisy updates | Exclude source URL and diagnostic extraction metadata from the structured content hash |
| Missing records | Active column was never used for disappearance | Mark unobserved records inactive only after complete traversal; record inactive_since; reactivate when observed |
| SQLite responsiveness | Synchronous connections and busy waits inside async functions | Dedicated single database thread and explicit write transaction for record read/update/history |
| Restart/scheduling | Multiple instances could compete; failures could reschedule every 30 seconds | Cross-platform process lock; launch serialization; failed attempts advance schedule; logged scheduler failures |
| Browser lifecycle | Per-page contexts, startup races, swallowed fallback errors | Serialized startup, scan-local context/cookies, visible rendering errors |
| Browser egress | Browser requests bypassed HTTP scope/security | Fulfill browser requests through guarded HTTP; block service workers, WebSockets, non-GET and out-of-bound resources |
| Extraction | Whole-page title plus footer phone generated false records; JSON-LD irregularities | Disable page-wide fallback; strengthen type/identifier handling and deduplication; preserve structured address/name extras |
| Field parsing | Phone regex matched inside long digit IDs; no ISO date regex | Digit boundaries and ISO date recognition |
| Link discovery | Base href and rel=next missing | Support both, then enforce crawler scope |
| Frontend security | Escaping did not neutralize javascript: links | Validate link scheme; strict CSP and delegated button handlers |
| GUI behavior | Polling reset source selection; concurrent refreshes raced; errors could disappear | Preserve selection; serialized polling; error text; displayed scan message |
| Exports | Spreadsheet formula injection, control-character failures, silent 250k truncation | Neutralize formula text, clean illegal controls, explicit limit error, extras/inactive state, write-only XLSX built off event loop |
| Search | No record-ID search; wildcard input not literal | External-ID search, escaped LIKE input, changed-order index |
| API boundary | No Host/Origin hardening | Local trusted hosts, cross-origin mutation rejection, no-store/nosniff/referrer/CSP headers |
| Dependencies | Known advisories in original resolved package set | Update FastAPI/Starlette, lxml, pytest/pytest-asyncio, and Playwright/Chromium |

The initial dependency audit returned 11 advisory entries across three packages,
including duplicate advisory aliases. The Windows StaticFiles issue is especially
relevant to this project's environment: see the
[upstream advisory](https://github.com/Kludex/starlette/security/advisories/GHSA-wqp7-x3pw-xc5r).
FastAPI 0.141.1, Starlette 1.6.0, lxml 6.1.0, pytest 9.0.3,
pytest-asyncio 1.4.0 and Playwright 1.62.0 are pinned after verification.
A clean advisory result is a point-in-time package check, not a security guarantee.

## Verification

- Baseline: 5 tests passed.
- Revised suite: 58 tests, including the original 5, passed locally on Windows/Python 3.12.
- Actual Chromium tests cover JavaScript-generated records, a GET data endpoint,
  session cookies, and blocking an internal-network script before transport.
- Live Uvicorn/Chromium GUI test adds a source, clicks Scan, waits for Jane's record,
  and verifies the source filter survives polling without JavaScript errors.
- FastAPI lifecycle smoke test verifies GET /, /api/health, source creation/listing,
  duplicate-source rejection, scan launch/status, record search, history behavior,
  CSV/XLSX/JSON export, invalid format, and Host/Origin/CSP controls.
- The miniature directory includes pagination, profile pages, duplicated sitemap
  locations, dates, structured addresses, phone numbers, and stable IDs.
- Scan one creates two records. Scan two updates Jane's phone, adds one record via
  changed pagination, and refreshes unchanged John's sighting through conditional
  fetching. The total becomes three, not a duplicate copy of the whole database.
- Further tests cover canonicalization, query order, same-host/port restrictions,
  DNS mixed answers and IP pinning, private IPv4/IPv6, shortest depth, high
  concurrency page bounds, conflicts, migration, restart, complete/partial
  disappearance, robots denials, XML location scope and encoding, body limits,
  shared throttling, formula-neutralized exports and concurrent launches.
- Package compatibility and dependency advisory checks were run locally.
- GitHub Actions installs Chromium, runs the full browser-enabled suite, and runs
  pip-audit. The exact published commit's result is reported with the delivery.

The fixtures inject a transport in Python tests only. No production API flag
permits localhost, private destinations, or disabled DNS checks. No random public
directory was crawled for these tests. Deprecation warnings in third-party test/
server integrations remain; they do not indicate failing checks.

## Files changed

- `app/crawler.py`: traversal, HTTP/rendering, scope, robots, sitemap and cache fixes.
- `app/security.py` (new): public-destination validation and pinned transport.
- `app/runtime.py` (new): single-instance database process lock.
- `app/database.py`: async thread boundary, identity migration, observations,
  inactive state, search behavior, errors and export limits.
- `app/normalizer.py`: conservative identity and stable payload hash.
- `app/extractor.py`: safer extraction, structured extras and links.
- `app/adapters.py`: exact-host adapter registry and URL narrowing hook.
- `app/main.py`: source validation, launch serialization, security headers,
  errors endpoint, shutdown and export changes.
- `app/static/app.js`, `app/static/index.html`: reliable polling, safe links,
  delegated events, scan messages and more accurate wording.
- `tests/conftest.py`, `tests/fixture_site.py`, `tests/test_audit.py`,
  `tests/test_browser.py` (new): isolated regression/integration/browser coverage.
- `requirements.txt`, `pyproject.toml`, `.github/workflows/tests.yml`,
  `.gitignore`: patched dependencies, test discovery, browser/advisory CI, lock exclusion.
- `README.md`, `AUDIT.md`: operation, migration, evidence and limitations.

Unchanged files were still inspected: configuration, models, app initializer,
CSS, run entrypoint and original tests.

## Remaining limits and next six-site phase

1. **Identity is inherently site-specific.** Same-name people on the same list URL
   without IDs remain ambiguous. Name changes without stable IDs can create a new
   record. Previously merged legacy identities cannot be reconstructed from the
   old current row alone. Use authoritative IDs and one canonical detail page.
2. **Generic extraction is heuristic.** Names, address boundaries, multilingual
   fields and date meanings need adapters. JSON-LD components are preserved in
   extra data; arbitrary free-text addresses are not reliably split into city/
   state/ZIP. Nested cards or incomplete list/detail copies can produce conflicts.
3. **“Complete” is a traversal outcome.** Unlinked/unlisted pages, incorrect server
   validators, hidden API data, infinite scroll, buttons and forms require adapter
   logic. Page/depth/query budgets can prevent visiting all content. A disappeared
   link does not prove deletion of the underlying public record.
4. **Rendering is intentionally restricted.** External CDNs, POST data calls,
   service workers, WebSockets, complex multi-step navigation and long-running
   applications may be incomplete. Cookies are scan-local. Chromium is serialized.
   Source-specific permitted rendering needs testing before widening any policy.
5. **Sitemaps are bounded.** Fifty maps, nested depth three, and body limits apply.
   Gzip-compressed sitemap files are not explicitly supported. External hosts are
   rejected even if a sitemap points there.
6. **Scale is modest.** Search uses literal substring LIKE, not FTS. Exports load
   up to 250k rows before formatting; the GUI only shows 250, while the API offers
   pagination. No large-production benchmark was performed. Multi-process workers
   are intentionally blocked; use a different job/storage architecture for that.
7. **Deployment remains local.** No accounts/RBAC, encryption-at-rest, tenant
   separation, retention automation or user-access audit trail. Keep loopback
   binding. Shared law-office deployment needs explicit authentication/TLS,
   protected storage, backups, recovery testing and retention decisions.
8. **Operational gaps remain.** No durable resumable frontier, automatic retry of
   interrupted jobs, or complete per-scan error archive. Startup marks interruption;
   the next crawl restarts traversal and reuses valid caches. Page error endpoint
   provides the most recent retained errors, not a full audit log.
9. **Validation scope is finite.** Tests exercised fixtures and a local server;
   they did not prove operation against any of the six as-yet-unknown websites.
   Dependencies need continuing updates, and browser/supply-chain risks are not
   exhausted by pip-audit.

For each target website: establish the permitted boundary and practical request
rate; inspect representative directory, pagination and detail pages; choose
stable namespaced IDs and the authoritative representation; add an exact-host
adapter with query/pagination rules and structured field mapping; capture lawful
fixtures for unchanged/changed/missing/denied cases; verify expected counts and
field precision; run two controlled scans; and only then enable scheduling.
Do this one site at a time. No CAPTCHA, credential, authentication, stealth, or
access-control bypass was added.
