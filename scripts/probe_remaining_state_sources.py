"""Temporary structure-only probe for remaining official state sources."""
from __future__ import annotations

import io
import json
import re
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

FL_SUSPENDED = "https://www.dms.myflorida.com/business_operations/state_purchasing/state_agency_resources/vendor_registration_and_vendor_lists/suspended_vendor_list"
FL_CONVICTED = "https://www.dms.myflorida.com/business_operations/state_purchasing/state_agency_resources/vendor_registration_and_vendor_lists/convicted_vendor_list"
MO_PAGE = "https://purch.oa.mo.gov/media/pdf/suspendeddebarred-vendors"
OH_URLS = [
    "https://procure.ohio.gov/state-and-local-agencies/resources/08_debarment-csv-list",
    "https://procure.ohio.gov/bidders-and-suppliers/resources/08_debarment-csv-list",
]


def _next_data(html: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    candidates = [tag.get_text() for tag in soup.find_all("script") if tag.get_text().lstrip().startswith("{")]
    for value in candidates:
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and "props" in payload and "page" in payload:
            return payload
    raise ValueError("Next.js page payload not found")


def probe_florida(client: httpx.Client) -> None:
    for label, url in (("FL suspended", FL_SUSPENDED), ("FL convicted", FL_CONVICTED)):
        response = client.get(url)
        print(f"[{label}] status={response.status_code} type={response.headers.get('content-type','')} bytes={len(response.content)}")
        payload = _next_data(response.text)
        page_data = payload["props"]["pageProps"]["pageData"]
        print(f"[{label}] page_data_keys={sorted(page_data.keys())}")
        summary_html = str((page_data.get("summary") or {}).get("html5") or "")
        short_html = str((page_data.get("shortDescription") or {}).get("html5") or "")
        if label.endswith("suspended"):
            embedded = BeautifulSoup(summary_html, "lxml")
            table = embedded.find("table")
            rows = table.find_all("tr") if table else []
            print(f"[{label}] table={bool(table)} rows={len(rows)}")
            if rows:
                header_cells = rows[0].find_all(["th", "td"], recursive=False)
                print(f"[{label}] header_count={len(header_cells)} header_labels={[c.get_text(' ', strip=True) for c in header_cells]}")
            if len(rows) > 1:
                cells = rows[1].find_all(["th", "td"], recursive=False)
                vendor = cells[0] if cells else None
                strings = list(vendor.stripped_strings) if vendor else []
                child_tags = [child.name for child in vendor.children if getattr(child, "name", None)] if vendor else []
                print(f"[{label}] data_cells={len(cells)} vendor_string_parts={len(strings)} vendor_child_tags={child_tags} vendor_links={len(vendor.find_all('a')) if vendor else 0} vendor_breaks={len(vendor.find_all('br')) if vendor else 0}")
        else:
            combined = (short_html + " " + summary_html).casefold()
            print(f"[{label}] explicit_empty={'no vendors' in combined} summary_len={len(summary_html)} short_len={len(short_html)}")


def probe_missouri(client: httpx.Client) -> None:
    response = client.get(MO_PAGE)
    print(f"[MO landing] status={response.status_code} type={response.headers.get('content-type','')} bytes={len(response.content)}")
    raw_paths = sorted(set(re.findall(r"(?:https?://[^\"'<>\s]+\.pdf|/[^\"'<>\s]+\.pdf)", response.text, re.I)))
    direct = []
    for raw in raw_paths:
        candidate = urljoin(str(response.url), raw)
        parsed = urlsplit(candidate)
        if "suspven" in parsed.path.casefold() and parsed.path.casefold().endswith(".pdf"):
            direct.append(candidate)
    print(f"[MO landing] pdf_paths={len(raw_paths)} direct_suspven_paths={len(direct)}")
    if len(direct) != 1:
        raise ValueError("Expected one direct Missouri suspension/debarment PDF")
    pdf = client.get(direct[0])
    print(f"[MO pdf] status={pdf.status_code} type={pdf.headers.get('content-type','')} bytes={len(pdf.content)} valid_pdf={pdf.content.startswith(b'%PDF')}")
    if not pdf.content.startswith(b"%PDF"):
        raise ValueError("Missouri direct resource is not PDF")
    reader = PdfReader(io.BytesIO(pdf.content), strict=False)
    pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
    text = "\n".join(pages)
    lines = [line for line in text.splitlines() if line.strip()]
    date_re = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
    date_lines = [line for line in lines if date_re.search(line)]
    date_counts = sorted(set(len(date_re.findall(line)) for line in date_lines))
    first_page_lines = [line for line in pages[0].splitlines() if line.strip()] if pages else []
    safe_keywords = [word for word in ("Vendor", "Effective", "Expiration", "Suspended", "Debarred", "Address", "Reason") if word.casefold() in text.casefold()]
    print(f"[MO pdf] pages={len(pages)} lines={len(lines)} first_page_lines={len(first_page_lines)} date_lines={len(date_lines)} date_counts={date_counts} keywords={safe_keywords}")


def probe_ohio(client: httpx.Client) -> None:
    statuses = [client.get(url).status_code for url in OH_URLS]
    print(f"[OH] documented_url_statuses={statuses} acquisition_unresolved={all(code == 404 for code in statuses)}")


def main() -> None:
    with httpx.Client(headers={"User-Agent": "ParalegalDatabaseTool-POC-SourceValidation/1.0"}, timeout=30.0, follow_redirects=True, trust_env=False) as client:
        probe_florida(client)
        probe_missouri(client)
        probe_ohio(client)


if __name__ == "__main__":
    main()
