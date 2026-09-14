# Contractor research POC review — 2026-09-14

## Outcome

Overall feasibility is **not yet proven**. The existing master/import/review architecture is reusable. Minnesota's official finite HTML list was fetched twice and parsed consistently. BBB search HTML returned a candidate, but its profile returned HTTP 403. Violation Tracker returned HTTP 403. No API credentials were provided for authenticated OSHA/DOL or SAM company lookups. Do not confuse passing fixture tests with working live collection.

The objective remains: research only imported contractors, save evidence separately, and compare without automatically overwriting approved data.

## Existing implementation assessment

Keep SQLite, the 30-column import/export format, separate source records, review queue, activity console, local Windows launcher, credential redaction, and source-specific OSHA/SAM/BBB adapters. The initial 111 non-browser tests passed before changes.

Fixes made: exact names now need location corroboration even when only one master contractor has that name; alternative master locations can corroborate identity. OSHA job-site differences remain review candidates rather than automatic attribution. BBB follows explicit pagination through its cap and reports capped lookups incomplete; no profile no longer means zero complaints. Its previous robots override was removed. SAM Alpha is evidence-only, never a real compliance proposal. Source ownership is enforced during comparison. Research cannot add contractors. Failed refreshes cannot revive stale proposals, and changed evidence/master values must be compared again before approval. Partial runs retain unknown findings where adapters can finalize. IDs and ZIPs remain strings, and whitespace in imported cells is preserved.

The existing generic crawler remains for internal regression coverage, but the application launch API refuses unregistered generic sources. There is no broad crawl fallback in the POC workflow.

## CSV and test companies

The supplied file has 24 rows and 30 columns. Primary states: WI 13, IL 5, MN 3, OH 1, MO 1, FL 1. All original cell values survived a SQLite import/read roundtrip exactly. Related companies are preserved as entered; semicolon, pipe, and newline separate aliases, while commas within company names are retained.

Live probes used one Wisconsin contractor for BBB/Violation Tracker and three Minnesota contractors for Minnesota. The Minnesota sample had no exact-name candidates among the 44 returned list entries. No master changes were inferred. The two Minnesota fetches yielded identical parsed entries. This validates list acquisition and absence handling, not live positive identity matching; positive/historical/date-conflict behaviors are tested using synthetic fixtures.

Contractor-specific query URLs, identities, and evidence from the supplied CSV are excluded from this repository change.

## Field ownership

| CSV fields | Source and interpretation |
| --- | --- |
| id, contractor_name, related_companies, address_1, city, state, zip, additional_address, additional_address_city, additional_address_state, additional_address_zip | Master identity inputs only; never updated by collectors |
| osha, osha_severe_violations, years | DOL OSHA inspection/violation datasets. Existing semantics: inspection presence; Serious/Willful/Repeat citation count and years. Confirm these meanings with the firm before operational use |
| state_federal_debarment | Federal: SAM production, when implemented/validated; current Alpha is test evidence only. State: active, confirmed Minnesota OSP entry may propose Y. No single negative lookup proposes N |
| better_business_bureau_complaints | Matched BBB profile's explicit three-year complaint count; no profile or inaccessible summary remains unknown |
| environmental_violations | Violation Tracker explicitly environmental offense categories, confirmed identity, positive only; provisional HTML parser |
| prevailing_wage_violations | Explicit prevailing-wage/Davis-Bacon evidence only; ordinary wage-and-hour categories do not qualify |
| misc_violations | Explicit allowlisted non-OSHA/non-environmental offenses; no catch-all mapping |
| federal_court | PACER excluded from POC |
| dfi, wc, wc_date, mndol_ineligibility, public_works_projects_budget_time_quality_complaint, circuit_court, ccap_show150, dwd, dwd_substance_abuse_plan, tax_liability | No implemented authoritative source in this POC; unchanged |

`app/source_catalog.py` is the executable mapping. DOL's live catalog lists OSHA inspection (dataset 10334), OSHA violation (10338), and WHD enforcement (10362). The WHD dataset exists, but additional schema/semantic validation is needed before a collector writes prevailing-wage fields. No duplicate OSHA queries are launched for the separate DOL category.

## Acquisition findings

BBB: ordinary search HTML contained a candidate for the selected contractor. The master and candidate used different city labels but shared a ZIP. The profile was not retrieved and no complaint count was established. The search-card text fallback also accidentally included a telephone suffix in the address; this illustrates why a search card alone must never establish a final complaint finding. Its downstream profile step returned 403. Do not bypass access controls or infer N. Public JSON-LD/embedded JSON parsing remains available but did not solve this blocked profile request. Browser interaction was not used to bypass that response.

Violation Tracker: public search and homepage returned 403 from this environment. Its own [user guide](https://violationtracker.goodjobsfirst.org/pages/user-guide) says search/display are free but downloads and several fields require a subscription. No free bulk feed was verified. The new HTML adapter is provisional and fail-closed; it has **not** passed a live detail-page test and may need selector changes when permitted access is available. It never uses subscriber-only routes and never emits negative compliance flags. Parent-company search results alone do not establish subsidiary identity.

Minnesota: [OSP list](https://mn.gov/admin/osp/government/suspended-debarred/) is server-rendered HTML including hidden detail tables. No JavaScript is needed. Validate the reported total against parsed entries, then match only selected master contractors. The list includes historical suspensions/debarments; active date intervals are required for a positive. Missing/unparseable end dates remain unresolved. Minnesota OSP is not Minnesota DLI, so `mndol_ineligibility` remains untouched.

APIs: DOL catalog discovery succeeded without credentials; authenticated data requests were not attempted with guessed keys. GSA's [v4 Exclusions documentation](https://open.gsa.gov/api/exclusions-api/) documents separate Alpha and production endpoints. Existing Alpha behavior is retained and visibly labeled; it cannot write real master fields. Production promotion is a remaining task. GSA documents a 10-request/day tier for personal users with no role, making small selected-company tests preferable to whole-database runs.

## State-specific next sources

| State | Authoritative resource | Acquisition status |
| --- | --- | --- |
| MN | [OSP suspension/debarment list](https://mn.gov/admin/osp/government/suspended-debarred/) | Implemented finite HTML list; live retrieval repeated |
| WI | [WisDOT list](https://wisconsindot.gov/hccidocs/debar.pdf), [DOA procurement](https://doa.wi.gov/Pages/StateEmployees/Procurement.aspx), [contract-compliance ineligible PDF](https://doa.wi.gov/Documents/DEO/WOCCELIIneligible.pdf) | WisDOT targeted PDF collector implemented with exact-name, location and active-date corroboration; live app retrieval still needs a permitted end-to-end run. Other Wisconsin lists remain separate/unimplemented |
| IL | [Labor public-works debarred contractors](https://labor.illinois.gov/laws-rules/conmed/debarred-contractors.html) | Finite HTML collector implemented; exact active listed names can propose debarment and prevailing-wage flags, with manual approval still required |
| MO | [OA suspended/debarred vendors](https://purch.oa.mo.gov/media/pdf/suspendeddebarred-vendors) | Official PDF resource identified; not implemented |
| FL | [DMS suspended vendors](https://www.dms.myflorida.com/business_operations/state_purchasing/state_agency_resources/vendor_registration_and_vendor_lists/suspended_vendor_list), [convicted vendors](https://www.dms.myflorida.com/business_operations/state_purchasing/state_agency_resources/vendor_registration_and_vendor_lists/convicted_vendor_list) | Separate public lists identified; not implemented |
| OH | Ohio DAS/OFCC debarment lists | Authority identified; current list acquisition remains unresolved |

A company's headquarters state does not determine every jurisdiction in which it works. The Minnesota collector can check any selected master contractor against the finite Minnesota list, but that never represents all-state coverage.

## Running the POC

Use the existing Windows launcher. Import the CSV. In Your research sites, select a few contractors and click the desired source's Collect button. Source coverage and ownership are visible in the expandable catalog; PACER explicitly shows Excluded from POC. Results remain source evidence. Compare collected data creates eligible proposals; Update is a separate manual action.

Credentials: use **Set OSHA API key** and **Set SAM test API key** in the source section. These are the single recommended entry points, not adapter source files. Existing environment overrides (`DOL_API_KEY`, `SAM_API_KEY`) are retained for compatibility and take precedence; remove an override if changing a key in the UI. Keys are stored locally in ignored `.runtime` files. **Test DOL API / Test SAM API** retests the saved key and verifies response structure. Never commit credentials.

Tests: `.venv/bin/pytest -q --ignore=tests/test_browser.py` (or `python -m pytest -q --ignore=tests/test_browser.py` in your environment). JavaScript syntax checked with `node --check app/static/app.js`. Browser rendering QA was unavailable because the Chromium download timed out in this environment. No browser-visual verification is claimed.

## Remaining acceptance gates

Supply and validate API credentials, complete a permitted BBB profile/complaint lookup, validate the provisional Violation Tracker parser against real HTML, and obtain repeatable positive identity matches. Add state-specific collectors only as needed. Until those gates pass, this is improved POC code with an honest feasibility report, not a validated legal research service.

## State-source implementation update

Illinois IDOL and Wisconsin WisDOT collectors were added after the initial review. Illinois is parsed as a finite official HTML list and only exact active listed names can produce positive evidence. Because the current Illinois page does not publish location alongside the listed name, the identity basis is explicitly recorded and the finding still requires manual proposal approval. The Illinois source owns both `state_federal_debarment` and `prevailing_wage_violations` because the page explicitly describes the debarment as a Prevailing Wage Act consequence.

Wisconsin WisDOT is an official PDF. The crawler now has a narrow binary-content opt-in: ordinary asset URLs remain blocked, while an adapter may explicitly allow a content type and extension. The WisDOT adapter extracts PDF text and searches only selected master aliases; it requires an exact name plus corroborating city/state or ZIP and an active restriction date before proposing `state_federal_debarment=Y`. Missing, expired, malformed, or location-mismatched entries remain unknown/no-change. This does not make WisDOT a substitute for Wisconsin DOA, DWD, federal SAM, or other jurisdiction-specific lists.
