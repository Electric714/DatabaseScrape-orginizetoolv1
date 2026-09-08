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

## OSHA / DOL REST API — Source 1

OSHA is a built-in integration. The application automatically creates and pins it to the U.S. Department of Labor Open Data REST API instead of asking the operator to configure an OSHA website.

- API base: `https://apiprod.dol.gov/v4`
- inspection dataset: `/get/OSHA/inspection/json`
- violation dataset: `/get/OSHA/violation/json`

Before collection, import the firm's bidder CSV. The adapter builds targeted API queries only for each approved `contractor_name` and any `related_companies`. It uses `address_1`, `city`, `state`, and `zip` only to resolve identity when more than one exact normalized company name could match. Similar-but-nonexact names remain unresolved for human review rather than being silently treated as the contractor.

The DOL Open Data API requires an API key for data requests. DOL describes registration as a free API account. The built-in OSHA card has a **Set API key** button; the app tests the key against DOL before saving it to `.runtime/dol_api_key.txt`. The key can also be supplied through the `DOL_API_KEY` environment variable. `.runtime/` and `.env` are ignored by Git. The application sends the key only in the `X-API-KEY` request header; it is never placed in a URL, source record, activity event, export, or API response.

OSHA is authoritative for only three fields in the firm's 30-column bidder database:

- `osha`
- `osha_severe_violations`
- `years`

The remaining 27 bidder fields belong to the other research sources and are not written by the OSHA adapter. Contractor name and location fields are matching inputs only.

For this integration, the OSHA fields mean:

- `osha = Y` — at least one exact normalized OSHA inspection match was found.
- `osha = N` — no exact match and no plausible similar-name candidate was found, and the entire targeted API collection completed without errors or limits.
- blank `osha` — a plausible similar-name result exists and needs identity review, or a partial/error run cannot support a negative conclusion.
- `osha_severe_violations` — count of non-deleted OSHA violation rows classified Serious, Willful, or Repeat across the matched inspections.
- `years` — years associated with those Serious, Willful, or Repeat citations, using the inspection open year only when the citation year is unavailable.

Inspection IDs, establishment/location fields, dates, citation IDs, violation types, penalties, matching terms, unresolved candidates, and completeness flags are retained as source evidence outside the flat 30-column export. That evidence supports review without letting OSHA overwrite fields it does not own.

Because this is a documented REST API integration, the application does not navigate OSHA pages, launch a special browser session, or apply website `robots.txt` logic for these API requests.

## SAM.gov Federal Debarment / Exclusions API — Source 2

SAM.gov Federal Debarment is also a built-in integration. For the current proof-of-concept the application is pinned to the official **v4 Alpha/test Exclusions API** documented by GSA:

- Alpha/test endpoint: `https://api-alpha.sam.gov/entity-information/v4/exclusions`
- Production endpoint reserved for promotion later: `https://api.sam.gov/entity-information/v4/exclusions`
- API documentation: `https://open.gsa.gov/api/exclusions-api/`

The adapter submits targeted `classification=Firm`, `recordStatus=Active`, and `exclusionName` queries for each approved contractor and related company. It performs conservative normalized company-name matching and uses the bidder address/state/city/ZIP only for disambiguation when necessary.

SAM requires an API key as the `api_key` query parameter. The built-in SAM card has a **Set SAM test API key** control; the app validates the key against the official Alpha endpoint before saving it to `.runtime/sam_api_key.txt`. `SAM_API_KEY` may also be supplied as an environment variable.

Because SAM requires the credential in the query string, the crawler uses a credential-injection hook: stored source URLs, page-cache URLs, record evidence, activity events, diagnostics, and bidder exports contain the credential-free logical URL. The API key is added only to the outbound network request.

SAM is authoritative only for the **federal positive component** of `state_federal_debarment`:

- an exact active federal exclusion match may propose `state_federal_debarment = Y`;
- a clean SAM search does **not** propose `N`, because the same combined field can still be positive due to a state debarment source;
- similar names remain evidence for manual identity review and do not update the master automatically.

The Alpha endpoint is for testing. GSA documents separate Alpha access/account steps and rate limits; production promotion should happen only after the adapter has been validated with the law firm's examples.

## BBB Business Profiles / Complaints — Source 3

BBB is a built-in targeted parser. It does not perform a broad crawl of BBB.org and it does not use BBB data to populate unrelated bidder fields.

For every approved master bidder, the adapter searches only the current `contractor_name` and any `related_companies`, using the master city/state/ZIP as location context. Candidate BBB profiles are matched conservatively by normalized business identity plus location evidence. This is specifically designed to avoid same-name false matches such as businesses with similar names in different cities.

The collection path is:

`master bidder → targeted BBB company/location lookup → plausible profile candidates → exact profile identity check → matched /complaints page → aggregate complaint evidence`

BBB is authoritative for exactly one master field:

- `better_business_bureau_complaints`

The field semantics are:

- `Y` — at least one exact company/location BBB profile reports one or more complaints in BBB's rolling three-year complaint summary.
- `N` — an exact matched BBB profile reports zero complaints in the three-year summary, or a complete targeted search found no exact/plausible BBB profile attributable to that bidder.
- blank — similar profiles require manual identity review, a matched profile's complaint summary could not be parsed reliably, or collection was incomplete.

The evidence record retains the matched BBB profile URL/name/address, BBB's three-year complaint count, complaints closed in the last 12 months, and available complaint date/type/status metadata. Consumer complaint narratives are deliberately **not retained** because the bidder database only needs complaint presence plus enough provenance to review the finding.

The parser has multiple layout fallbacks: ordinary server-rendered profile links, embedded JSON search payloads, JSON-LD profile identity data, semantic text/address parsing, and flexible complaint-summary wording. It follows at most three explicit search-result pages and uses one request at a time with a built-in delay. A 401/403/429 or explicit access challenge stops the source rather than attempting to defeat the site's controls.

## Query-focused source integrations
Three website positions are still **pending**. Source 1 is OSHA/DOL, Source 2 is SAM.gov Federal Debarment, and Source 3 is the BBB complaint parser.

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
- **Research websites:** six visible source positions, including built-in OSHA/DOL, SAM.gov Federal Debarment, and BBB complaint sources, with remaining positions reserved for source-specific adapters.
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



