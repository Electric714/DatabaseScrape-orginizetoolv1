"""Temporary live-source probe for FL/MO/OH official debarment resources.

Prints only transport and document structure. It intentionally does not log vendor
names, addresses, IDs, or contractor data.
"""
from __future__ import annotations

import io
import re
from collections import Counter
from html import unescape
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
    print(f"[{label}] common_classes={classes.most_common(10)}")
    return soup


def script_shape(script_text: str) -> str:
    prefix = script_text[:180]
    return re.sub(r"[A-Za-z0-9]", "x", prefix)


def embedded_html_shape(label: str, script_text: str) -> None:
    decoded = unescape(script_text).replace('\\"', '"').replace("\\n", "\n")
    embedded = BeautifulSoup(decoded, "lxml")
    print(
        f"[{label}] embedded tags={{'table':{len(embedded.find_all('table'))},'tr':{len(embedded.find_all('tr'))},"
        f"'li':{len(embedded.find_all('li'))},'p':{len(embedded.find_all('p'))},'a':{len(embedded.find_all('a'))}}}"
    )
    tables = embedded.find_all("table")
    for index, table in enumerate(tables[:3]):
        headers = [cell.get_text(" ", strip=True) for cell in table.find_all(["th", "td"])[:4]]
        safe_headers = [value for value in headers if value in {"Vendor Name/Address", "Agency of Origin", "Effective Date", "Notice of Default"}]
        print(f"[{label}] embedded_table{index}_rows={len(table.find_all('tr'))} recognized_headers={safe_headers}")


def probe_florida(client: httpx.Client) -> None:
    for label, url in [("FL suspended", FL_SUSPENDED), ("FL convicted", FL_CONVICTED)]:
        response = client.get(url)
        summary(label, response)
        soup = html_structure(label, response)
        scripts = [s.get_text() for s in soup.find_all("script") if s.get_text()]
        marker_scripts = [value for value in scripts if "Vendor Name/Address" in value or "no vendors" in value.casefold()]
        print(f"[{label}] scripts={len(scripts)} marker_scripts={len(marker_scripts)}")
        for index, value in enumerate(marker_scripts[:2]):
            print(f"[{label}] marker_script{index}_len={len(value)} attrs_shape={script_shape(value)!r}")
            embedded_html_shape(label, value)


def probe_missouri(client: httpx.Client) -> None:
    response = client.get(MO_PAGE)
    summary("MO landing", response)
    soup = html_structure("MO landing", response)
    candidates = []
    for anchor in soup.find_all("a", href=True):
        href = urljoin(str(response.url), anchor["href"])
        if ".pdf" in href.casefold():
            candidates.append((anchor.get_text(" ", strip=True), href))
    raw_pdf_paths = sorted(set(re.findall(r"(?:https?://[^\"'<>\s]+|/[^\"'<>\s]+)\.pdf", response.text, re.I)))
    print(f"[MO landing] anchor_pdf_links={len(candidates)} raw_pdf_paths={len(raw_pdf_paths)}")
    for path in raw_pdf_paths:
        print(f"[MO landing] raw_pdf_path host={urlsplit(urljoin(str(response.url), path + '.pdf')).hostname} path={urlsplit(urljoin(str(response.url), path + '.pdf')).path}")
    target_url = None
    for text, href in candidates:
        if "suspven" in (text + href).casefold() or "suspend" in text.casefold() or "debar" in text.casefold():
            target_url = href
            break
    if target_url is None:
        raw_target = next((path + ".pdf" for path in raw_pdf_paths if "suspven" in path.casefold()), None)
        if raw_target:
            target_url = urljoin(str(response.url), raw_target)
    if target_url is None:
        print("[MO] acquisition_target_unresolved=true")
        return
    pdf = client.get(target_url)
    summary("MO pdf", pdf)
    if not pdf.content.startswith(b"%PDF"):
        print("[MO pdf] valid_pdf=false")
        return
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
        response = client.get(url)
        summary(f"OH {index}", response)
        if response.status_code >= 400:
            continue
        soup = html_structure(f"OH {index}", response)
        text = soup.get_text(" ", strip=True).casefold()
        links = [urljoin(str(response.url), a["href"]) for a in soup.find_all("a", href=True)]
        csv_links = [href for href in links if ".csv" in href.casefold()]
        print(f"[OH {index}] tokens={{'debarment':{'debar' in text},'vendor':{'vendor' in text},'supplier':{'supplier' in text}}} csv_links={len(csv_links)}")
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
