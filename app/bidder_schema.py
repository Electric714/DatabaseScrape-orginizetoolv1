import json
import re
from typing import Any

BIDDER_COLUMNS = [
    "id",
    "contractor_name",
    "related_companies",
    "address_1",
    "city",
    "state",
    "zip",
    "additional_address",
    "additional_address_city",
    "additional_address_state",
    "additional_address_zip",
    "dfi",
    "wc",
    "wc_date",
    "osha_severe_violations",
    "years",
    "osha",
    "state_federal_debarment",
    "mndol_ineligibility",
    "public_works_projects_budget_time_quality_complaint",
    "federal_court",
    "circuit_court",
    "ccap_show150",
    "environmental_violations",
    "prevailing_wage_violations",
    "dwd",
    "dwd_substance_abuse_plan",
    "better_business_bureau_complaints",
    "misc_violations",
    "tax_liability",
]

# Labels commonly seen in spreadsheets/forms for the law firm's bidder database.
# Exact snake_case column names are also accepted by the generic extractor.
BIDDER_FIELD_ALIASES = {
    "bidder_id": {"id", "bidder id", "contractor id"},
    "contractor_name": {"contractor_name", "contractor name", "bidder name"},
    "related_companies": {"related_companies", "related companies", "related company", "affiliated companies"},
    "address_1": {"address_1", "address 1", "primary address"},
    "city": {"city"},
    "state": {"state"},
    "zip": {"zip", "zip code", "postal code"},
    "additional_address": {"additional_address", "additional address", "secondary address"},
    "additional_address_city": {"additional_address_city", "additional address city", "secondary address city"},
    "additional_address_state": {"additional_address_state", "additional address state", "secondary address state"},
    "additional_address_zip": {"additional_address_zip", "additional address zip", "secondary address zip"},
    "dfi": {"dfi", "department of financial institutions", "wisconsin dfi"},
    "wc": {"wc", "workers comp", "workers compensation", "worker's compensation"},
    "wc_date": {"wc_date", "wc date", "workers comp date", "workers compensation date"},
    "osha_severe_violations": {"osha_severe_violations", "osha severe violations", "severe osha violations"},
    "years": {"years", "year(s)", "violation years"},
    "osha": {"osha", "osha flag"},
    "state_federal_debarment": {"state_federal_debarment", "state federal debarment", "state/federal debarment", "debarment"},
    "mndol_ineligibility": {"mndol_ineligibility", "mndol ineligibility", "mn dol ineligibility", "minnesota dol ineligibility"},
    "public_works_projects_budget_time_quality_complaint": {
        "public_works_projects_budget_time_quality_complaint",
        "public works projects budget time quality complaint",
        "public works complaint",
    },
    "federal_court": {"federal_court", "federal court"},
    "circuit_court": {"circuit_court", "circuit court"},
    "ccap_show150": {"ccap_show150", "ccap show150", "ccap show 150", "ccap"},
    "environmental_violations": {"environmental_violations", "environmental violations"},
    "prevailing_wage_violations": {"prevailing_wage_violations", "prevailing wage violations"},
    "dwd": {"dwd", "department of workforce development", "wisconsin dwd"},
    "dwd_substance_abuse_plan": {"dwd_substance_abuse_plan", "dwd substance abuse plan", "substance abuse plan"},
    "better_business_bureau_complaints": {
        "better_business_bureau_complaints",
        "better business bureau complaints",
        "bbb complaints",
        "bbb",
    },
    "misc_violations": {"misc_violations", "misc violations", "miscellaneous violations"},
    "tax_liability": {"tax_liability", "tax liability"},
}

_LOCATION_RE = re.compile(
    r"^\s*(?P<city>.+?)\s*,\s*(?P<state>[A-Za-z]{2})(?:\s+(?P<zip>\d{5}(?:-\d{4})?))?\s*$"
)


def capture_bidder_fields(record: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Preserve law-firm bidder fields inside record provenance without losing generic fields."""
    result = dict(extra or {})
    bidder = dict(result.get("bidder_fields") or {})
    if record.get("bidder_id") not in (None, ""):
        bidder["id"] = str(record.get("bidder_id")).strip()
    for key in BIDDER_COLUMNS:
        if key == "id":
            continue
        if key in record and record.get(key) not in (None, ""):
            bidder[key] = str(record.get(key)).strip()
    if bidder:
        result["bidder_fields"] = bidder
    return result


def _extra_dict(row: dict[str, Any]) -> dict[str, Any]:
    extra = row.get("extra")
    if isinstance(extra, dict):
        return extra
    raw = row.get("extra_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            value = json.loads(raw)
            return value if isinstance(value, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def _location_parts(location: str) -> tuple[str, str, str]:
    match = _LOCATION_RE.match((location or "").strip())
    if not match:
        return "", "", ""
    return (
        match.group("city").strip(),
        match.group("state").upper(),
        (match.group("zip") or "").strip(),
    )


def bidder_row(row: dict[str, Any]) -> dict[str, Any]:
    """Project an internal research record into the exact 30-column bidder database layout."""
    extra = _extra_dict(row)
    bidder = dict(extra.get("bidder_fields") or {})
    postal = extra.get("postal_address") if isinstance(extra.get("postal_address"), dict) else {}

    city, state, zip_code = _location_parts(str(row.get("location") or ""))
    city = str(bidder.get("city") or postal.get("addressLocality") or city or "").strip()
    state = str(bidder.get("state") or postal.get("addressRegion") or state or "").strip()
    zip_code = str(bidder.get("zip") or postal.get("postalCode") or zip_code or "").strip()

    contractor = str(
        bidder.get("contractor_name")
        or row.get("company")
        or row.get("name")
        or row.get("owner")
        or ""
    ).strip()

    projected = {column: str(bidder.get(column) or "").strip() for column in BIDDER_COLUMNS}
    projected["id"] = bidder.get("id") or row.get("id") or ""
    projected["contractor_name"] = contractor
    projected["address_1"] = str(
        bidder.get("address_1")
        or postal.get("streetAddress")
        or row.get("address")
        or ""
    ).strip()
    projected["city"] = city
    projected["state"] = state
    projected["zip"] = zip_code

    # Preserve the current generic OSHA result only when the source did not already
    # provide the law firm's explicit OSHA Y/N field.
    if not projected["osha"]:
        generic = str(row.get("osha_status") or "").strip().casefold()
        if generic == "none_reported":
            projected["osha"] = "N"
        elif generic in {"open", "closed"}:
            projected["osha"] = "Y"

    projected["_record_id"] = row.get("id") or ""
    return projected
