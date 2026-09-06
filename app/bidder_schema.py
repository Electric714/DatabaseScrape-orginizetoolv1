import csv
import io
import json
import re
import unicodedata
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


def bidder_row(row: dict[str, Any], fallback_id: bool = True) -> dict[str, Any]:
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
    projected["id"] = bidder.get("id") or (row.get("id") if fallback_id else "") or ""
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


BIDDER_DB_COLUMNS = ["bidder_id", *BIDDER_COLUMNS[1:]]
COMPLIANCE_COLUMNS = BIDDER_COLUMNS[11:]


def normalize_match_text(value: Any) -> str:
    """Conservative normalization for matching the same contractor across sources."""
    text = unicodedata.normalize("NFKC", str(value or "")).casefold()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def bidder_values_equal(left: Any, right: Any) -> bool:
    """Avoid noisy proposals caused only by case, punctuation, or whitespace."""
    return normalize_match_text(left) == normalize_match_text(right)


def bidder_master_row(row: dict[str, Any]) -> dict[str, Any]:
    """Return a stored master row using the law firm's exact external column names."""
    result = {column: "" for column in BIDDER_COLUMNS}
    result["id"] = str(row.get("bidder_id") or "")
    for column in BIDDER_COLUMNS[1:]:
        result[column] = str(row.get(column) or "")
    result["_master_id"] = row.get("pk") or row.get("_master_id") or ""
    result["_updated_at"] = row.get("updated_at") or ""
    result["_source_import_id"] = row.get("source_import_id") or ""
    return result


def parse_bidder_csv(data: bytes) -> tuple[list[dict[str, str]], list[str]]:
    """Parse a bidder CSV without coercing ZIP codes or identifiers to numbers."""
    if not data:
        raise ValueError("The CSV file is empty.")
    if len(data) > 20 * 1024 * 1024:
        raise ValueError("The CSV file is larger than the 20 MB import limit.")
    decoded = None
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            decoded = data.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError("The CSV could not be decoded as UTF-8 or Windows-1252.")

    reader = csv.DictReader(io.StringIO(decoded, newline=""))
    if not reader.fieldnames:
        raise ValueError("The CSV does not contain a header row.")

    normalized_headers = {str(name or "").strip().casefold(): str(name or "") for name in reader.fieldnames}
    missing = [column for column in BIDDER_COLUMNS if column.casefold() not in normalized_headers]
    if missing:
        raise ValueError("Missing bidder database columns: " + ", ".join(missing))

    extras = [
        original for folded, original in normalized_headers.items()
        if folded not in {column.casefold() for column in BIDDER_COLUMNS}
    ]
    warnings = []
    if extras:
        warnings.append("Ignored extra columns: " + ", ".join(extras))

    header_lookup = {column: normalized_headers[column.casefold()] for column in BIDDER_COLUMNS}
    rows: list[dict[str, str]] = []
    for line_number, raw in enumerate(reader, start=2):
        row = {column: clean_csv_cell(raw.get(header_lookup[column])) for column in BIDDER_COLUMNS}
        if not any(row.values()):
            continue
        if not row["contractor_name"]:
            warnings.append(f"Row {line_number} was skipped because contractor_name is blank.")
            continue
        rows.append(row)
    if not rows:
        raise ValueError("The CSV contains no bidder rows with a contractor_name.")
    return rows, warnings


def clean_csv_cell(value: Any) -> str:
    if value is None:
        return ""
    return str(value).replace("\x00", "").strip()
