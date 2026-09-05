# Paralegal Research Desk

This project is being built for a working paralegal: collect relevant public records from **six designated research websites**, keep them organized in a **local searchable database**, and quickly find business, ownership, address, and regulatory information needed for legal-document research.

The central workflow is **select the research sites → collect their records → search the saved database → inspect the original source → export the relevant results**.

## What the research records need to contain
- Business or company name.
- Person's name and business owner, kept separate when the source distinguishes them.
- Business location and full address.
- OSHA violation/status information when explicitly reported by a source, including its original wording.
- Other source-provided details such as phone, document/record identifier, and document date.
- Source website/link and collection timestamps so findings can be checked against their origin.

A missing OSHA field means **unknown or not reported**, not “no violations.” An open-violation finding must come from source evidence; a keyword match or missing record does not establish a business's current status.

## The six websites
The six website names and URLs are **pending**. The interface has six numbered positions ready for them, clearly marked as not yet configured.

The local database, search, exports, and general webpage collector are in place. Once the actual websites are supplied, each one needs its search forms, navigation, pagination, and field mappings connected and checked against real examples.

**Searching saved records is a local database search.** Refreshing a configured site runs its collector. Live name/business queries through each site's own search form require that site's integration.

The interface and record schema follow this workflow. Collection, search, and exports keep source evidence attached to each result. Current collection handles supported webpage records; PDF text extraction and OCR are not implemented.

## Start on Windows
1. Download the repository ZIP and **extract all files**.
2. Double-click **1 - START Paralegal Research Desk.cmd** in the extracted folder.
3. Leave its window open. First setup downloads a private Python environment, packages, and Chromium, then opens your browser.

No existing Python or administrator access is needed. Internet is required for setup. Everything installs inside this folder. Windows 10/11 x64 and ARM64 are supported by the launcher; x64 is tested.

Later launches reuse the environment. To stop, press **Ctrl+C** in the launcher window. Closing the browser tab alone does not stop the server.

If setup fails, run **2 - REPAIR Setup.cmd**, then start again. Repair preserves your database. Setup details are in **logs/setup.log**. If company policy blocks scripts or downloads, ask your IT administrator.

## Your workspace
- **Research websites:** six visible site positions, with Collect records and Update configured sites controls. Edit settings to adjust scheduling or limits without removing saved records.
- **Collection:** update configured sources and follow progress and errors.
- **Local research database:** find saved business/person records, filter by source, inspect provenance, and export CSV, Excel, or JSON. Search specifically by business, person, owner, address, or location; filter source-reported OSHA status. Open a record to review source wording, links, and collection dates.
- **Activity console:** follow progress, filter warnings/errors, search, pause, and jump to the latest event.

To share a problem, reproduce it, click **Capture snapshot**, then **Export report**. This downloads a diagnostic ZIP. If the server is unavailable, export downloads the visible browser logs as JSON.

Logs are saved automatically: 5,000 events in the database and up to 1,000 in the browser. Reports exclude collected records and redact common credentials and URL query values. Review before sharing: paths, URLs, and error context can still identify your environment. Nothing is automatically sent to anyone.

## Download layout
| File or folder | Purpose |
| --- | --- |
| **0 - START HERE.txt** | Short instructions |
| **1 - START Paralegal Research Desk.cmd** | Windows setup and launch |
| **2 - REPAIR Setup.cmd** | Repair Windows setup |
| **3 - START on Mac.command** | macOS helper; requires Python 3.11+ |
| data/ | Your database, created automatically |
| logs/ | Setup logs, created automatically |
| .runtime/ | Private environment and browser, created automatically |
| app/ and scripts/ | Program internals |
| docs/ | Technical instructions and audit |
| tests/ | Automated verification |

Back up **data/** with the application stopped. When downloading an update into a separate folder, copy your old data folder into it before launching. Do not copy .runtime between machines.

## macOS and Linux
Install Python 3.11 or newer, then run **bash scripts/start-unix.sh** from the extracted folder. The macOS .command file runs the same helper; Finder may require executable permission after ZIP extraction. The helper creates a local environment and installs packages and Chromium. Linux may need the system libraries requested by Playwright. Operating-system packages are not automatically installed.

## Technical documentation
- [Configuration, selectors, and development](docs/TECHNICAL.md)
- [Engineering audit and limitations](docs/AUDIT.md)
- [Interface, logging, and setup details](docs/INTERFACE-AND-SETUP.md)

This remains a single-process local proof of concept. A completed scan means the configured traversal finished, not that every possible record has been found. Use permitted public sources.



