"""Temporary public-source structure probe for authoritative source implementation.

This prints only source structure/column metadata. It never reads bidder data and
is removed before integration.
"""
from __future__ import annotations

import io
import re
from collections import Counter
from urllib.parse import urljoin, urlsplit

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

UA = "ParalegalResearchDesk-POC-OfficialSourceValidation/1.0"
MO_PAGE = "https://purch.oa.mo.gov/media/pdf/suspendeddebarred-vendors"
FL_SUSPENDED = "https://www.dms.myflorida.com/business_operations/state_purchasing/state_agency_resources/vendor_registration_and_vendor_lists/suspended_vendor_list"
FL_CONVICTED = "https://www.dms.myflorida.com/business_operations/state_purchasing/state_agency_resources/vendor_registration_and_vendor_lists/convicted_vendor_list"
WI_CC = "https://doa.wi.gov/Documents/DEO/WOCCELIIneligible.pdf"
WI_TAX = "https://doa.wi.gov/Documents/DEO/CertList.pdf"
DFI = "https://apps.dfi.wi.gov/apps/CorpSearch/Results.aspx?q=S5&type=Simple"


def safe_get(client: httpx.Client, url: str) -> httpx.Response:
    r = client.get(url)
    print(f"GET host={urlsplit(url).hostname} path={urlsplit(url).path} status={r.status_code} type={r.headers.get('content-type','').split(';')[0]} bytes={len(r.content)}")
    r.raise_for_status()
    return r


def html_tables(label: str, html: str) -> None:
    soup = BeautifulSoup(html, "lxml")
    print(f"{label}: title={soup.title.get_text(' ', strip=True) if soup.title else ''!r}")
    for i, table in enumerate(soup.find_all("table")):
        headers = [re.sub(r"\s+", " ", th.get_text(" ", strip=True)) for th in table.find_all("th")]
        rows = table.find_all("tr")
        print(f"{label}: table{i} headers={headers!r} rows={max(0, len(rows)-1)}")


def pdf_layout(label: str, body: bytes) -> None:
    if not body.startswith(b"%PDF"):
        raise ValueError(f"{label}: not PDF")
    reader = PdfReader(io.BytesIO(body), strict=False)
    print(f"{label}: pages={len(reader.pages)}")
    text = "\n".join((page.extract_text(extraction_mode="layout") or "") for page in reader.pages)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines() if line.strip()]
    print(f"{label}: first_lines={lines[:12]!r}")
    date = re.compile(r"\b(?:\d{1,2}/\d{1,2}/\d{2,4}|[A-Z][a-z]{2}-\d{4})\b")
    shapes = Counter()
    examples = []
    for line in lines:
        if date.search(line):
            parts = tuple(part.strip() for part in re.split(r"\s{2,}", line) if part.strip())
            shapes[len(parts)] += 1
            if len(examples) < 12:
                examples.append(tuple(len(p) for p in parts))
    print(f"{label}: dated_row_part_counts={dict(shapes)} sample_part_lengths={examples!r}")


with httpx.Client(headers={"User-Agent": UA}, timeout=45, follow_redirects=True, trust_env=False) as client:
    for label, url in (("fl_suspended", FL_SUSPENDED), ("fl_convicted", FL_CONVICTED), ("dfi", DFI)):
        html_tables(label, safe_get(client, url).text)

    landing = safe_get(client, MO_PAGE)
    soup = BeautifulSoup(landing.text, "lxml")
    pdfs = []
    for a in soup.find_all("a", href=True):
        href = urljoin(str(landing.url), a["href"])
        parsed = urlsplit(href)
        if parsed.hostname == "purch.oa.mo.gov" and parsed.path.casefold().endswith(".pdf") and "suspven" in parsed.path.casefold():
            pdfs.append(href)
    print(f"mo: candidate_pdfs={len(set(pdfs))} paths={[urlsplit(u).path for u in sorted(set(pdfs))]!r}")
    if len(set(pdfs)) != 1:
        raise ValueError("Missouri landing did not expose exactly one suspended/debarred PDF")
    pdf_layout("mo", safe_get(client, next(iter(set(pdfs)))).content)
    pdf_layout("wi_contract_compliance", safe_get(client, WI_CC).content)
    # Tax PDF is large; only verify acquisition/header without logging vendor data.
    tax = safe_get(client, WI_TAX)
    reader = PdfReader(io.BytesIO(tax.content), strict=False)
    first = re.sub(r"\s+", " ", reader.pages[0].extract_text() or " ").strip()
    print(f"wi_tax: pages={len(reader.pages)} header_tokens={{'vendor': {'Vendor' in first}, '77.66': {'77.66' in first}}}")
