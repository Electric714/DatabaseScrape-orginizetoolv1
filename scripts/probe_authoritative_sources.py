"""Temporary public-source structure probe for authoritative source implementation.

This prints only source structure/column metadata. It never reads bidder data and
is removed before integration.
"""
from __future__ import annotations

import io
import re
from collections import Counter
from urllib.parse import parse_qs, unquote, urljoin, urlsplit

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


def html_structure(label: str, html: str) -> None:
    soup = BeautifulSoup(html, "lxml")
    print(f"{label}: title={soup.title.get_text(' ', strip=True) if soup.title else ''!r}")
    for i, table in enumerate(soup.find_all("table")):
        headers = [re.sub(r"\s+", " ", th.get_text(" ", strip=True)) for th in table.find_all("th")]
        rows = table.find_all("tr")
        print(f"{label}: table{i} headers={headers!r} rows={max(0, len(rows)-1)}")
    # Florida currently renders the suspended list through non-table CMS markup.
    if label.startswith("fl_"):
        markers = ["Vendor Name/Address", "Agency of Origin", "Effective Date", "Notice of Default", "There are currently no vendors on this list"]
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        print(f"{label}: markers={{m: (m in text) for m in {markers!r}}}")
        for marker in ("Building Maintenance of America", "There are currently no vendors on this list"):
            node = soup.find(string=re.compile(re.escape(marker), re.I))
            if node:
                parent = node.parent
                print(f"{label}: marker_parent={parent.name} class={parent.get('class')} parent2={parent.parent.name if parent.parent else ''} class2={parent.parent.get('class') if parent.parent else None}")
                print(f"{label}: marker_parent_text={re.sub(r'\s+', ' ', parent.parent.get_text(' ', strip=True) if parent.parent else parent.get_text(' ', strip=True))[:500]!r}")


def pdf_layout(label: str, body: bytes) -> None:
    if not body.startswith(b"%PDF"):
        raise ValueError(f"{label}: not PDF")
    reader = PdfReader(io.BytesIO(body), strict=False)
    print(f"{label}: pages={len(reader.pages)}")
    text = "\n".join((page.extract_text(extraction_mode="layout") or "") for page in reader.pages)
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    print(f"{label}: first_lines={[re.sub(r'\s+', ' ', line).strip() for line in lines[:14]]!r}")
    date = re.compile(r"\b(?:\d{1,2}/\d{1,2}/\d{2,4}|[A-Z][a-z]{2}-\d{4})\b")
    shapes = Counter()
    examples = []
    for line in lines:
        if date.search(line):
            parts = tuple(part.strip() for part in re.split(r"\s{2,}", line.strip()) if part.strip())
            shapes[len(parts)] += 1
            if len(examples) < 15:
                examples.append(parts)
    print(f"{label}: dated_row_part_counts={dict(shapes)}")
    print(f"{label}: dated_row_examples={examples!r}")


with httpx.Client(headers={"User-Agent": UA}, timeout=45, follow_redirects=True, trust_env=False) as client:
    pages = {}
    for label, url in (("fl_suspended", FL_SUSPENDED), ("fl_convicted", FL_CONVICTED), ("dfi", DFI)):
        pages[label] = safe_get(client, url).text
        html_structure(label, pages[label])

    landing = safe_get(client, MO_PAGE)
    soup = BeautifulSoup(landing.text, "lxml")
    print("mo: relevant_anchors=" + repr([(a.get_text(" ", strip=True), a.get("href")) for a in soup.find_all("a", href=True) if "suspven" in (a.get_text(" ", strip=True) + a.get("href", "")).casefold()]))
    pdfs = []
    # Drupal may wrap a direct official PDF in its same-host PDF.js viewer.
    for a in soup.find_all("a", href=True):
        href = urljoin(str(landing.url), a["href"])
        parsed = urlsplit(href)
        candidates = [href]
        file_value = (parse_qs(parsed.query).get("file") or [""])[0]
        if file_value:
            candidates.append(unquote(file_value))
        for candidate in candidates:
            cp = urlsplit(candidate)
            if cp.hostname == "purch.oa.mo.gov" and cp.path.casefold().endswith(".pdf") and "suspven" in cp.path.casefold():
                pdfs.append(candidate)
    # Also inspect raw CMS markup because some link modules encode the PDF outside href.
    for raw in re.findall(r"https?://[^\"'<>\s]+suspven[^\"'<>\s]+\.pdf", landing.text, re.I):
        pdfs.append(raw.replace("&amp;", "&"))
    pdfs = list(dict.fromkeys(pdfs))
    print(f"mo: candidate_pdfs={len(pdfs)} paths={[urlsplit(u).path for u in pdfs]!r}")
    if len(pdfs) == 1:
        pdf_layout("mo", safe_get(client, pdfs[0]).content)
    else:
        print("mo: unable_to_prove_single_pdf")

    pdf_layout("wi_contract_compliance", safe_get(client, WI_CC).content)
    tax = safe_get(client, WI_TAX)
    reader = PdfReader(io.BytesIO(tax.content), strict=False)
    tax_text = "\n".join((page.extract_text(extraction_mode="layout") or "") for page in reader.pages)
    first = re.sub(r"\s+", " ", tax_text[:5000]).strip()
    print(f"wi_tax: pages={len(reader.pages)} header_vendor={'Vendor' in first} heading_7766={'77.66' in tax_text}")
    asof = re.search(r"Vendors Not In Compliance With Sec\.\s*77\.66.*?as of ([A-Za-z]+ \d{1,2}, \d{4})", re.sub(r"\s+", " ", tax_text), re.I)
    print(f"wi_tax: as_of={asof.group(1) if asof else None!r}")
