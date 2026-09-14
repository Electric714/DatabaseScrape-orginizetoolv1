"""Temporary live-source probe for FL/MO/OH official debarment resources.

Prints only transport and document structure. It intentionally does not log vendor
names, addresses, IDs, or contractor data.
"""
from __future__ import annotations

import io
import re
from collections import Counter
from urllib.parse import urljoin, urlsplit

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
    classes = Counter()
    for tag in soup.find_all(True):
        for value in tag.get("class") or []:
            classes[value] += 1
    print(f"[{label}] common_classes={classes.most_common(12)}")
    return soup


def describe_static_marker(label: str, soup: BeautifulSoup, needle: str) -> None:
    node = soup.find(string=lambda value: value and needle.casefold() in value.casefold())
    if not node:
        print(f"[{label}] marker={needle!r} found=false")
        return
    tag = node.parent
    parent = tag.parent if tag else None
    print(
        f"[{label}] marker={needle!r} found=true tag={getattr(tag, 'name', None)} "
        f"class={getattr(tag, 'attrs', {}).get('class')} parent={getattr(parent, 'name', None)} "
        f"parent_class={getattr(parent, 'attrs', {}).get('class')}"
    )


def probe_florida(client: httpx.Client) -> None:
    for label, url in [("FL suspended", FL_SUSPENDED), ("FL convicted", FL_CONVICTED)]:
        response = client.get(url)
        summary(label, response)
        soup = html_structure(label, response)
        text = soup.get_text(" ", strip=True).casefold()
        print(f"[{label}] expected_tokens={{'vendor':{'vendor' in text},'list':{'list' in text},'no_vendors':{'no vendors' in text}}}")
        for marker in ("Vendor Name/Address", "Agency of Origin", "Effective Date", "Notice of Default", "no vendors"):
            describe_static_marker(label, soup, marker)
        notice_links = [a for a in soup.find_all("a", href=True) if "notice of default" in a.get_text(" ", strip=True).casefold()]
        print(f"[{label}] notice_links={len(notice_links)}")
        if notice_links:
            tag = notice_links[0]
            chain = []
            for _ in range(5):
                tag = tag.parent
                if not tag:
                    break
                chain.append((tag.name, tag.get("class")))
            print(f"[{label}] first_notice_ancestor_chain={chain}")


def probe_missouri(client: httpx.Client) -> None:
    response = client.get(MO_PAGE)
    summary("MO landing", response)
    soup = html_structure("MO landing", response)
    pdf_links = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(str(response.url), anchor["href"])
        if ".pdf" in href.casefold():
            pdf_links.append((anchor.get_text(" ", strip=True), href))
    print(f"[MO landing] pdf_links={len(pdf_links)}")
    for text, href in pdf_links:
        print(f"[MO landing] pdf_candidate text={text!r} host={urlsplit(href).hostname} path={urlsplit(href).path}")
    target = next(((text, href) for text, href in pdf_links if "suspven" in (text + href).casefold() or "suspend" in text.casefold() or "debar" in text.casefold()), None)
    if not target:
        raise SystemExit("Missouri official page exposed no targeted suspension/debarment PDF link")
    _, target_url = target
    pdf = client.get(target_url)
    summary("MO pdf", pdf)
    if not pdf.content.startswith(b"%PDF"):
        raise SystemExit("Missouri linked resource is not a PDF")
    reader = PdfReader(io.BytesIO(pdf.content), strict=False)
    texts = [(page.extract_text(extraction_mode="layout") or "") for page in reader.pages]
    text = "\n".join(texts)
    folded = text.casefold()
    lines = [line for line in text.splitlines() if line.strip()]
    date_re = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
    date_lines = [line for line in lines if date_re.search(line)]
    multi_date_lines = [line for line in date_lines if len(date_re.findall(line)) >= 2]
    print(f"[MO pdf] pages={len(reader.pages)} text_chars={len(text)} nonempty_lines={len(lines)} date_lines={len(date_lines)} multi_date_lines={len(multi_date_lines)} tokens={{'suspended':{'suspend' in folded},'debarred':{'debar' in folded},'effective':{'effective' in folded},'vendor':{'vendor' in folded},'reason':{'reason' in folded}}}")


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
