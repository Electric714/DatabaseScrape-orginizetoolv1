import json
import re
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .normalizer import clean_text, canonical_record, entity_key

PHONE_RE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}(?!\d)")
DATE_RE = re.compile(
    r"\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-](?:\d{4}|\d{2})|"
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|"
    r"Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
    r"\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)
ADDRESS_RE = re.compile(
    r"\b\d{1,6}\s+[A-Za-z0-9.'#\- ]{2,80}\s+"
    r"(?:St(?:reet)?|Ave(?:nue)?|Rd|Road|Blvd|Boulevard|Dr(?:ive)?|Ln|Lane|Ct|Court|"
    r"Way|Pkwy|Parkway|Hwy|Highway|Cir|Circle|Pl|Place|Ter|Terrace)\b[^\n,;]{0,80}",
    re.IGNORECASE,
)

FIELD_ALIASES = {
    "name": {"name", "full name", "person", "person name", "contact", "contact name", "member", "attorney"},
    "company": {"company", "company name", "business", "business name", "legal business name", "organization", "organisation", "employer", "firm"},
    "owner": {"owner", "owner name", "business owner", "business owner name", "name of owner"},
    "phone": {"phone", "telephone", "tel", "mobile", "cell"},
    "address": {"address", "street address", "mailing address", "business address", "physical address"},
    "location": {"location", "business location", "city", "city/state", "city / state", "city, state"},
    "osha_status": {"osha status", "osha violation status", "osha violations status"},
    "osha_details": {"osha violations", "open osha violations", "osha details", "osha violation details", "osha findings"},
    "date": {"date", "updated", "update date", "filed", "filed date", "record date", "created"},
    "external_id": {"id", "record id", "case id", "license id", "number", "record number"},
}


def _field_for_header(text: str) -> str | None:
    key = clean_text(text).lower().rstrip(":")
    for field, aliases in FIELD_ALIASES.items():
        if key in aliases:
            return field
    return None


def _jsonld_objects(value: Any):
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_objects(item)
    elif isinstance(value, dict):
        if "@graph" in value:
            yield from _jsonld_objects(value["@graph"])
        for key, nested in value.items():
            if key != "@graph" and isinstance(nested, (list, dict)):
                yield from _jsonld_objects(nested)
        yield value


def _address_from_jsonld(value: Any) -> str:
    if isinstance(value, str):
        return clean_text(value)
    if not isinstance(value, dict):
        return ""
    parts = [
        value.get("streetAddress"), value.get("addressLocality"), value.get("addressRegion"),
        value.get("postalCode"), value.get("addressCountry"),
    ]
    return ", ".join([clean_text(x) for x in parts if clean_text(x)])


def _record_has_signal(record: dict[str, Any]) -> bool:
    return bool(record.get("name") or record.get("company") or record.get("owner")) and bool(
        record.get("phone") or record.get("address") or record.get("date") or record.get("external_id")
        or record.get("location") or record.get("osha_status") or record.get("osha_details")
        or (record.get("company") and record.get("owner"))
    )


def _assign_labeled(record: dict[str, Any], field: str, value: str) -> None:
    value = clean_text(value)
    record[field] = value
    if field == "osha_status":
        record["extra"]["osha_status_raw"] = value


def extract_jsonld(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        try:
            payload = json.loads(script.string or script.get_text())
        except Exception:
            continue
        for obj in _jsonld_objects(payload):
            typ = obj.get("@type", "")
            types = {typ} if isinstance(typ, str) else {t for t in typ if isinstance(t, str)} if isinstance(typ, list) else set()
            if not types.intersection({"Person", "Organization", "LocalBusiness", "ProfessionalService", "Attorney"}):
                continue
            contact = obj.get("contactPoint")
            if isinstance(contact, list):
                contact = contact[0] if contact else {}
            contact = contact if isinstance(contact, dict) else {}
            identifier = obj.get("identifier") or obj.get("@id") or ""
            if isinstance(identifier, dict):
                identifier = identifier.get("value") or identifier.get("@id") or ""
            record = {
                "name": obj.get("name", "") if "Person" in types else "",
                "company": obj.get("name", "") if "Person" not in types else clean_text(
                    (obj.get("worksFor") or {}).get("name") if isinstance(obj.get("worksFor"), dict) else obj.get("worksFor")
                ),
                "phone": obj.get("telephone") or contact.get("telephone") or "",
                "address": _address_from_jsonld(obj.get("address")),
                "date": obj.get("dateModified") or obj.get("datePublished") or "",
                "external_id": identifier,
                "source_url": page_url,
                "extra": {"jsonld_type": sorted(types),
                          **{k: obj[k] for k in ("givenName", "additionalName", "familyName") if k in obj},
                          **({"postal_address": obj["address"]} if isinstance(obj.get("address"), dict) else {})},
            }
            if _record_has_signal(record):
                records.append(record)
    return records


def extract_tables(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header_cells = rows[0].find_all(["th", "td"])
        mapped = [_field_for_header(c.get_text(" ", strip=True)) for c in header_cells]
        if not any(mapped):
            continue
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            record: dict[str, Any] = {"source_url": page_url, "extra": {}}
            for idx, cell in enumerate(cells):
                if idx < len(mapped) and mapped[idx]:
                    _assign_labeled(record, mapped[idx], cell.get_text(" ", strip=True))
            link = row.find("a", href=True)
            if link:
                record["source_url"] = urljoin(page_url, link["href"])
            if _record_has_signal(record):
                records.append(record)
    return records


def extract_definition_lists(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for dl in soup.find_all("dl"):
        record: dict[str, Any] = {"source_url": page_url, "extra": {}}
        for dt in dl.find_all("dt"):
            dd = dt.find_next_sibling("dd")
            if not dd:
                continue
            field = _field_for_header(dt.get_text(" ", strip=True))
            if field:
                _assign_labeled(record, field, dd.get_text(" ", strip=True))
        if _record_has_signal(record):
            records.append(record)
    return records


def extract_labeled_blocks(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    selectors = "article, .card, .result, .results-item, .listing, .listing-item, .record, .profile, .directory-item"
    seen: set[str] = set()
    for block in soup.select(selectors):
        text = clean_text(block.get_text(" ", strip=True))
        if len(text) < 20 or len(text) > 5000:
            continue
        record: dict[str, Any] = {"source_url": page_url, "extra": {"raw_text": text[:1500]}}
        for element in block.find_all(["dt", "th", "strong", "b", "span", "div", "p"]):
            label = clean_text(element.get_text(" ", strip=True)).rstrip(":")
            field = _field_for_header(label)
            if field:
                if isinstance(element.next_sibling, str) and clean_text(element.next_sibling).lstrip(": "):
                    _assign_labeled(record, field, str(element.next_sibling).lstrip(": "))
                else:
                    sibling = element.find_next_sibling()
                    if sibling:
                        _assign_labeled(record, field, sibling.get_text(" ", strip=True))
            elif ":" in label and not element.find(["div", "p", "dl", "table", "strong", "b", "span", "dt", "th"]):
                # A single labeled line such as "Business name: Example LLC".
                # Do not parse a whole card as one field or treat prose as status.
                inline_label, value = label.split(":", 1)
                field = _field_for_header(inline_label)
                if field:
                    _assign_labeled(record, field, value)
        phone = PHONE_RE.search(text)
        address = ADDRESS_RE.search(text)
        date = DATE_RE.search(text)
        record.setdefault("phone", phone.group(0) if phone else "")
        record.setdefault("address", address.group(0) if address else "")
        record.setdefault("date", date.group(0) if date else "")
        if not record.get("name") and not record.get("company"):
            heading = block.find(["h1", "h2", "h3", "h4"])
            if heading:
                candidate = clean_text(heading.get_text(" ", strip=True))
                if 2 <= len(candidate) <= 160:
                    record["name"] = candidate
        link = block.find("a", href=True)
        if link:
            record["source_url"] = urljoin(page_url, link["href"])
        key = json.dumps(record, sort_keys=True, default=str)
        if _record_has_signal(record) and key not in seen:
            seen.add(key)
            records.append(record)
    return records


def extract_page_fallback(soup: BeautifulSoup, page_url: str) -> list[dict[str, Any]]:
    for element in soup(["nav", "footer", "header", "script", "style"]):
        element.decompose()
    text = clean_text(soup.get_text(" ", strip=True))
    if not text or len(text) > 30000:
        return []
    phone = PHONE_RE.search(text)
    address = ADDRESS_RE.search(text)
    date = DATE_RE.search(text)
    if not (phone or address):
        return []
    title_node = soup.find("h1") or soup.title
    title = clean_text(title_node.get_text(" ", strip=True) if title_node else "")
    if not title:
        return []
    return [{
        "name": title,
        "company": "",
        "phone": phone.group(0) if phone else "",
        "address": address.group(0) if address else "",
        "date": date.group(0) if date else "",
        "external_id": "",
        "source_url": page_url,
        "extra": {"extraction": "page_fallback"},
    }]


def extract_records(html: str, page_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "lxml")
    records = []
    records.extend(extract_jsonld(soup, page_url))
    records.extend(extract_tables(soup, page_url))
    records.extend(extract_definition_lists(soup, page_url))
    records.extend(extract_labeled_blocks(soup, page_url))
    # Page-wide title/phone pairing is too speculative for record collection.
    # Preserve stronger JSON-LD/table results when overlapping card heuristics agree.
    unique = {}
    for record in records:
        record["extra"]["collected_from_url"] = page_url
        record["source_url"] = safe_link(page_url, record.get("source_url", page_url))
        normalized = canonical_record(record)
        unique.setdefault(entity_key(normalized), normalized)
    return list(unique.values())


def discover_links(html: str, page_url: str) -> list[str]:
    soup = BeautifulSoup(html, "lxml")
    base = soup.find("base", href=True)
    origin = urljoin(page_url, base["href"]) if base else page_url
    return [urljoin(origin, a["href"]) for a in soup.select("a[href], link[rel~=next][href]")]


def safe_link(page_url: str, href: str) -> str:
    from urllib.parse import urlsplit
    value = urljoin(page_url, href)
    parts = urlsplit(value)
    return value if parts.scheme in {"http", "https"} and not parts.username else page_url
