"""Explicit field ownership; unsupported fields remain untouched."""
from urllib.parse import urlsplit
from .bidder_schema import BIDDER_COLUMNS

SOURCE_CATALOG = [
    {"key": "osha", "name": "OSHA / DOL", "method": "REST API", "status": "Needs API Key",
     "fields": ["osha", "osha_severe_violations", "years"], "hosts": ["apiprod.dol.gov", "api.dol.gov", "www.osha.gov"],
     "note": "Current DOL v4 API. X-API-KEY is sent as the documented query parameter only at network egress; live authenticated collection awaits operator retest."},
    {"key": "pacer", "name": "PACER — Federal Court Records", "method": "Deferred", "status": "Excluded from POC",
     "fields": ["federal_court"], "hosts": [], "note": "No collection or search-engine substitute."},
    {"key": "sam", "name": "SAM.gov federal exclusions", "method": "REST API", "status": "Needs API Key",
     "fields": ["state_federal_debarment"], "hosts": ["api.sam.gov", "api-alpha.sam.gov"],
     "note": "Production v4 API. Exact active federal exclusions may propose Y; a clean federal search never clears the combined state/federal field."},
    {"key": "bbb", "name": "Better Business Bureau", "method": "Published profile sitemaps + local profile navigation", "status": "Fail-closed sitemap/profile collector",
     "fields": ["better_business_bureau_complaints"], "hosts": ["bbb.org", "www.bbb.org"],
     "note": "BBB's published business-profile sitemap index is used for discovery; /search is not required. POC sitemap ranges cover FL, IL, MN, MO, OH, and WI. Each contractor is isolated so one blocked profile does not invalidate unrelated verified evidence. Exact company and location identity is required, and N is allowed only when the exact verified profile's complaint page is successfully reached and explicitly reports zero in BBB's rolling three-year period. Blocked, malformed, partial, missing-profile, or ambiguous outcomes remain UNKNOWN."},
    {"key": "vt", "name": "Violation Tracker", "method": "Targeted public HTML", "status": "Fail-closed parser; live access environment-dependent",
     "fields": ["environmental_violations", "prevailing_wage_violations", "misc_violations"], "hosts": ["violationtracker.goodjobsfirst.org"],
     "note": "Uses only public search/detail HTML and never subscriber-only downloads. HTTP/access failures, incomplete pagination, and unrecognized layouts remain UNKNOWN. A parent-company result alone never proves a subsidiary/master-contractor match. Generic wage-and-hour findings are retained as evidence but do not map to prevailing-wage violations unless the public record explicitly identifies prevailing-wage, Davis-Bacon, or equivalent public-works wage coverage."},
    {"key": "dol", "name": "DOL enforcement / open data", "method": "REST API", "status": "Shared with OSHA",
     "fields": [], "hosts": [], "note": "OSHA is queried once. Additional labor datasets require schema and field-semantic verification."},
    {"key": "state_mn", "name": "State debarment — Minnesota OSP", "method": "Official finite HTML list", "status": "Live list retrieval verified",
     "fields": ["state_federal_debarment"], "hosts": ["mn.gov"],
     "note": "Checks active dates. Minnesota procurement coverage only; not Minnesota DLI or all-state clearance."},
    {"key": "state_il", "name": "State debarment — Illinois IDOL public works", "method": "Official finite HTML list", "status": "Collector implemented",
     "fields": ["state_federal_debarment", "prevailing_wage_violations"], "hosts": ["labor.illinois.gov"],
     "note": "Positive-only exact listed-name evidence. The official page ties debarment to Prevailing Wage Act violations; absence never clears either field."},
    {"key": "state_wi", "name": "State debarment — Wisconsin DOT", "method": "Official PDF", "status": "Collector implemented",
     "fields": ["state_federal_debarment"], "hosts": ["wisconsindot.gov", "www.wisconsindot.gov"],
     "note": "Positive-only targeted PDF matching with location and active-date corroboration. WisDOT list is not a complete federal/all-agency clearance."},
]


def owned_fields(url):
    host = urlsplit(url).hostname
    for source in SOURCE_CATALOG:
        if host in source["hosts"]:
            return source["fields"]
    return []


def field_map():
    return {field: [s["name"] for s in SOURCE_CATALOG if field in s["fields"]] for field in BIDDER_COLUMNS}