from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup

from .bidder_schema import normalize_match_text

OSHA_SEARCH_PATH = "/ords/imis/establishment.search"
OSHA_DETAIL_PATH = "/ords/imis/establishment.inspection_detail"
OSHA_BASE = "https://www.osha.gov"

_CORP_SUFFIXES = {
    "inc", "incorporated", "llc", "corp", "corporation", "co", "company",
    "ltd", "limited", "lp", "llp", "pllc",
}
_DECORATION = re.compile(r'[*"#]+')
_LEADING_STATE_ID = re.compile(r"^\s*\d{4,8}\s*-\s*")
_DATE_OPENED = re.compile(r"Date\s+Opened:\s*(\d{1,2}/\d{1,2}/\d{4})", re.I)
_INSPECTION_HEADING = re.compile(r"Inspection:\s*([0-9]+)\s*-\s*(.+)", re.I)
_INT = re.compile(r"-?\d+")


def _collapsed_tokens(value: str) -> list[str]:
    tokens = normalize_match_text(value).split()
    # OSHA occasionally renders L.L.C. as separate tokens.
    if len(tokens) >= 3 and tokens[-3:] == ["l", "l", "c"]:
        tokens = tokens[:-3] + ["llc"]
    if len(tokens) >= 3 and tokens[-3:] == ["l", "l", "p"]:
        tokens = tokens[:-3] + ["llp"]
    return tokens


def company_core(value: str) -> str:
    """Conservative company-name key used only for exact OSHA result matching."""
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


def year_windows(today: date | None = None) -> list[tuple[date, date]]:
    """OSHA's establishment help caps a single search window at ten years."""
    today = today or date.today()
    windows = []
    start_year = 1972
    while start_year <= today.year:
        end_year = min(start_year + 9, today.year)
        end = today if end_year == today.year else date(end_year, 12, 31)
        windows.append((date(start_year, 1, 1), end))
        start_year = end_year + 1
    return windows


def build_search_url(term: str, start: date, end: date) -> str:
    params = {
        "establishment": term,
        "state": "all",
        "office": "all",
        "officetype": "all",
        "sitezip": "100000",
        "p_case": "all",
        "p_violations_exist": "both",
        "p_sort": "7",
        "p_desc": "DESC",
        "startmonth": f"{start.month:02d}",
        "startday": f"{start.day:02d}",
        "startyear": str(start.year),
        "endmonth": f"{end.month:02d}",
        "endday": f"{end.day:02d}",
        "endyear": str(end.year),
    }
    return f"{OSHA_BASE}{OSHA_SEARCH_PATH}?{urlencode(params)}"


def _query_term(url: str) -> str:
    return (parse_qs(urlsplit(url).query).get("establishment") or [""])[0]


def _inspection_id(url: str) -> str:
    return (parse_qs(urlsplit(url).query).get("id") or [""])[0]


def _table_headers(table) -> list[str]:
    first = table.find("tr")
    if not first:
        return []
    return [normalize_match_text(cell.get_text(" ", strip=True)) for cell in first.find_all(["th", "td"])]


def _to_int(value: str) -> int:
    match = _INT.search(value or "")
    return int(match.group(0)) if match else 0


def parse_violation_summary(soup: BeautifulSoup) -> dict[str, int]:
    for table in soup.find_all("table"):
        headers = _table_headers(table)
        if not headers or "serious" not in headers or "repeat" not in headers or "total" not in headers:
            continue
        index = {name: pos for pos, name in enumerate(headers)}
        for row in table.find_all("tr")[1:]:
            cells = [cell.get_text(" ", strip=True) for cell in row.find_all(["td", "th"])]
            if not cells or normalize_match_text(cells[0]) != "current violations":
                continue
            return {
                "serious": _to_int(cells[index["serious"]]) if index.get("serious", 999) < len(cells) else 0,
                "willful": _to_int(cells[index["willful"]]) if index.get("willful", 999) < len(cells) else 0,
                "repeat": _to_int(cells[index["repeat"]]) if index.get("repeat", 999) < len(cells) else 0,
                "other": _to_int(cells[index["other"]]) if index.get("other", 999) < len(cells) else 0,
                "total": _to_int(cells[index["total"]]) if index.get("total", 999) < len(cells) else 0,
            }
    return {"serious": 0, "willful": 0, "repeat": 0, "other": 0, "total": 0}


def parse_inspection_detail(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    heading_text = ""
    for heading in soup.find_all(["h1", "h2", "h3", "h4"]):
        candidate = heading.get_text(" ", strip=True)
        if "Inspection:" in candidate:
            heading_text = candidate
            break
    heading = _INSPECTION_HEADING.search(heading_text or text)
    inspection_id = _inspection_id(url) or (heading.group(1) if heading else "")
    establishment = heading.group(2).strip() if heading else ""
    opened_match = _DATE_OPENED.search(text)
    opened = opened_match.group(1) if opened_match else ""
    violations = parse_violation_summary(soup)
    severe = violations["serious"] + violations["willful"] + violations["repeat"]
    return {
        "inspection_id": inspection_id,
        "establishment_name": establishment,
        "date_opened": opened,
        "severe_current_violations": severe,
        "current_violations": violations,
        "detail_url": url,
    }


class OshaEstablishmentAdapter:
    """Targeted OSHA IMIS/ORDS adapter driven by the imported master bidder database."""

    query_mode = True
    always_parse = True

    def __init__(self):
        self.contractors: dict[str, dict] = {}
        self.term_contexts: dict[str, list[str]] = defaultdict(list)
        self.detail_context: dict[str, str] = {}
        self.inspections: dict[str, dict[str, dict]] = defaultdict(dict)
        self.query_urls: dict[str, list[str]] = defaultdict(list)

    def seed_urls(self, master_rows: list[dict], today: date | None = None) -> list[str]:
        self.contractors.clear()
        self.term_contexts.clear()
        self.detail_context.clear()
        self.inspections.clear()
        self.query_urls.clear()

        urls = []
        windows = year_windows(today)
        for row in master_rows:
            contractor_name = str(row.get("contractor_name") or "").strip()
            if not contractor_name:
                continue
            key = str(row.get("_master_id") or row.get("id") or normalize_match_text(contractor_name))
            context = dict(row)
            context["_osha_key"] = key
            aliases = contractor_aliases(row)
            context["_osha_aliases"] = aliases
            context["_osha_match_cores"] = sorted({company_core(alias) for alias in aliases if company_core(alias)})
            self.contractors[key] = context

            terms = []
            for alias in aliases:
                cleaned = clean_search_term(alias)
                if cleaned:
                    terms.append(cleaned)
                core = company_core(alias)
                # A suffix-free variant catches OSHA punctuation/legal-suffix differences,
                # but avoid making very short numeric names broader than necessary.
                if core and core != normalize_match_text(cleaned) and (len(core) >= 8 or " " in core):
                    terms.append(core)
            deduped_terms = []
            seen_terms = set()
            for term in terms:
                normalized = normalize_match_text(term)
                if normalized and normalized not in seen_terms:
                    seen_terms.add(normalized)
                    deduped_terms.append(term)

            for term in deduped_terms:
                term_key = normalize_match_text(term)
                if key not in self.term_contexts[term_key]:
                    self.term_contexts[term_key].append(key)
                for start, end in windows:
                    query_url = build_search_url(term, start, end)
                    urls.append(query_url)
                    self.query_urls[key].append(query_url)
        return list(dict.fromkeys(urls))

    def allowed_url(self, url: str) -> bool:
        parts = urlsplit(url)
        if (parts.hostname or "").lower() != "www.osha.gov":
            return False
        return parts.path.lower() in {OSHA_SEARCH_PATH, OSHA_DETAIL_PATH}

    def _contexts_for_search(self, url: str) -> list[str]:
        return self.term_contexts.get(normalize_match_text(_query_term(url)), [])

    def _candidate_matches(self, candidate_name: str, context_key: str) -> bool:
        candidate = company_core(candidate_name)
        return bool(candidate and candidate in set(self.contractors[context_key].get("_osha_match_cores") or []))

    def links(self, html: str, url: str) -> list[str]:
        parts = urlsplit(url)
        if parts.path.lower() != OSHA_SEARCH_PATH:
            return []

        soup = BeautifulSoup(html, "lxml")
        contexts = self._contexts_for_search(url)
        links: list[str] = []

        # Follow only detail rows whose establishment name exactly matches one of
        # the master contractor's normalized legal/related names.
        for table in soup.find_all("table"):
            headers = _table_headers(table)
            if "activity" not in headers or "establishment name" not in headers:
                continue
            name_index = headers.index("establishment name")
            for row in table.find_all("tr")[1:]:
                cells = row.find_all(["td", "th"])
                if name_index >= len(cells):
                    continue
                candidate_name = cells[name_index].get_text(" ", strip=True)
                detail = next((a for a in row.find_all("a", href=True) if urlsplit(urljoin(url, a["href"])).path.lower() == OSHA_DETAIL_PATH), None)
                if not detail:
                    continue
                detail_url = urljoin(url, detail["href"])
                inspection_id = _inspection_id(detail_url)
                if not inspection_id:
                    continue
                matched = [key for key in contexts if self._candidate_matches(candidate_name, key)]
                if len(matched) == 1:
                    self.detail_context[inspection_id] = matched[0]
                    links.append(detail_url)

        # OSHA paginates at 20 rows. Follow page navigation for this exact query,
        # but ignore sort links and unrelated site navigation.
        current_term = normalize_match_text(_query_term(url))
        current_query = parse_qs(parts.query)
        for anchor in soup.find_all("a", href=True):
            label = normalize_match_text(anchor.get_text(" ", strip=True))
            if not (label.isdigit() or label in {"next", "prev", "previous"}):
                continue
            candidate = urljoin(url, anchor["href"])
            candidate_parts = urlsplit(candidate)
            if candidate_parts.path.lower() != OSHA_SEARCH_PATH:
                continue
            candidate_query = parse_qs(candidate_parts.query)
            if normalize_match_text((candidate_query.get("establishment") or [""])[0]) != current_term:
                continue
            # Keep date window/case scope pinned to the original targeted search.
            stable = ("startyear", "startmonth", "startday", "endyear", "endmonth", "endday", "p_case", "p_violations_exist")
            if any((candidate_query.get(name) or [""])[0] != (current_query.get(name) or [""])[0] for name in stable):
                continue
            links.append(candidate)

        return list(dict.fromkeys(links))

    def extract(self, html: str, url: str) -> list[dict]:
        if urlsplit(url).path.lower() != OSHA_DETAIL_PATH:
            return []
        inspection_id = _inspection_id(url)
        context_key = self.detail_context.get(inspection_id)
        if not context_key:
            return []
        detail = parse_inspection_detail(html, url)
        if not detail["inspection_id"]:
            return []
        # Re-check the detail-page establishment name against the contractor. A
        # changed/mislinked OSHA result is treated as non-authoritative.
        if detail["establishment_name"] and not self._candidate_matches(detail["establishment_name"], context_key):
            return []
        self.inspections[context_key][detail["inspection_id"]] = detail
        return []

    def finalize_records(self, complete: bool) -> list[dict]:
        records = []
        for key, contractor in self.contractors.items():
            inspections = list(self.inspections.get(key, {}).values())
            if not inspections and not complete:
                # A partial crawl cannot prove an OSHA-negative result.
                continue

            inspections.sort(key=lambda item: item.get("date_opened") or "")
            bidder_id = str(contractor.get("id") or "").strip()
            contractor_name = str(contractor.get("contractor_name") or "").strip()
            severe_total = sum(int(item.get("severe_current_violations") or 0) for item in inspections)
            severe_years = sorted({
                item["date_opened"].split("/")[-1]
                for item in inspections
                if item.get("date_opened") and int(item.get("severe_current_violations") or 0) > 0
            })
            latest_date = inspections[-1]["date_opened"] if inspections else ""
            osha_value = "Y" if inspections else "N"

            # Aggregate counts are only authoritative after every targeted search
            # page/detail path completed. Positive existence is still safe on a
            # partial run; negative existence and totals are not.
            severe_value = str(severe_total) if complete and inspections else ""
            years_value = ", ".join(severe_years) if complete and inspections else ""

            details = []
            for item in inspections:
                v = item["current_violations"]
                details.append(
                    f'{item["inspection_id"]} ({item.get("date_opened") or "date unknown"}): '
                    f'{v["serious"]} serious, {v["willful"]} willful, {v["repeat"]} repeat, {v["total"]} total'
                )
            if inspections:
                narrative = f"OSHA establishment search matched {len(inspections)} inspection(s). " + "; ".join(details)
            else:
                narrative = "No exact-name OSHA inspection match was found across the completed 1972-present targeted searches."

            source_url = (self.query_urls.get(key) or [f"{OSHA_BASE}/ords/imis/establishment.html"])[-1]
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
                    "source_system": "OSHA IMIS Establishment Search",
                    "query_mode": "master_contractor_only",
                    "search_terms": contractor.get("_osha_aliases") or [],
                    "query_count": len(self.query_urls.get(key) or []),
                    "complete_aggregate": bool(complete),
                    "severe_definition": "Current Serious + Willful + Repeat violations",
                    "osha_inspections": inspections,
                },
            })
        return records
