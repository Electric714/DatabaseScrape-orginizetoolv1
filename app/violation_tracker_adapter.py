"""Fail-closed Violation Tracker public HTML adapter.

Only free public search/detail pages are used. Search/display access may be blocked
in some environments; blocked or incomplete retrieval remains UNKNOWN. The
adapter is positive-only: it can propose Y from exact supported evidence, but it
never writes N from absence.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from .identity import location_corroborates
from .osha_adapter import company_core, contractor_aliases

VT_URL = "https://violationtracker.goodjobsfirst.org/"
VT_FIELDS = ("environmental_violations", "prevailing_wage_violations", "misc_violations")

_RESULT_COUNT = re.compile(r"([\d,]+)\s+Violation\s+Tracker\s+results?\s+found", re.I)
_DETAIL_PATH = re.compile(r"^/violation-tracker/[^/?#]+/?$", re.I)

_ENVIRONMENTAL_OFFENSES = {
    "air pollution violation",
    "water pollution violation",
    "environmental violation",
    "hazardous waste violation",
    "oil spill",
    "drinking water violation",
    "wetlands violation",
    "toxic substances violation",
}
_MISC_OFFENSES = {
    "false claims act and related",
    "government contracting violation",
    "bribery",
    "price-fixing or anti-competitive practices",
}
_PREVAILING_WAGE_MARKERS = (
    "prevailing wage",
    "davis-bacon",
    "davis bacon",
    "public works wage",
)


def _string(value) -> str:
    return "" if value is None else str(value).strip()


def _normalized_label(value: str) -> str:
    return re.sub(r"\s+", " ", _string(value).rstrip(":")).casefold()


def offense_field(offense, offense_group="", description=""):
    """Map only explicitly supported offense evidence to owned master fields."""
    value = _string(offense).casefold()
    group = _string(offense_group).casefold()
    description_text = _string(description).casefold()

    if value in _ENVIRONMENTAL_OFFENSES or group == "environment-related offenses":
        return "environmental_violations"
    if any(marker in value or marker in description_text for marker in _PREVAILING_WAGE_MARKERS):
        return "prevailing_wage_violations"
    if value in _MISC_OFFENSES:
        return "misc_violations"
    # Deliberately unsupported: generic wage-and-hour findings are not enough to
    # establish a prevailing-wage violation.
    return None


def _search_url(term: str, *, page: int | None = None) -> str:
    params = {"company": term}
    if page is not None and page > 0:
        params["page"] = str(page)
    return VT_URL + "?" + urlencode(params)


def _query_term(url: str) -> str:
    return (parse_qs(urlsplit(url).query).get("company") or [""])[0].strip()


def _is_detail_url(url: str) -> bool:
    parts = urlsplit(urljoin(VT_URL, url))
    return (parts.hostname or "").lower() == urlsplit(VT_URL).hostname and bool(_DETAIL_PATH.match(parts.path))


def _headers(table) -> list[str]:
    row = table.find("tr")
    if not row:
        return []
    return [_normalized_label(cell.get_text(" ", strip=True)) for cell in row.find_all(["th", "td"], recursive=False)]


def _search_rows(soup: BeautifulSoup, page_url: str) -> list[dict]:
    """Parse the public results table by header names, not CSS classes."""
    for table in soup.find_all("table"):
        headers = _headers(table)
        if "company" not in headers or "primary offense type" not in headers:
            continue
        rows: list[dict] = []
        for tr in table.find_all("tr")[1:]:
            cells = tr.find_all(["th", "td"], recursive=False)
            if not cells:
                continue
            values = [cell.get_text(" ", strip=True) for cell in cells]
            if len(values) < len(headers):
                values.extend([""] * (len(headers) - len(values)))
            mapped = dict(zip(headers, values))
            company_index = headers.index("company")
            company_cell = cells[company_index] if company_index < len(cells) else None
            detail_url = ""
            if company_cell:
                anchor = company_cell.find("a", href=True)
                if anchor:
                    target = urljoin(page_url, anchor["href"])
                    if _is_detail_url(target):
                        detail_url = target
            if not mapped.get("company"):
                continue
            rows.append({
                "company": mapped.get("company", ""),
                "parent": mapped.get("current parent", ""),
                "parent_industry": mapped.get("current parent industry", ""),
                "offense": mapped.get("primary offense type", ""),
                "year": mapped.get("year", ""),
                "agency": mapped.get("agency", ""),
                "penalty": mapped.get("penalty amount", ""),
                "detail_url": detail_url,
            })
        return rows
    return []


def _next_page(soup: BeautifulSoup, url: str, term: str) -> str | None:
    current = 0
    raw_page = (parse_qs(urlsplit(url).query).get("page") or ["0"])[0]
    try:
        current = max(0, int(raw_page))
    except ValueError:
        current = 0

    numeric_fallback = None
    for anchor in soup.find_all("a", href=True):
        target = urljoin(url, anchor["href"])
        parts = urlsplit(target)
        if (parts.hostname or "").lower() != urlsplit(VT_URL).hostname or parts.path not in {"", "/"}:
            continue
        query = parse_qs(parts.query)
        if (query.get("company") or [""])[0].strip() != term:
            continue
        label = " ".join([
            anchor.get_text(" ", strip=True),
            _string(anchor.get("aria-label")),
            " ".join(anchor.get("rel") or []),
        ]).casefold()
        if "next" in label or label.strip() == ">":
            return target
        raw = (query.get("page") or [""])[0]
        try:
            page = int(raw)
        except ValueError:
            continue
        if page == current + 1:
            numeric_fallback = numeric_fallback or target
    return numeric_fallback


def _detail_fields(soup: BeautifulSoup) -> dict[str, str]:
    """Read labeled fields from current and legacy public detail layouts."""
    fields: dict[str, str] = {}

    for tr in soup.find_all("tr"):
        cells = tr.find_all(["th", "td"], recursive=False)
        if len(cells) == 2:
            label = _normalized_label(cells[0].get_text(" ", strip=True))
            value = cells[1].get_text(" ", strip=True)
            if label and value and label not in fields:
                fields[label] = value

    for dt in soup.find_all("dt"):
        dd = dt.find_next_sibling("dd")
        if dd:
            label = _normalized_label(dt.get_text(" ", strip=True))
            value = dd.get_text(" ", strip=True)
            if label and value and label not in fields:
                fields[label] = value

    lines = [line.strip() for line in soup.get_text("\n", strip=True).splitlines() if line.strip()]
    pending = ""
    for line in lines:
        if pending:
            if ":" not in line and line:
                fields.setdefault(pending, line)
                pending = ""
                continue
            pending = ""
        match = re.match(r"^([A-Za-z][A-Za-z0-9 /&()'\-]{1,80}):\s*(.*)$", line)
        if not match:
            continue
        label = _normalized_label(match.group(1))
        value = match.group(2).strip()
        if value:
            fields.setdefault(label, value)
        else:
            pending = label
    return fields


def _candidate_location(fields: dict[str, str]) -> dict:
    return {
        "address": fields.get("address", ""),
        "city": fields.get("city", ""),
        "state": fields.get("state", ""),
        "zip": fields.get("zip", fields.get("zip code", fields.get("postal code", ""))),
    }


class ViolationTrackerAdapter:
    query_mode = True
    always_parse = True
    # One blocked contractor/search must not invalidate unrelated contractors.
    fail_fast_access_errors = False
    canonical_start_url = VT_URL
    master_fields = VT_FIELDS

    def __init__(self):
        self.contractors = []
        self.contexts = defaultdict(list)
        self.alias_owners = defaultdict(set)
        self.query_urls = defaultdict(list)
        self.expected = {}
        self.seen_records = defaultdict(set)
        self.completed_terms = set()
        self.term_errors = defaultdict(list)
        self.detail_contexts = defaultdict(list)
        self.required_details = defaultdict(set)
        self.processed_details = set()
        self.matches = defaultdict(list)
        self.unresolved = defaultdict(list)
        self.page_failures = defaultdict(list)

    def seed_urls(self, master_rows):
        self.__init__()
        self.contractors = [dict(row) for row in master_rows]
        urls = []
        seen_terms = set()
        for index, row in enumerate(self.contractors):
            row_terms = set()
            for alias in contractor_aliases(row):
                term = company_core(alias)
                if not term:
                    continue
                row_terms.add(term)
                self.alias_owners[term].add(index)
                if index not in self.contexts[term]:
                    self.contexts[term].append(index)
            for term in sorted(row_terms):
                query = _search_url(term)
                self.query_urls[index].append(query)
                if term not in seen_terms:
                    seen_terms.add(term)
                    urls.append(query)
        return urls

    def allowed_url(self, url):
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host != urlsplit(VT_URL).hostname:
            return False
        if parts.path in {"", "/"}:
            query = parse_qs(parts.query)
            return bool((query.get("company") or [""])[0].strip())
        return bool(_DETAIL_PATH.match(parts.path)) and not parts.query

    def record_page_error(self, url: str, error: str):
        """Optional crawler hook used to retain fail-closed acquisition provenance."""
        message = _string(error)
        lowered = message.casefold()
        category = "blocked" if (
            "http 401" in lowered or "http 403" in lowered or "http 429" in lowered
            or "access challenge" in lowered or "captcha" in lowered
        ) else "fetch_error"
        term = _query_term(url)
        indices = set(self.contexts.get(term, [])) if term else set()
        if _is_detail_url(url):
            indices.update(context["index"] for context in self.detail_contexts.get(url, []))
        if not indices:
            indices.update(range(len(self.contractors)))
        stage = "detail" if _is_detail_url(url) else "search"
        for index in indices:
            self.page_failures[index].append({
                "url": url,
                "stage": stage,
                "category": category,
                "reason": message[:500],
            })
            if term:
                self.term_errors[term].append(message[:500])

    def _remember_unresolved(self, index: int, row: dict, reason: str, term: str):
        item = {
            "company": row.get("company", ""),
            "current_parent": row.get("parent", ""),
            "primary_offense": row.get("offense", ""),
            "year": row.get("year", ""),
            "agency": row.get("agency", ""),
            "penalty": row.get("penalty", ""),
            "source_url": row.get("detail_url", ""),
            "search_term": term,
            "identity_decision": "unresolved",
            "reason": reason,
        }
        if item not in self.unresolved[index]:
            self.unresolved[index].append(item)

    def _search_page(self, html: str, url: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        text = soup.get_text(" ", strip=True)
        term = _query_term(url)
        if not term or term not in self.contexts:
            raise ValueError("Violation Tracker search context was not recognized; UNKNOWN")
        count = _RESULT_COUNT.search(text)
        if not count:
            raise ValueError("Violation Tracker search structure unrecognized; UNKNOWN")
        expected = int(count.group(1).replace(",", ""))
        prior = self.expected.get(term)
        if prior is not None and prior != expected:
            raise ValueError("Violation Tracker result count changed during pagination; UNKNOWN")
        self.expected[term] = expected

        rows = _search_rows(soup, url)
        if expected and not rows:
            raise ValueError("Violation Tracker results table layout unrecognized; UNKNOWN")

        links = []
        for row in rows:
            detail_url = row.get("detail_url") or ""
            if detail_url:
                self.seen_records[term].add(detail_url)
            company = company_core(row.get("company", ""))
            parent = company_core(row.get("parent", ""))
            for index in self.contexts[term]:
                aliases = {company_core(alias) for alias in contractor_aliases(self.contractors[index]) if company_core(alias)}
                if company and company in aliases:
                    if not detail_url:
                        self._remember_unresolved(index, row, "exact company row had no public detail link", term)
                        continue
                    context = {"index": index, "term": term, "search_row": dict(row)}
                    if context not in self.detail_contexts[detail_url]:
                        self.detail_contexts[detail_url].append(context)
                    self.required_details[index].add(detail_url)
                    links.append(detail_url)
                elif parent and parent in aliases:
                    self._remember_unresolved(
                        index, row,
                        "current parent matched the master, but the violating company did not; parent-only evidence cannot prove subsidiary identity",
                        term,
                    )
                elif company:
                    ratio = max((SequenceMatcher(None, company, alias).ratio() for alias in aliases), default=0.0)
                    if ratio >= 0.88:
                        self._remember_unresolved(index, row, "similar company name requires manual identity review", term)

        next_url = _next_page(soup, url, term)
        if next_url:
            links.append(next_url)
        else:
            if len(self.seen_records[term]) == expected:
                self.completed_terms.add(term)
            else:
                self.term_errors[term].append(
                    f"result count mismatch: expected {expected}, observed {len(self.seen_records[term])}"
                )
        return list(dict.fromkeys(links))

    def _detail_page(self, html: str, url: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        fields = _detail_fields(soup)
        company_name = fields.get("company", "")
        offense = fields.get("primary offense", "")
        if not company_name or not offense:
            raise ValueError("Violation Tracker detail layout unverified; UNKNOWN")

        candidate = {
            "name": company_name,
            "parent": fields.get("current parent company", fields.get("current parent", "")),
            "parent_at_time": fields.get("parent at the time of the penalty announcement", ""),
            "offense_group": fields.get("offense group", ""),
            "offense": offense,
            "secondary_offense": fields.get("secondary offense", ""),
            "description": fields.get("violation description", ""),
            "agency": fields.get("agency", ""),
            "penalty": fields.get("penalty", fields.get("penalty amount", "")),
            "year": fields.get("year", ""),
            "date": fields.get("date", ""),
            "level_of_government": fields.get("level of government", ""),
            "action_type": fields.get("action type", ""),
            "source_url": url,
            **_candidate_location(fields),
        }
        company = company_core(candidate["name"])
        parent = company_core(candidate["parent"])

        for context in self.detail_contexts.get(url, []):
            index = context["index"]
            aliases = {company_core(alias) for alias in contractor_aliases(self.contractors[index]) if company_core(alias)}
            exact_company = bool(company and company in aliases)
            parent_only = bool(parent and parent in aliases and not exact_company)
            has_location = any(candidate.get(key) for key in ("address", "city", "state", "zip"))
            location_match = location_corroborates(candidate, self.contractors[index]) if has_location else False
            unique_alias_owner = exact_company and len(self.alias_owners.get(company, set())) == 1

            if exact_company and (location_match or (not has_location and unique_alias_owner)):
                field = offense_field(candidate["offense"], candidate["offense_group"], candidate["description"])
                self.matches[index].append({
                    **candidate,
                    "search_term": context["term"],
                    "identity_decision": "confirmed_exact_company",
                    "identity_basis": "exact company name plus corroborating location" if location_match else "exact company name uniquely maps to one selected master contractor and the source supplied no contractor location",
                    "location_corroborated": bool(location_match),
                    "master_field": field or "",
                    "classification_decision": "supported" if field else "unsupported_offense",
                    "classification_reason": (
                        f"primary offense maps to {field}" if field else
                        "primary offense does not explicitly support an owned master field"
                    ),
                })
            elif parent_only:
                self._remember_unresolved(index, context["search_row"], "parent-company identity alone is insufficient", context["term"])
            else:
                self.unresolved[index].append({
                    **candidate,
                    "search_term": context["term"],
                    "identity_decision": "unresolved",
                    "reason": "detail company identity did not meet conservative exact-company/location rules",
                })
        self.processed_details.add(url)
        return []

    def links(self, html, url):
        if urlsplit(url).path in {"", "/"}:
            return self._search_page(html, url)
        if _is_detail_url(url):
            return self._detail_page(html, url)
        return []

    def extract(self, html, url):
        return []

    def _terms_for(self, index: int) -> list[str]:
        return list(dict.fromkeys(
            company_core(alias) for alias in contractor_aliases(self.contractors[index]) if company_core(alias)
        ))

    def finalize_records(self, complete):
        results = []
        for index, row in enumerate(self.contractors):
            terms = self._terms_for(index)
            searches_complete = bool(terms) and all(term in self.completed_terms for term in terms)
            details_complete = self.required_details[index].issubset(self.processed_details)
            contractor_complete = searches_complete and details_complete and not self.page_failures[index]

            fields = {field: "" for field in VT_FIELDS}
            for candidate in self.matches[index]:
                field = candidate.get("master_field")
                if field in fields:
                    fields[field] = "Y"

            blocked = any(item.get("category") == "blocked" for item in self.page_failures[index])
            if blocked:
                acquisition_status = "blocked"
            elif self.page_failures[index] or not contractor_complete:
                acquisition_status = "partial"
            elif any(fields.values()):
                acquisition_status = "verified_positive"
            elif self.unresolved[index]:
                acquisition_status = "complete_with_unresolved_identity"
            else:
                acquisition_status = "complete_no_supported_positive"

            bidder_id = _string(row.get("id"))
            master_key = _string(row.get("_master_id") or bidder_id or index)
            source_url = self.query_urls[index][0] if self.query_urls[index] else VT_URL
            search_status = {
                term: {
                    "expected_results": self.expected.get(term),
                    "observed_result_links": len(self.seen_records.get(term, set())),
                    "complete": term in self.completed_terms,
                    "errors": list(dict.fromkeys(self.term_errors.get(term, []))),
                }
                for term in terms
            }

            results.append({
                "external_id": "vt:bidder:" + master_key,
                "bidder_id": bidder_id,
                "company": _string(row.get("contractor_name")),
                "source_url": source_url,
                **fields,
                "extra": {
                    "master_id": row.get("_master_id"),
                    "source_system": "Good Jobs First Violation Tracker public HTML",
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                    "complete_aggregate": bool(contractor_complete),
                    "global_scan_complete": bool(complete),
                    "acquisition_status": acquisition_status,
                    "search_terms": terms,
                    "search_status": search_status,
                    "matched_records": self.matches[index],
                    "unresolved_candidates": self.unresolved[index],
                    "page_failures": self.page_failures[index],
                    "api_fields_written_to_master": list(VT_FIELDS),
                    "field_semantics": "positive-only; absence, blocked access, unsupported offenses, and unresolved identity remain UNKNOWN",
                    "parent_company_match_rule": "parent-company identity alone never proves the selected contractor",
                    "prevailing_wage_rule": "generic wage-and-hour violations are not prevailing-wage violations without explicit prevailing-wage/Davis-Bacon/public-works-wage evidence",
                    "narrative": (
                        "Supported exact-company Violation Tracker evidence may propose Y. "
                        "No Violation Tracker search outcome proposes N, and blocked/partial retrieval remains UNKNOWN."
                    ),
                    "coverage": "Free public search/display pages only; subscriber-only downloads and fields are not accessed",
                },
            })
        return results
