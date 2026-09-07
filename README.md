# Paralegal Database Tool

This project is a **Paralegal Database Tool** for maintaining and updating a law firm's contractor/bidder due-diligence database. It combines the firm's existing 30-column bidder database with targeted research against **six designated public-record websites**, preserves the source evidence behind every collected finding, and gives a paralegal a controlled review step before new information changes the approved master database.

The central workflow is **import the existing bidder CSV → run source-specific contractor queries → collect only the relevant public-record findings → compare those findings with the approved master → review highlighted differences → approve or dismiss updates → search/export the current master database**.

## Bidder database structure

The user-facing database and CSV/Excel/JSON exports now follow the law firm's supplied **Bidder Database-Example.csv** layout exactly, in this exact 30-column order:

`id`, `contractor_name`, `related_companies`, `address_1`, `city`, `state`, `zip`, `additional_address`, `additional_address_city`, `additional_address_state`, `additional_address_zip`, `dfi`, `wc`, `wc_date`, `osha_severe_violations`, `years`, `osha`, `state_federal_debarment`, `mndol_ineligibility`, `public_works_projects_budget_time_quality_complaint`, `federal_court`, `circuit_court`, `ccap_show150`, `environmental_violations`, `prevailing_wage_violations`, `dwd`, `dwd_substance_abuse_plan`, `better_business_bureau_complaints`, `misc_violations`, `tax_liability`.

The crawler still keeps internal source URL, collection timestamps, generic contact details, page evidence, and change history underneath those 30 fields. That provenance is deliberately not added as extra columns to the bidder export.

The generic extractor recognizes the exact field names above plus common human-readable label variants, but it is only a fallback. The production collector is intentionally **query-focused**: each of the six real website adapters should submit the contractor/company identifiers appropriate to that source, follow only the result/detail/pagination paths needed for that lookup, and map only that source's authoritative findings into the relevant bidder fields. The six adapters will determine the real query inputs, result matching rules, pagination, and authoritative mapping for DFI, workers' compensation, OSHA, debarment, courts, DWD, BBB, tax liability, and other compliance fields.

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

## OSHA proof of concept — Source 1

The first real source integration is OSHA's Establishment Search:

- user-provided legacy URL: `https://www.osha.gov/pls/imis/establishment.html`
- current OSHA route used by the adapter: `https://www.osha.gov/ords/imis/establishment.html`
- query endpoint: `/ords/imis/establishment.search`
- inspection detail endpoint: `/ords/imis/establishment.inspection_detail`

This adapter is **master-database driven**. It does not crawl OSHA generally. Before running it, import the law firm's bidder CSV. A scan then builds OSHA establishment searches only for the contractors and related-company names in that approved master database.

For this proof of concept, the OSHA adapter explicitly **skips the generic `robots.txt` policy gate**. OSHA currently rejects both the generic HTTP crawler and headless Chromium from the local proof-of-concept with HTTP 403. The OSHA source therefore opens a normal visible Chromium window on the operator desktop, first loads the public establishment form, retains normal session state, and then navigates the public search/detail pages. Browser egress remains hard-limited to `www.osha.gov` and only the establishment form, search, and inspection-detail paths. It does not implement CAPTCHA solving, stealth fingerprinting, credential bypass, or arbitrary browser navigation. The override applies to the OSHA POC adapter only.

OSHA's public search limits a single inspection-date query to ten years, so the adapter searches consecutive ten-year windows from 1972 through the current date. It searches open and closed cases and both inspections with and without violations. Result rows are accepted automatically only when the OSHA establishment name exactly matches the contractor/related-company name after conservative punctuation and legal-suffix normalization. Similar names are retained as unresolved candidates so a spelling variation cannot silently create a false negative; they are not treated as authoritative matches.

For the proof of concept, the OSHA fields mean:

- `osha = Y` — at least one exact-name OSHA inspection match was found.
- `osha = N` — no exact-name inspection match and no plausible similar-name candidate was found **and the entire targeted scan completed without errors/limits**. Similar-but-unresolved names remain unknown rather than being converted to N.
- `osha_severe_violations` — sum of the inspection detail page's **current Serious + Willful + Repeat** violations across matched inspections.
- `years` — years in which a matched inspection has at least one current Serious, Willful, or Repeat violation.

The severe-violation definition is an explicit proof-of-concept rule and can be changed if the law firm's actual criterion differs. On a partial/error scan, positive OSHA existence can still be retained, but the adapter deliberately does not issue a negative finding or an aggregate severe count that could be incomplete.

The adapter stores inspection IDs, detail URLs, dates, current violation categories, search terms, query count, and the aggregate-completeness flag as source evidence. These details remain outside the firm's flat 30-column export.

## Query-focused source integrations
Five website names and URLs are still **pending**. Source 1 is now the OSHA Establishment Search proof of concept; the remaining five positions are ready for the other sources.

The master database, comparison/review workflow, exports, crawl engine, and generic fallback extractor are in place. What is **not finished yet** is the site-specific query logic. Once the actual sites are supplied, each adapter must be tested against real examples and should define: the contractor/company query inputs; search-form or public endpoint behavior; result matching and identity rules; pagination/detail-page navigation; which bidder fields that site is authoritative for; and exactly what constitutes a positive, negative, unknown, or historical finding.

A broad same-host crawl remains useful for discovery and as a fallback, but the intended law-firm workflow is **not** to indiscriminately scrape every page. Production adapters should prioritize targeted queries for the contractors already in the master database, plus narrowly scoped discovery where the source supports it.

**Searching saved bidder records is local database search.** Collecting from a configured site will become a source-specific contractor lookup once that site's adapter is implemented.

The interface and record schema follow this workflow. Collection, search, and exports keep source evidence attached to each result. Current collection handles supported webpage records; PDF text extraction and OCR are not implemented.

## Start on Windows
1. Download the repository ZIP and **extract all files**.
2. Double-click **1 - START Paralegal Research Desk.cmd** in the extracted folder.
3. Leave its window open. First setup downloads a private Python environment, packages, and Chromium, then opens your browser.

No existing Python or administrator access is needed. Internet is required for setup. Everything installs inside this folder. Windows 10/11 x64 and ARM64 are supported by the launcher; x64 is tested.

Later launches reuse the environment. To stop, press **Ctrl+C** in the launcher window. Closing the browser tab alone does not stop the server.

If setup fails, run **2 - REPAIR Setup.cmd**, then start again. Repair preserves your database. Setup details are in **logs/setup.log**. If company policy blocks scripts or downloads, ask your IT administrator.

## Your workspace
- **Master bidder database:** upload the firm's existing CSV, search the approved 30-column database, and export the current master as CSV, Excel, or JSON.
- **Research websites:** six visible source positions ready for source-specific contractor-query adapters. Edit collection settings without removing saved records.
- **Collection:** run targeted source lookups, retain raw source evidence, and follow progress/errors.
- **Comparison review:** compare source findings against the approved master, see old values in red and newly found values in green, then approve or dismiss each proposed change.
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

This remains a single-process local proof of concept. The database/review workflow is substantially implemented; the six source integrations still require real-site adapters before collection can be considered production-ready. A completed generic scan means the configured traversal finished, not that every relevant legal/public record has been found. Use only permitted public sources.



