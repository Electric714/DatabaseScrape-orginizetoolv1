# Public Data Monitor

A local workspace for monitoring public websites, organizing records, and following each scan.

## Start on Windows
1. Download the repository ZIP and **extract all files**.
2. Double-click **1 - START Public Data Monitor.cmd** in the extracted folder.
3. Leave its window open. First setup downloads a private Python environment, packages, and Chromium, then opens your browser.

No existing Python or administrator access is needed. Internet is required for setup. Everything installs inside this folder. Windows 10/11 x64 and ARM64 are supported by the launcher; x64 is tested.

Later launches reuse the environment. To stop, press **Ctrl+C** in the launcher window. Closing the browser tab alone does not stop the server.

If setup fails, run **2 - REPAIR Setup.cmd**, then start again. Repair preserves your database. Setup details are in **logs/setup.log**. If company policy blocks scripts or downloads, ask your IT administrator.

## Your workspace
- **Add a website:** enter its public URL and extraction selectors. Advanced options control limits, rendering, and scheduling.
- **Sources:** run scans, force fresh scans, or remove sources.
- **Records:** search, filter, paginate, and export CSV, Excel, or JSON.
- **Activity console:** follow progress, filter warnings/errors, search, pause, and jump to the latest event.

To share a problem, reproduce it, click **Capture snapshot**, then **Export report**. This downloads a diagnostic ZIP. If the server is unavailable, export downloads the visible browser logs as JSON.

Logs are saved automatically: 5,000 events in the database and up to 1,000 in the browser. Reports exclude collected records and redact common credentials and URL query values. Review before sharing: paths, URLs, and error context can still identify your environment. Nothing is automatically sent to anyone.

## Download layout
| File or folder | Purpose |
| --- | --- |
| **0 - START HERE.txt** | Short instructions |
| **1 - START Public Data Monitor.cmd** | Windows setup and launch |
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
