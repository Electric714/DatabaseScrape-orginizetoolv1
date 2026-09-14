"""Additional official state debarment collectors used by the POC.

Illinois is a finite public HTML list tied directly to Prevailing Wage Act
violations. Wisconsin publishes a finite WisDOT PDF; the adapter searches only
selected master contractors and requires matching location/date evidence before
a positive finding can be proposed.
"""
from __future__ import annotations

import io
import re
from datetime import date, datetime, timezone

from bs4 import BeautifulSoup
from pypdf import PdfReader

from .bidder_schema import normalize_match_text
from .osha_adapter import contractor_aliases

IL_URL = "https://labor.illinois.gov/laws-rules/conmed/debarred-contractors.html"
WI_URL = "https://wisconsindot.gov/hccidocs/debar.pdf"

_IL_PATTERN = re.compile(
    r"^(?P<name>.+?)\s+has been debarred from any public works project for a period of four years effective\s+"
    r"(?P<date>[A-Z][a-z]+\s+\d{1,2},\s+\d{4})\.?$",
    re.I,
)
_DATE = re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b")
_ACTION = re.compile(r"\b(debarment|debarred|suspended|ineligible)\b", re.I)


def _plus_four_years(value: date) -> date:
    try:
        return value.replace(year=value.year + 4)
    except ValueError:
        return value.replace(year=value.year + 4, day=28)


def parse_illinois(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    if "Public Works Debarred Contractors" not in text or "Prevailing Wage Act" not in text:
        raise ValueError("Illinois debarment page structure changed; no conclusion")

    records = []
    for value in soup.stripped_strings:
        match = _IL_PATTERN.match(value.strip())
        if not match:
            continue
        try:
            start = datetime.strptime(match.group("date"), "%B %d, %Y").date()
        except ValueError as exc:
            raise ValueError("Illinois debarment effective date could not be parsed") from exc
        records.append({
            "name": match.group("name").strip(),
            "effective": start.isoformat(),
            "termination": _plus_four_years(start).isoformat(),
            "source_url": IL_URL,
        })

    if not records and "no currently debarred" not in text.casefold():
        raise ValueError("Illinois debarment list could not be parsed; no conclusion")
    return records


def _exact_aliases(row: dict) -> set[str]:
    return {normalize_match_text(value) for value in contractor_aliases(row) if normalize_match_text(value)}


def _entry_active(entry: dict, today: date) -> bool | None:
    try:
        start = date.fromisoformat(entry["effective"])
        end = date.fromisoformat(entry["termination"])
    except (KeyError, TypeError, ValueError):
        return None
    return start <= today <= end


class IllinoisDebarmentAdapter:
    query_mode = always_parse = fail_fast_access_errors = True
    canonical_start_url = IL_URL
    master_fields = ("state_federal_debarment", "prevailing_wage_violations")

    def seed_urls(self, master_rows):
        self.contractors = master_rows
        self.entries = []
        self.parsed = False
        return [IL_URL]

    def allowed_url(self, url):
        return url == IL_URL

    def links(self, html, url):
        self.entries = parse_illinois(html)
        self.parsed = True
        return []

    def extract(self, html, url):
        return []

    def finalize_records(self, complete):
        now = datetime.now(timezone.utc)
        results = []
        for row in self.contractors:
            aliases = _exact_aliases(row)
            candidates = []
            for entry in self.entries:
                exact = normalize_match_text(entry["name"]) in aliases
                if exact:
                    candidates.append({**entry, "match": "Exact official listed name", "active": _entry_active(entry, now.date())})
            positive = any(item["active"] is True for item in candidates)
            done = complete and self.parsed
            fields = {
                "state_federal_debarment": "Y" if positive else "",
                "prevailing_wage_violations": "Y" if positive else "",
            }
            results.append({
                "external_id": "il:bidder:" + str(row.get("_master_id") or row.get("id")),
                "bidder_id": row.get("id", ""),
                "company": row["contractor_name"],
                "source_url": IL_URL,
                **fields,
                "extra": {
                    "master_id": row.get("_master_id"),
                    "source_system": "Illinois Department of Labor public works debarment",
                    "api_fields_written_to_master": list(self.master_fields),
                    "retrieved_at": now.isoformat(),
                    "complete_aggregate": done,
                    "search_terms": contractor_aliases(row),
                    "candidates": candidates,
                    "list_records_checked": len(self.entries),
                    "jurisdiction": "IL",
                    "identity_basis": "Exact listed entity name; Illinois page does not publish location on the current list",
                    "narrative": "Active Illinois public-works debarment under the Prevailing Wage Act" if positive else
                        ("No exact active listed name; combined fields unchanged" if done else "UNKNOWN / INCOMPLETE"),
                    "coverage": "Illinois public-works debarment under the Prevailing Wage Act only",
                },
            })
        return results


def wisconsin_pdf_text(body: bytes) -> str:
    if not body.startswith(b"%PDF"):
        raise ValueError("WisDOT response is not a PDF")
    try:
        reader = PdfReader(io.BytesIO(body), strict=False)
    except Exception as exc:
        raise ValueError("WisDOT PDF could not be opened") from exc
    if not (2 <= len(reader.pages) <= 20):
        raise ValueError("WisDOT PDF page count is outside the expected range")
    parts = []
    for page in reader.pages:
        try:
            parts.append(page.extract_text() or "")
        except Exception as exc:
            raise ValueError("WisDOT PDF text extraction failed") from exc
    text = "\n".join(parts)
    required = ("Debarred", "Ineligible", "Suspended", "Effective", "Termination")
    if not all(token.casefold() in text.casefold() for token in required):
        raise ValueError("WisDOT PDF layout changed; no conclusion")
    return text


def _flat(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _location_confirmed(context: str, row: dict) -> bool:
    haystack = normalize_match_text(context)
    locations = [
        (row.get("city", ""), row.get("state", ""), row.get("zip", "")),
        (row.get("additional_address_city", ""), row.get("additional_address_state", ""), row.get("additional_address_zip", "")),
    ]
    for city, state, zip_code in locations:
        z = normalize_match_text(zip_code)
        c = normalize_match_text(city)
        s = normalize_match_text(state)
        if z and z in haystack:
            return True
        if c and s and c in haystack and s in haystack:
            return True
    return False


def _wisconsin_active(effective: str, termination: str, today: date) -> bool | None:
    try:
        start = datetime.strptime(effective, "%m/%d/%Y").date()
    except (TypeError, ValueError):
        return None
    if start > today:
        return False
    if termination.casefold() == "indefinite":
        return True
    try:
        end = datetime.strptime(termination, "%m/%d/%Y").date()
    except (TypeError, ValueError):
        return None
    return today <= end


def find_wisconsin_candidates(text: str, row: dict) -> list[dict]:
    flat = _flat(text)
    lower = flat.casefold()
    results = []
    seen = set()
    for alias in contractor_aliases(row):
        needle = _flat(alias).casefold()
        if not needle or len(needle) < 4:
            continue
        start = 0
        while True:
            index = lower.find(needle, start)
            if index < 0:
                break
            start = index + len(needle)
            before = flat[max(0, index - 260):index]
            after = flat[index:index + 720]
            context = before + " " + after
            action = _ACTION.search(after)
            dates = _DATE.findall(after)
            indefinite = re.search(r"\bIndefinite\b", after, re.I)
            if not action or not dates or "statewide" not in after.casefold():
                continue
            effective = dates[0]
            termination = dates[1] if len(dates) > 1 else ("Indefinite" if indefinite else "")
            location = _location_confirmed(context, row)
            candidate = {
                "name": alias,
                "effective": effective,
                "termination": termination,
                "action": action.group(1).title(),
                "location_confirmed": location,
                "active": _wisconsin_active(effective, termination, datetime.now(timezone.utc).date()),
                "match": "Confirmed exact name + location" if location else "Exact name / location not corroborated",
                "source_url": WI_URL,
            }
            signature = (normalize_match_text(alias), effective, termination, candidate["action"])
            if signature not in seen:
                seen.add(signature)
                results.append(candidate)
    return results


class WisconsinDebarmentAdapter:
    query_mode = always_parse = fail_fast_access_errors = True
    canonical_start_url = WI_URL
    master_fields = ("state_federal_debarment",)
    accepted_content_types = ("application/pdf",)
    allowed_asset_extensions = (".pdf",)

    def seed_urls(self, master_rows):
        self.contractors = master_rows
        self.text = ""
        self.parsed = False
        return [WI_URL]

    def allowed_url(self, url):
        return url == WI_URL

    def decode_body(self, body, headers, url):
        kind = str(headers.get("content-type") or "").casefold()
        if "pdf" in kind or body.startswith(b"%PDF"):
            return wisconsin_pdf_text(body)
        return body.decode("utf-8", errors="replace")

    def links(self, text, url):
        if "List of Debarred, Suspended and Ineligible Contractors" not in text:
            raise ValueError("WisDOT extracted text missing expected title")
        self.text = text
        self.parsed = True
        return []

    def extract(self, text, url):
        return []

    def finalize_records(self, complete):
        now = datetime.now(timezone.utc)
        results = []
        for row in self.contractors:
            candidates = find_wisconsin_candidates(self.text, row) if self.parsed else []
            positive = any(
                item["location_confirmed"] and item["active"] is True
                for item in candidates
            )
            done = complete and self.parsed
            results.append({
                "external_id": "wi:bidder:" + str(row.get("_master_id") or row.get("id")),
                "bidder_id": row.get("id", ""),
                "company": row["contractor_name"],
                "source_url": WI_URL,
                "state_federal_debarment": "Y" if positive else "",
                "extra": {
                    "master_id": row.get("_master_id"),
                    "source_system": "Wisconsin DOT debarred/suspended/ineligible PDF",
                    "api_fields_written_to_master": list(self.master_fields),
                    "retrieved_at": now.isoformat(),
                    "complete_aggregate": done,
                    "search_terms": contractor_aliases(row),
                    "candidates": candidates,
                    "jurisdiction": "WI",
                    "narrative": "Active WisDOT-listed restriction with corroborating location" if positive else
                        ("No confirmed active location-corroborated match; combined field unchanged" if done else "UNKNOWN / INCOMPLETE"),
                    "coverage": "WisDOT's published list only; not a complete federal or all-agency clearance",
                },
            })
        return results
