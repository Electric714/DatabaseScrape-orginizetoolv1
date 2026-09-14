"""Temporary live-source probe for FL/MO/OH official debarment resources.

Prints only transport and document structure. It intentionally does not log vendor
names, addresses, IDs, or contractor data.
"""
from __future__ import annotations

import io
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

FL_SUSPENDED = "https://www.dms.myflorida.com/business_operations/state_purchasing/state_agency_resources/vendor_registration_and_vendor_lists/suspended_vendor_list"
FL_CONVICTED = "https://www.dms.myflorida.com/business_operations/state_purchasing/state_agency_resources/vendor_registration_and_vendor_lists/convicted_vendor_list"
MO_PAGE = "https://purch.oa.mo.gov/media/pdf/suspendeddebarred-vendors"
OH_CANDIDATES = [
    "https://procure.ohio.gov/state-and-local-agencies/resources/08_debarment-csv-list",
    "https://procure.ohio.gov/bidders-and-suppliers/resources/08_debarment-csv-list",
]


def summary(label: str, response: httpx.Response) -> None:
    print(f"[{label}] status={response.status_code} final={response.url} type={response.headers.get('content-type','')} bytes={len(response.content)}")


def html_structure(label: str, response: httpx.Response) -> BeautifulSoup:
    soup = BeautifulSoup(response.text, "lxml")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    tables = soup.find_all("table")
    print(f"[{label}] title={title!r} tables={len(tables)} rows={[len(t.find_all('tr')) for t in tables][:8]}")
    for index, table in enumerate(tables[:4]):
        headers = [cell.get_text(" ", strip=True) for cell in table.find_all("th")]
        print(f"[{label}] table{index}_headers={headers}")
    return soup


def probe_florida(client: httpx.Client) -> None:
    for label, url in [("FL suspended", FL_SUSPENDED), ("FL convicted", FL_CONVICTED)]:
        response = client.get(url)
        summary(label, response)
        soup = html_structure(label, response)
        text = soup.get_text(" ", strip=True).casefold()
        print(f"[{label}] expected_tokens={{'vendor':{'vendor' in text},'list':{'list' in text},'no_vendors':{'currently no vendors' in text}}}")


def probe_missouri(client: httpx.Client) -> None:
    response = client.get(MO_PAGE)
    summary("MO landing", response)
    soup = html_structure("MO landing", response)
    pdf_links = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(str(response.url), anchor["href"])
        if ".pdf" in href.casefold():
            pdf_links.append(href)
    print(f"[MO landing] pdf_links={len(pdf_links)}")
    if not pdf_links:
        raise SystemExit("Missouri official page exposed no PDF link")
    pdf = client.get(pdf_links[0])
    summary("MO pdf", pdf)
    if not pdf.content.startswith(b"%PDF"):
        raise SystemExit("Missouri linked resource is not a PDF")
    reader = PdfReader(io.BytesIO(pdf.content), strict=False)
    texts = [(page.extract_text() or "") for page in reader.pages]
    text = "\n".join(texts).casefold()
    print(f"[MO pdf] pages={len(reader.pages)} text_chars={len(text)} tokens={{'suspended':{'suspend' in text},'debarred':{'debar' in text},'effective':{'effective' in text},'vendor':{'vendor' in text}}}")


def probe_ohio(client: httpx.Client) -> None:
    success = False
    for index, url in enumerate(OH_CANDIDATES):
        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            print(f"[OH {index}] error={type(exc).__name__}")
            continue
        summary(f"OH {index}", response)
        if response.status_code >= 400:
            continue
        soup = html_structure(f"OH {index}", response)
        text = soup.get_text(" ", strip=True).casefold()
        links = [urljoin(str(response.url), a["href"]) for a in soup.find_all("a", href=True)]
        csv_links = [href for href in links if ".csv" in href.casefold()]
        download_links = [href for href in links if "download" in href.casefold() or "debar" in href.casefold()]
        print(f"[OH {index}] expected_tokens={{'debarment':{'debar' in text},'vendor':{'vendor' in text},'supplier':{'supplier' in text}}} csv_links={len(csv_links)} debar_or_download_links={len(download_links)}")
        if soup.find("table") or csv_links:
            success = True
    if not success:
        print("[OH] acquisition_unresolved=true")


def main() -> None:
    headers = {"User-Agent": "ParalegalDatabaseTool-POC-SourceValidation/1.0"}
    with httpx.Client(headers=headers, timeout=30.0, follow_redirects=True, trust_env=False) as client:
        probe_florida(client)
        probe_missouri(client)
        probe_ohio(client)


if __name__ == "__main__":
    main()
