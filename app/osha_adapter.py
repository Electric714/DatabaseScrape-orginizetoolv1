from __future__ import annotations

import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlencode, urlsplit

from .bidder_schema import normalize_match_text
from .config import get_dol_api_key

DOL_API_BASE = "https://apiprod.dol.gov"
DOL_INSPECTION_PATH = "/v4/get/OSHA/inspection/json"
DOL_VIOLATION_PATH = "/v4/get/OSHA/violation/json"
DOL_INSPECTION_ENDPOINT = f"{DOL_API_BASE}{DOL_INSPECTION_PATH}"
DOL_VIOLATION_ENDPOINT = f"{DOL_API_BASE}{DOL_VIOLATION_PATH}"
OSHA_PUBLIC_SEARCH = "https://www.osha.gov/ords/imis/establishment.html"
API_PAGE_LIMIT = 5000
OSHA_MASTER_FIELDS = ("osha", "osha_severe_violations", "years")

_CORP_SUFFIXES = {
    "inc", "incorporated", "llc", "corp", "corporation", "co", "company",
    "ltd", "limited", "lp", "llp", "pllc",
}
_DECORATION = re.compile(r'[*"#]+')
_LEADING_STATE_ID = re.compile(r"^\s*\d{4,8}\s*-\s*")
_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _collapsed_tokens(value: str) -> list[str]:
    tokens = normalize_match_text(value).split()
    if len(tokens) >= 3 and tokens[-3:] == ["l", "l", "c"]:
        tokens = tokens[:-3] + ["llc"]
    if len(tokens) >= 3 and tokens[-3:] == ["l", "l", "p"]:
        tokens = tokens[:-3] + ["llp"]
    return tokens


def company_core(value: str) -> str:
    """Conservative company-name key for OSHA/DOL identity matching."""
    value = _LEADING_STATE_ID.sub("", value or "")
    tokens = _collapsed_tokens(value)
    while tokens and tokens[-1] in _CORP_SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def clean_search_term(value: str) -> str:
    value = _DECORATION.sub(" ", value or "")
    value = re.sub(r"\s+", " ", value).strip(" ,")
    return value


def contractor_aliases(row: dict) -> list[str]:
    values = [str(row.get("contractor_name") or "").strip()]
    related = str(row.get("related_companies") or "")
    values.extend(part.strip() for part in re.split(r"[;|\n]+", related) if part.strip())
    seen = set()
    aliases = []
    for value in values:
        if not value:
            continue
        key = normalize_match_text(value)
        if key and key not in seen:
            seen.add(key)
            aliases.append(value)
    return aliases


def _filter_object(url: str) -> dict:
    raw = (parse_qs(urlsplit(url).query).get("filter_object") or ["{}"])[0]
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _conditions(node):
    if not isinstance(node, dict):
        return
    if "field" in node:
        yield node
    for group in ("and", "or"):
        values = node.get(group)
        if isinstance(values, list):
            for value in values:
                yield from _conditions(value)


def _filter_value(url: str, field: str) -> str:
    field = field.lower()
    for condition in _conditions(_filter_object(url)):
        if str(condition.get("field") or "").lower() == field:
            return str(condition.get("value") or "")
    return ""


def _query_term(url: str) -> str:
    return _filter_value(url, "estab_name").strip("% ")


def _activity_nr(url: str) -> str:
    return _filter_value(url, "activity_nr").strip()


def _query_limit(url: str) -> int:
    raw = (parse_qs(urlsplit(url).query).get("limit") or [str(API_PAGE_LIMIT)])[0]
    try:
        return max(1, int(raw))
    except ValueError:
        return API_PAGE_LIMIT


def _query_offset(url: str) -> int:
    raw = (parse_qs(urlsplit(url).query).get("offset") or ["0"])[0]
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _with_offset(url: str, offset: int) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["offset"] = [str(offset)]
    pairs = []
    for key, values in query.items():
        for value in values:
            pairs.append((key, value))
    return parts._replace(query=urlencode(pairs)).geturl()


def _api_url(path: str, fields: tuple[str, ...], filter_object: dict, *,
             offset: int = 0, limit: int = API_PAGE_LIMIT, sort_by: str | None = None,
             sort: str = "asc") -> str:
    params = {
        "limit": str(limit),
        "offset": str(offset),
        "fields": ",".join(fields),
        "filter_object": json.dumps(filter_object, separators=(",", ":")),
    }
    if sort_by:
        params["sort_by"] = sort_by
        params["sort"] = sort
    return f"{DOL_API_BASE}{path}?{urlencode(params)}"


def build_inspection_query(term: str, *, offset: int = 0) -> str:
    return _api_url(
        DOL_INSPECTION_PATH,
        (
            "activity_nr", "estab_name", "site_address", "site_city", "site_state",
            "site_zip", "open_date", "close_case_date", "naics_code",
        ),
        {"field": "estab_name", "operator": "like", "value": f"%{term.upper()}%"},
        offset=offset,
        sort_by="open_date",
        sort="desc",
    )


def build_violation_query(activity_nr: str, *, offset: int = 0) -> str:
    return _api_url(
        DOL_VIOLATION_PATH,
        (
            "activity_nr", "citation_id", "delete_flag", "viol_type", "issuance_date",
            "current_penalty", "initial_penalty", "standard", "nr_instances", "nr_exposed",
        ),
        {"field": "activity_nr", "operator": "eq", "value": str(activity_nr)},
        offset=offset,
        sort_by="issuance_date",
        sort="asc",
    )


def _rows(text: str) -> list[dict]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("DOL API returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("DOL API response did not contain a data array")
    rows = []
    for item in payload["data"]:
        if isinstance(item, dict):
            rows.append({str(key).lower(): value for key, value in item.items()})
    return rows


def _string(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _year(value) -> str:
    match = _YEAR.search(_string(value))
    return match.group(0) if match else ""


def _is_deleted(row: dict) -> bool:
    return _string(row.get("delete_flag")).upper() in {"X", "Y", "1", "TRUE", "DELETED"}


def _is_severe(row: dict) -> bool:
    value = normalize_match_text(_string(row.get("viol_type")))
    return value in {"s", "serious", "w", "willful", "r", "repeat", "repeated"}


class OshaEstablishmentAdapter:
    """OSHA enforcement adapter backed by the Department of Labor REST API."""

    query_mode = True
    always_parse = True
    api_source = True
    fail_fast_access_errors = True
    canonical_start_url = DOL_INSPECTION_ENDPOINT
    master_fields = OSHA_MASTER_FIELDS

    def __init__(self):
        self.contractors: dict[str, dict] = {}
        self.term_contexts: dict[str, list[str]] = defaultdict(list)
        self.inspection_context: dict[str, str] = {}
        self.inspections: dict[str, dict[str, dict]] = defaultdict(dict)
        self.violations: dict[str, dict[tuple[str, str], dict]] = defaultdict(dict)
        self.ambiguous_candidates: dict[str, dict[str, dict]] = defaultdict(dict)
        self.query_urls: dict[str, list[str]] = defaultdict(list)

    def request_headers(self, url: str) -> dict[str, str]:
        key = get_dol_api_key()
        if not key:
            raise ValueError(
                "DOL_API_KEY is not configured. Register for the free DOL Open Data API "
                "account, then save the key in the OSHA source setup."
            )
        return {"X-API-KEY": key, "Accept": "application/json"}

    def seed_urls(self, master_rows: list[dict]) -> list[str]:
        # Fail before any scan work if authentication has not been configured.
        self.request_headers(DOL_INSPECTION_ENDPOINT)

        self.contractors.clear()
        self.term_contexts.clear()
        self.inspection_context.clear()
        self.inspections.clear()
        self.violations.clear()
        self.ambiguous_candidates.clear()
        self.query_urls.clear()

        urls = []
        for row in master_rows:
            contractor_name = _string(row.get("contractor_name"))
            if not contractor_name:
                continue
            key = _string(row.get("_master_id") or row.get("id") or normalize_match_text(contractor_name))
            context = dict(row)
            context["_osha_key"] = key
            aliases = contractor_aliases(row)
            context["_osha_aliases"] = aliases
            context["_osha_match_cores"] = sorted({
                company_core(alias) for alias in aliases if company_core(alias)
            })
            self.contractors[key] = context

            terms = []
            for alias in aliases:
                cleaned = clean_search_term(alias)
                core = company_core(alias)
                preferred = core if core and (len(core) >= 8 or " " in core) else cleaned
                if preferred:
                    terms.append(preferred)
            seen_terms = set()
            for term in terms:
                term_key = normalize_match_text(term)
                if not term_key or term_key in seen_terms:
                    continue
                seen_terms.add(term_key)
                if key not in self.term_contexts[term_key]:
                    self.term_contexts[term_key].append(key)
                query_url = build_inspection_query(term)
                urls.append(query_url)
                self.query_urls[key].append(query_url)
        return list(dict.fromkeys(urls))

    def allowed_url(self, url: str) -> bool:
        parts = urlsplit(url)
        if (parts.hostname or "").lower() not in {"apiprod.dol.gov", "api.dol.gov"}:
            return False
        return parts.path.lower() in {
            DOL_INSPECTION_PATH.lower(),
            DOL_VIOLATION_PATH.lower(),
        }

    def _contexts_for_search(self, url: str) -> list[str]:
        return self.term_contexts.get(normalize_match_text(_query_term(url)), [])

    def _candidate_matches(self, candidate_name: str, context_key: str) -> bool:
        candidate = company_core(candidate_name)
        return bool(candidate and candidate in set(
            self.contractors[context_key].get("_osha_match_cores") or []
        ))

    def _candidate_is_plausible(self, candidate_name: str, context_key: str) -> bool:
        candidate = company_core(candidate_name)
        if len(candidate) < 6:
            return False
        for target in self.contractors[context_key].get("_osha_match_cores") or []:
            if not target:
                continue
            if candidate in target or target in candidate:
                return True
            if SequenceMatcher(None, candidate, target).ratio() >= 0.84:
                return True
        return False

    def _location_score(self, row: dict, context_key: str) -> int:
        context = self.contractors[context_key]
        score = 0
        pairs = (
            ("site_state", "state", 4),
            ("site_city", "city", 2),
            ("site_zip", "zip", 3),
            ("site_address", "address_1", 2),
        )
        for source_field, master_field, weight in pairs:
            left = normalize_match_text(_string(row.get(source_field)))
            right = normalize_match_text(_string(context.get(master_field)))
            if master_field == "zip":
                left, right = left[:5], right[:5]
            if left and right and left == right:
                score += weight
        return score

    def _choose_exact_context(self, row: dict, contexts: list[str]) -> str | None:
        matches = [
            key for key in contexts
            if self._candidate_matches(_string(row.get("estab_name")), key)
        ]
        if len(matches) == 1:
            return matches[0]
        if len(matches) <= 1:
            return None
        scored = sorted(
            ((self._location_score(row, key), key) for key in matches),
            reverse=True,
        )
        if scored and scored[0][0] > 0 and (
            len(scored) == 1 or scored[0][0] > scored[1][0]
        ):
            return scored[0][1]
        return None

    def _remember_inspection(self, row: dict, context_key: str, search_url: str) -> str:
        activity_nr = _string(row.get("activity_nr"))
        if not activity_nr:
            return ""
        existing = self.inspection_context.get(activity_nr)
        if existing and existing != context_key:
            # Do not silently attribute one inspection to two bidder records.
            for key in {existing, context_key}:
                self.ambiguous_candidates[key][activity_nr] = {
                    "activity_nr": activity_nr,
                    "establishment_name": _string(row.get("estab_name")),
                    "site_city": _string(row.get("site_city")),
                    "site_state": _string(row.get("site_state")),
                    "site_zip": _string(row.get("site_zip")),
                    "search_url": search_url,
                    "reason": "inspection matched multiple master contractors",
                }
            self.inspection_context.pop(activity_nr, None)
            self.inspections[existing].pop(activity_nr, None)
            return ""
        self.inspection_context[activity_nr] = context_key
        self.inspections[context_key][activity_nr] = {
            "activity_nr": activity_nr,
            "establishment_name": _string(row.get("estab_name")),
            "site_address": _string(row.get("site_address")),
            "site_city": _string(row.get("site_city")),
            "site_state": _string(row.get("site_state")),
            "site_zip": _string(row.get("site_zip")),
            "open_date": _string(row.get("open_date")),
            "close_case_date": _string(row.get("close_case_date")),
            "naics_code": _string(row.get("naics_code")),
            "search_url": search_url,
        }
        return activity_nr

    def links(self, text: str, url: str) -> list[str]:
        path = urlsplit(url).path.lower()
        rows = _rows(text)
        links: list[str] = []

        if path == DOL_INSPECTION_PATH.lower():
            contexts = self._contexts_for_search(url)
            for row in rows:
                name = _string(row.get("estab_name"))
                context_key = self._choose_exact_context(row, contexts)
                if context_key:
                    activity_nr = self._remember_inspection(row, context_key, url)
                    if activity_nr:
                        links.append(build_violation_query(activity_nr))
                    continue
                for key in contexts:
                    if self._candidate_is_plausible(name, key):
                        activity_nr = _string(row.get("activity_nr"))
                        if activity_nr:
                            self.ambiguous_candidates[key][activity_nr] = {
                                "activity_nr": activity_nr,
                                "establishment_name": name,
                                "site_city": _string(row.get("site_city")),
                                "site_state": _string(row.get("site_state")),
                                "site_zip": _string(row.get("site_zip")),
                                "search_url": url,
                                "reason": "similar name requires identity review",
                            }

        # The DOL API caps result size. A full page means another page may exist.
        limit = _query_limit(url)
        if len(rows) >= limit:
            links.append(_with_offset(url, _query_offset(url) + limit))
        return list(dict.fromkeys(links))

    def extract(self, text: str, url: str) -> list[dict]:
        if urlsplit(url).path.lower() != DOL_VIOLATION_PATH.lower():
            return []
        activity_nr = _activity_nr(url)
        context_key = self.inspection_context.get(activity_nr)
        if not context_key:
            return []
        for row in _rows(text):
            row_activity = _string(row.get("activity_nr")) or activity_nr
            if row_activity != activity_nr:
                continue
            citation_id = _string(row.get("citation_id")) or f"row-{len(self.violations[context_key]) + 1}"
            normalized = {
                "activity_nr": activity_nr,
                "citation_id": citation_id,
                "delete_flag": _string(row.get("delete_flag")),
                "viol_type": _string(row.get("viol_type")),
                "issuance_date": _string(row.get("issuance_date")),
                "current_penalty": _string(row.get("current_penalty")),
                "initial_penalty": _string(row.get("initial_penalty")),
                "standard": _string(row.get("standard")),
                "nr_instances": _string(row.get("nr_instances")),
                "nr_exposed": _string(row.get("nr_exposed")),
            }
            self.violations[context_key][(activity_nr, citation_id)] = normalized
        return []

    def finalize_records(self, complete: bool) -> list[dict]:
        records = []
        for key, contractor in self.contractors.items():
            inspections = list(self.inspections.get(key, {}).values())
            ambiguous = list(self.ambiguous_candidates.get(key, {}).values())
            violations = list(self.violations.get(key, {}).values())

            if not inspections and not complete:
                # A partial API run cannot prove that OSHA has no matching record.
                continue

            severe = [row for row in violations if not _is_deleted(row) and _is_severe(row)]
            severe_years = set()
            for row in severe:
                year = _year(row.get("issuance_date"))
                if not year:
                    inspection = self.inspections.get(key, {}).get(_string(row.get("activity_nr")), {})
                    year = _year(inspection.get("open_date"))
                if year:
                    severe_years.add(year)

            inspections.sort(key=lambda item: item.get("open_date") or "")
            latest_date = inspections[-1].get("open_date", "") if inspections else ""
            bidder_id = _string(contractor.get("id"))
            contractor_name = _string(contractor.get("contractor_name"))
            osha_value = "Y" if inspections else ("" if ambiguous else "N")
            severe_value = str(len(severe)) if complete and inspections else ""
            years_value = ", ".join(sorted(severe_years, key=int)) if complete and inspections else ""

            if inspections:
                narrative = (
                    f"DOL OSHA enforcement API matched {len(inspections)} inspection(s) and "
                    f"{len(severe)} current Serious/Willful/Repeat citation(s)."
                )
                if ambiguous:
                    narrative += (
                        f" {len(ambiguous)} additional similar-name result(s) remain unresolved."
                    )
            elif ambiguous:
                names = ", ".join(sorted({
                    item["establishment_name"] for item in ambiguous if item.get("establishment_name")
                })[:8])
                narrative = (
                    "DOL OSHA enforcement API returned similar establishment names that require "
                    "manual identity review before assigning Y/N: " + names
                )
            else:
                narrative = (
                    "No exact-name or plausible similar-name OSHA inspection match was found "
                    "after the targeted DOL API queries completed."
                )

            source_url = (self.query_urls.get(key) or [DOL_INSPECTION_ENDPOINT])[-1]
            records.append({
                "external_id": f"osha:bidder:{bidder_id or normalize_match_text(contractor_name)}",
                "company": contractor_name,
                "date": latest_date,
                "source_url": source_url,
                "osha": osha_value,
                "osha_severe_violations": severe_value,
                "years": years_value,
                "osha_details": narrative,
                "extra": {
                    "source_system": "DOL Open Data API / OSHA enforcement",
                    "api_base": DOL_API_BASE,
                    "api_fields_written_to_master": list(OSHA_MASTER_FIELDS),
                    "identity_fields_used_for_matching_only": [
                        "contractor_name", "related_companies", "address_1", "city", "state", "zip"
                    ],
                    "search_terms": contractor.get("_osha_aliases") or [],
                    "query_count": len(self.query_urls.get(key) or []),
                    "complete_aggregate": bool(complete),
                    "severe_definition": (
                        "Non-deleted OSHA citation rows classified Serious, Willful, or Repeat"
                    ),
                    "osha_inspections": inspections,
                    "osha_severe_citations": severe,
                    "ambiguous_candidates": ambiguous,
                    "public_review_url": OSHA_PUBLIC_SEARCH,
                },
            })
        return records
