# Paralegal Research Desk

This project is being built for a working paralegal: collect relevant public records from **six designated research websites**, keep them organized in a **local searchable database**, and quickly find business, ownership, address, and regulatory information needed for legal-document research.

The central workflow is **select the research sites → collect their records → search the saved database → inspect the original source → export the relevant results**.

## Bidder database structure

The user-facing database and CSV/Excel/JSON exports now follow the law firm's supplied **Bidder Database-Example.csv** layout exactly, in this exact 30-column order:

`id`, `contractor_name`, `related_companies`, `address_1`, `city`, `state`, `zip`, `additional_address`, `additional_address_city`, `additional_address_state`, `additional_address_zip`, `dfi`, `wc`, `wc_date`, `osha_severe_violations`, `years`, `osha`, `state_federal_debarment`, `mndol_ineligibility`, `public_works_projects_budget_time_quality_complaint`, `federal_court`, `circuit_court`, `ccap_show150`, `environmental_violations`, `prevailing_wage_violations`, `dwd`, `dwd_substance_abuse_plan`, `better_business_bureau_complaints`, `misc_violations`, `tax_liability`.

The crawler still keeps internal source URL, collection timestamps, generic contact details, page evidence, and change history underneath those 30 fields. That provenance is deliberately not added as extra columns to the bidder export.

The generic extractor recognizes the exact field names above plus common human-readable label variants. Fields a website does not provide remain blank until that source's adapter can populate them. The six real website adapters will determine the authoritative meaning and mapping for DFI, workers' compensation, OSHA, debarment, courts, DWD, BBB, tax liability, and other compliance fields.

A blank compliance field means **unknown or not supplied by that source**, not a clean record. Automated adapters must preserve source wording rather than infer legal conclusions from missing data.

## Existing CSV → master database → reviewed updates

The law firm's existing bidder CSV can now be uploaded directly from the workspace. The import keeps every bidder field as text, including ZIP codes and IDs, and merges matching rows by bidder ID first and then by normalized contractor name/address. An import **does not delete contractors that are absent from the uploaded file**.

The uploaded rows become the approved **master bidder database**. Crawled website records remain separate source evidence. Clicking **Compare collected data** creates a review queue instead of overwriting the master automatically:

- current master value is highlighted in red;
- newly found value is highlighted in green;
- each change includes the research source;
- **Update** applies one proposed field change;
- **Keep old** dismisses that proposal;
- newly discovered contractors can be approved with **Add contractor**;
- ambiguous contractor-name matches are flagged for manual review and cannot be applied blindly.

Blank values from a website never erase an existing master value. Dismissed findings do not keep reappearing unless the source later reports a materially different value. Approved source-driven changes are written to a bidder master history table with their source URL and timestamp.

The master table can be searched and exported in the exact 30-column CSV, Excel, or JSON format expected by the existing bidder database.

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
- **Bidder database:** review the exact 30-column law-firm layout, search across saved source data, filter by source, inspect provenance, and export the same 30-column structure as CSV, Excel, or JSON. Open a contractor name to review source wording, links, and collection dates.
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



