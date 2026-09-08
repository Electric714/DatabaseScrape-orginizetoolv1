from __future__ import annotations

import json
from collections import defaultdict
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlencode, urlsplit

from .bidder_schema import normalize_match_text
from .config import get_sam_api_key
from .osha_adapter import company_core, contractor_aliases, clean_search_term

SAM_ALPHA_API_BASE = "https://api-alpha.sam.gov"
SAM_PRODUCTION_API_BASE = "https://api.sam.gov"
SAM_EXCLUSIONS_PATH = "/entity-information/v4/exclusions"
SAM_EXCLUSIONS_ENDPOINT = f"{SAM_ALPHA_API_BASE}{SAM_EXCLUSIONS_PATH}"
SAM_PRODUCTION_EXCLUSIONS_ENDPOINT = f"{SAM_PRODUCTION_API_BASE}{SAM_EXCLUSIONS_PATH}"
SAM_PUBLIC_SEARCH = "https://sam.gov/search/?index=ex"
SAM_MASTER_FIELDS = ("state_federal_debarment",)
SAM_PAGE_SIZE = 10


def _string(value) -> str:
    return "" if value is None else str(value).strip()


def _query_term(url: str) -> str:
    return (parse_qs(urlsplit(url).query).get("exclusionName") or [""])[0].strip()


def _query_page(url: str) -> int:
    raw = (parse_qs(urlsplit(url).query).get("page") or ["0"])[0]
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def _query_size(url: str) -> int:
    raw = (parse_qs(urlsplit(url).query).get("size") or [str(SAM_PAGE_SIZE)])[0]
    try:
        return min(SAM_PAGE_SIZE, max(1, int(raw)))
    except ValueError:
        return SAM_PAGE_SIZE


def _with_page(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["page"] = [str(page)]
    pairs = [(key, value) for key, values in query.items() for value in values]
    return parts._replace(query=urlencode(pairs)).geturl()


def build_exclusions_query(term: str, *, page: int = 0, size: int = SAM_PAGE_SIZE) -> str:
    params = {
        "classification": "Firm",
        "exclusionName": term,
        "recordStatus": "Active",
        "page": str(max(0, page)),
        "size": str(min(SAM_PAGE_SIZE, max(1, size))),
    }
    return f"{SAM_EXCLUSIONS_ENDPOINT}?{urlencode(params)}"


def _payload(text: str) -> dict:
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("SAM.gov Exclusions API returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("SAM.gov Exclusions API returned an unexpected response")
    entities = value.get("excludedEntity")
    if entities is None:
        entities = []
    if not isinstance(entities, list):
        raise ValueError("SAM.gov Exclusions API response did not contain an excludedEntity list")
    value["excludedEntity"] = [item for item in entities if isinstance(item, dict)]
    return value


def _identification(entity: dict) -> dict:
    value = entity.get("exclusionIdentification")
    return value if isinstance(value, dict) else {}


def _details(entity: dict) -> dict:
    value = entity.get("exclusionDetails")
    return value if isinstance(value, dict) else {}


def _address(entity: dict) -> dict:
    for key in ("exclusionPrimaryAddress", "exclusionAddress"):
        value = entity.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _actions(entity: dict) -> list[dict]:
    value = entity.get("exclusionActions")
    if not isinstance(value, dict):
        return []
    actions = value.get("listOfActions")
    return [item for item in actions if isinstance(item, dict)] if isinstance(actions, list) else []


def _entity_name(entity: dict) -> str:
    identification = _identification(entity)
    return _string(identification.get("entityName") or identification.get("name"))


def _entity_key(entity: dict) -> str:
    identification = _identification(entity)
    details = _details(entity)
    actions = _actions(entity)
    action = actions[-1] if actions else {}
    stable = "|".join(
        _string(value)
        for value in (
            identification.get("ueiSAM"),
            identification.get("cageCode"),
            _entity_name(entity),
            details.get("excludingAgencyCode"),
            details.get("exclusionType"),
            action.get("activateDate"),
            action.get("createDate"),
        )
    )
    return normalize_match_text(stable) or normalize_match_text(_entity_name(entity))


def _normalized_entity(entity: dict) -> dict:
    identification = _identification(entity)
    details = _details(entity)
    address = _address(entity)
    actions = _actions(entity)
    return {
        "entity_name": _entity_name(entity),
        "uei_sam": _string(identification.get("ueiSAM")),
        "cage_code": _string(identification.get("cageCode")),
        "classification_type": _string(details.get("classificationType")),
        "exclusion_type": _string(details.get("exclusionType")),
        "exclusion_program": _string(details.get("exclusionProgram")),
        "excluding_agency_code": _string(details.get("excludingAgencyCode")),
        "excluding_agency_name": _string(details.get("excludingAgencyName")),
        "address_line_1": _string(address.get("addressLine1")),
        "address_line_2": _string(address.get("addressLine2")),
        "city": _string(address.get("city")),
        "state": _string(address.get("stateOrProvinceCode")),
        "zip": _string(address.get("zipCode")),
        "country": _string(address.get("countryCode")),
        "actions": [
            {
                "create_date": _string(action.get("createDate")),
                "update_date": _string(action.get("updateDate")),
                "activate_date": _string(action.get("activateDate")),
                "termination_date": _string(action.get("terminationDate")),
                "termination_type": _string(action.get("terminationType")),
                "record_status": _string(action.get("recordStatus")),
            }
            for action in actions
        ],
    }


class SamExclusionsAdapter:
    """Built-in SAM.gov federal debarment adapter using the documented v4 Alpha API."""

    query_mode = True
    always_parse = True
    api_source = True
    fail_fast_access_errors = True
    canonical_start_url = SAM_EXCLUSIONS_ENDPOINT
    master_fields = SAM_MASTER_FIELDS

    def __init__(self):
        self.contractors: dict[str, dict] = {}
        self.term_contexts: dict[str, list[str]] = defaultdict(list)
        self.matches: dict[str, dict[str, dict]] = defaultdict(dict)
        self.ambiguous: dict[str, dict[str, dict]] = defaultdict(dict)
        self.query_urls: dict[str, list[str]] = defaultdict(list)

    def request_url(self, url: str) -> str:
        key = get_sam_api_key()
        if not key:
            raise ValueError(
                "SAM_API_KEY is not configured. Add the SAM.gov Alpha/test API key "
                "from the built-in Federal Debarment source."
            )
        parts = urlsplit(url)
        query = parse_qs(parts.query, keep_blank_values=True)
        query["api_key"] = [key]
        pairs = [(name, value) for name, values in query.items() for value in values]
        return parts._replace(query=urlencode(pairs)).geturl()

    def seed_urls(self, master_rows: list[dict]) -> list[str]:
        # Fail before any scan work if authentication has not been configured.
        self.request_url(SAM_EXCLUSIONS_ENDPOINT)

        self.contractors.clear()
        self.term_contexts.clear()
        self.matches.clear()
        self.ambiguous.clear()
        self.query_urls.clear()

        urls: list[str] = []
        for row in master_rows:
            contractor_name = _string(row.get("contractor_name"))
            if not contractor_name:
                continue
            key = _string(row.get("_master_id") or row.get("id") or normalize_match_text(contractor_name))
            context = dict(row)
            aliases = contractor_aliases(row)
            context["_sam_aliases"] = aliases
            context["_sam_match_cores"] = sorted({company_core(alias) for alias in aliases if company_core(alias)})
            self.contractors[key] = context

            seen_terms = set()
            for alias in aliases:
                core = company_core(alias)
                term = core if core and (len(core) >= 6 or " " in core) else clean_search_term(alias)
                term_key = normalize_match_text(term)
                if not term_key or term_key in seen_terms:
                    continue
                seen_terms.add(term_key)
                if key not in self.term_contexts[term_key]:
                    self.term_contexts[term_key].append(key)
                query_url = build_exclusions_query(term)
                self.query_urls[key].append(query_url)
                urls.append(query_url)
        return list(dict.fromkeys(urls))

    def allowed_url(self, url: str) -> bool:
        parts = urlsplit(url)
        return (
            (parts.hostname or "").lower() in {"api-alpha.sam.gov", "api.sam.gov"}
            and parts.path.lower() == SAM_EXCLUSIONS_PATH.lower()
        )

    def _contexts(self, url: str) -> list[str]:
        return self.term_contexts.get(normalize_match_text(_query_term(url)), [])

    def _exact(self, entity_name: str, context_key: str) -> bool:
        candidate = company_core(entity_name)
        return bool(candidate and candidate in set(self.contractors[context_key].get("_sam_match_cores") or []))

    def _plausible(self, entity_name: str, context_key: str) -> bool:
        candidate = company_core(entity_name)
        if len(candidate) < 6:
            return False
        for target in self.contractors[context_key].get("_sam_match_cores") or []:
            if not target:
                continue
            if candidate in target or target in candidate:
                return True
            if SequenceMatcher(None, candidate, target).ratio() >= 0.86:
                return True
        return False

    def _location_score(self, entity: dict, context_key: str) -> int:
        source = _address(entity)
        target = self.contractors[context_key]
        score = 0
        for source_field, master_field, weight in (
            ("stateOrProvinceCode", "state", 4),
            ("city", "city", 2),
            ("zipCode", "zip", 3),
            ("addressLine1", "address_1", 2),
        ):
            left = normalize_match_text(_string(source.get(source_field)))
            right = normalize_match_text(_string(target.get(master_field)))
            if master_field == "zip":
                left, right = left[:5], right[:5]
            if left and right and left == right:
                score += weight
        return score

    def _choose_context(self, entity: dict, contexts: list[str]) -> str | None:
        exact = [key for key in contexts if self._exact(_entity_name(entity), key)]
        if len(exact) == 1:
            return exact[0]
        if len(exact) <= 1:
            return None
        scored = sorted(((self._location_score(entity, key), key) for key in exact), reverse=True)
        if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][1]
        return None

    def links(self, text: str, url: str) -> list[str]:
        payload = _payload(text)
        contexts = self._contexts(url)
        for entity in payload["excludedEntity"]:
            context_key = self._choose_context(entity, contexts)
            key = _entity_key(entity)
            normalized = _normalized_entity(entity)
            normalized["search_url"] = url
            if context_key and key:
                self.matches[context_key][key] = normalized
                continue
            for candidate_key in contexts:
                if self._plausible(_entity_name(entity), candidate_key):
                    self.ambiguous[candidate_key][key or normalize_match_text(_entity_name(entity))] = {
                        **normalized,
                        "reason": "similar SAM.gov firm name requires identity review",
                    }

        total = payload.get("totalRecords")
        try:
            total_records = max(0, int(total or 0))
        except (TypeError, ValueError):
            total_records = len(payload["excludedEntity"])
        page = _query_page(url)
        size = _query_size(url)
        if (page + 1) * size < min(total_records, 10000):
            return [_with_page(url, page + 1)]
        return []

    def extract(self, text: str, url: str) -> list[dict]:
        # Search pages are accumulated and emitted as one contractor-level record
        # during finalization so the combined state/federal bidder field is never
        # rewritten once per exclusion row.
        return []

    def finalize_records(self, complete: bool) -> list[dict]:
        records: list[dict] = []
        for key, contractor in self.contractors.items():
            matches = list(self.matches.get(key, {}).values())
            ambiguous = list(self.ambiguous.get(key, {}).values())
            if not matches and not ambiguous and not complete:
                continue

            contractor_name = _string(contractor.get("contractor_name"))
            bidder_id = _string(contractor.get("id"))
            if matches:
                narrative = (
                    f"SAM.gov returned {len(matches)} active federal exclusion record(s) "
                    "that exactly matched the bidder or a listed related company."
                )
            elif ambiguous:
                narrative = (
                    f"SAM.gov returned {len(ambiguous)} similar active firm exclusion result(s) "
                    "that require manual identity review."
                )
            else:
                narrative = (
                    "No exact active federal exclusion match was found in the completed SAM.gov "
                    "Alpha/test API queries. The combined state/federal master field remains "
                    "unchanged because state debarment still requires separate research."
                )

            # SAM proves a positive federal exclusion. It cannot by itself prove the
            # combined state_federal_debarment field is negative.
            combined_value = "Y" if matches else ""
            latest = ""
            for match in matches:
                for action in match.get("actions") or []:
                    latest = max(latest, action.get("update_date") or action.get("activate_date") or "")

            source_url = (self.query_urls.get(key) or [SAM_EXCLUSIONS_ENDPOINT])[-1]
            records.append({
                "external_id": f"sam:bidder:{bidder_id or normalize_match_text(contractor_name)}",
                "company": contractor_name,
                "date": latest,
                "source_url": source_url,
                "state_federal_debarment": combined_value,
                "extra": {
                    "source_system": "SAM.gov Exclusions API v4 Alpha/test",
                    "api_endpoint": SAM_EXCLUSIONS_ENDPOINT,
                    "api_fields_written_to_master": list(SAM_MASTER_FIELDS),
                    "federal_component_only": True,
                    "negative_result_writes_combined_field": False,
                    "search_terms": contractor.get("_sam_aliases") or [],
                    "query_count": len(self.query_urls.get(key) or []),
                    "complete_aggregate": bool(complete),
                    "active_federal_exclusions": matches,
                    "ambiguous_candidates": ambiguous,
                    "narrative": narrative,
                    "public_review_url": SAM_PUBLIC_SEARCH,
                },
            })
        return records
