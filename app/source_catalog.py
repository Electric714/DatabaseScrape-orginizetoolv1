"""Explicit field ownership; unsupported fields remain untouched."""
from urllib.parse import urlsplit
from .bidder_schema import BIDDER_COLUMNS

SOURCE_CATALOG = [
    {"key": "osha", "name": "OSHA / DOL", "method": "REST API", "status": "Needs API Key",
     "fields": ["osha", "osha_severe_violations", "years"], "hosts": ["apiprod.dol.gov", "api.dol.gov", "www.osha.gov"],
     "note": "Live authenticated lookup untested. Inspection presence is not necessarily a violation."},
    {"key": "pacer", "name": "PACER — Federal Court Records", "method": "Deferred", "status": "Excluded from POC",
     "fields": ["federal_court"], "hosts": [], "note": "No collection or search-engine substitute."},
    {"key": "sam", "name": "SAM.gov federal exclusions", "method": "REST API", "status": "Needs API Key",
     "fields": ["state_federal_debarment"], "hosts": ["api-alpha.sam.gov", "api.sam.gov"],
     "note": "Alpha/test evidence only; never proposes real compliance changes. Federal absence cannot clear combined field."},
    {"key": "bbb", "name": "Better Business Bureau", "method": "Targeted HTML", "status": "Blocked",
     "fields": ["better_business_bureau_complaints"], "hosts": ["bbb.org", "www.bbb.org"],
     "note": "Search HTML retrieved; profile returned 403. Complaint collection not yet proven. No profile is not zero complaints."},
    {"key": "vt", "name": "Violation Tracker", "method": "Targeted HTML", "status": "Blocked",
     "fields": ["environmental_violations", "prevailing_wage_violations", "misc_violations"], "hosts": ["violationtracker.goodjobsfirst.org"],
     "note": "Live HTTP 403. Parser provisional; bulk downloads require subscription. General wage-and-hour findings do not prove prevailing-wage violations."},
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
