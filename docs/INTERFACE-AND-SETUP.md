# Interface and setup

## Paralegal Database Tool workflow
The tool now puts the approved master bidder database first. The existing 30-column CSV can be imported, while collected website records remain separate source evidence until a paralegal approves a proposed change. Six website positions are reserved until the actual source names and URLs are supplied. The generic collector can traverse a configured site, but the six production adapters do not yet submit contractor/company queries to each site's real search form or public endpoint.

The master view searches the firm's bidder fields and exports the approved 30-column database. The comparison workspace shows current values and newly collected values side by side and requires explicit approval before changing the master. Source evidence, provenance links, timestamps, and collection history remain separate from the flattened bidder export. Blank or missing source data remains unknown and never clears a populated master field automatically.

The frontend uses only local assets: a mist background, teal navigation, rounded panels, an orbital illustration, and a dark activity console. It adapts to narrow screens and supports reduced motion and keyboard focus. Source removal requires confirmation; records are paginated in batches of 50.

## Logging
SQLite retains the latest 5,000 events. The frontend polls a cursor endpoint every 2.5 seconds and retains 1,000 events. Pausing stops polling; resuming catches up within retained history. Filters affect the visible feed, not exported history.

Events cover startup, failed requests, queued scans, page processing, retries, cache hits, rendering, completion, cancellation, and exceptions. Startup failures before the server runs appear in logs/setup.log.

Capture snapshot stores workspace counts. Export report creates a ZIP containing summary.json, activity.json, activity.txt, and READ-ME.txt. It omits collected records and environment variables. Common secrets are redacted before persistence, but redaction is best effort. Review reports before sharing. When disconnected, export falls back to the browser event buffer as JSON.

## Setup
Windows downloads pinned uv 0.12.10 from its official GitHub release and validates the published SHA-256 checksum. It installs managed Python 3.12, requirements, and Chromium under .runtime. No global Python or packages are modified. A requirements fingerprint and import/browser smoke check gate reuse. Repair rebuilds only the private virtual environment. A setup lock prevents concurrent installation. Setup logs rotate after 2 MB.

The runner binds only 127.0.0.1, checks ports 8000 through 8009, recognizes an existing instance for the same database, and opens the browser when ready.

## Verification
Tests cover cursor persistence, retention, redaction, exception capture, desktop interactions, mobile overflow, filtering, pause/resume, snapshots, ZIP export, and disconnected JSON export. Existing crawler/security checks remain enabled. Windows setup is tested from a directory containing spaces, including reuse and repair while preserving data. macOS/Linux helpers and native ARM64 require platform-specific smoke testing.

