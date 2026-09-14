"""Temporary live-source probe for FL/MO/OH official debarment resources.

Prints only transport and document structure. It intentionally does not log vendor
names, addresses, IDs, or contractor data.
"""
from __future__ import annotations

import io
import json
import re
from collections import Counter
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
STATIC_MARKERS = ("Vendor Name/Address", "Agency of Origin", "Effective Date", "Notice of Default")


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
    print(f"[{label}] common_classes={classes.most_common(8)}")
    return soup


def _walk_strings(value, path="$", depth=0):
    if depth > 14:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _walk_strings(item, f"{path}.{key}", depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value[:50]):
            yield from _walk_strings(item, f"{path}[{index}]", depth + 1)
    elif isinstance(value, str):
        yield path, value


def _container_at(value, path: str):
    # Probe helper only: find the nearest container by walking path tokens.
    current = value
    tokens = re.findall(r"\.([^\.\[]+)|\[(\d+)\]", path[1:])
    for key, index in tokens[:-1]:
        current = current[int(index)] if index else current[key]
    return current


def probe_florida(client: httpx.Client) -> None:
    for label, url in [("FL suspended", FL_SUSPENDED), ("FL convicted", FL_CONVICTED)]:
        response = client.get(url)
        summary(label, response)
        soup = html_structure(label, response)
        scripts = [s.get_text() for s in soup.find_all("script") if s.get_text().lstrip().startswith("{")]
        marker_scripts = [value for value in scripts if "Vendor Name/Address" in value or "no vendors" in value.casefold()]
        print(f"[{label}] json_scripts={len(scripts)} marker_scripts={len(marker_scripts)}")
        if len(marker_scripts) != 1:
            print(f"[{label}] json_shape_unresolved=true")
            continue
        try:
            payload = json.loads(marker_scripts[0])
        except json.JSONDecodeError:
            print(f"[{label}] json_parse=false")
            continue
        print(f"[{label}] top_keys={sorted(payload.keys())}")
        hits = []
        for path, text in _walk_strings(payload):
            folded = text.casefold()
            if any(marker.casefold() in folded for marker in STATIC_MARKERS) or "no vendors" in folded:
                hits.append((path, text))
        print(f"[{label}] marker_value_paths={[path for path, _ in hits]}")
        for path, text in hits[:8]:
            container = _container_at(payload, path)
            print(
                f"[{label}] path={path} string_len={len(text)} starts_markup={text.lstrip().startswith('<')} "
                f"container_type={type(container).__name__} container_keys={sorted(container.keys()) if isinstance(container, dict) else None}"
            )
            if "<" in text and ">" in text:
                embedded = BeautifulSoup(text, "lxml")
                tables = embedded.find_all("table")
                recognized = []
                for marker in STATIC_MARKERS:
                    if marker.casefold() in embedded.get_text(" ", strip=True).casefold():
                        recognized.append(marker)
                print(f"[{label}] embedded tables={len(tables)} rows={sum(len(t.find_all('tr')) for t in tables)} recognized={recognized}")


def probe_missouri(client: httpx.Client) -> None:
    response = client.get(MO_PAGE)
    summary("MO landing", response)
    html_structure("MO landing", response)
    pdf_paths = sorted(set(re.findall(r"(?:https?://[^\"'<>\s]+\.pdf|/[^\"'<>\s]+\.pdf)", response.text, re.I)))
    target = next((path for path in pdf_paths if "suspven" in path.casefold()), None)
    print(f"[MO landing] pdf_paths={len(pdf_paths)} targeted_path_found={bool(target)}")
    if not target:
        print("[MO] acquisition_target_unresolved=true")
        return
    pdf_url = urljoin(str(response.url), target)
    pdf = client.get(pdf_url)
    summary("MO pdf", pdf)
    if not pdf.content.startswith(b"%PDF"):
        print("[MO pdf] valid_pdf=false")
        return
    reader = PdfReader(io.BytesIO(pdf.content), strict=False)
    text = "\n".join((page.extract_text(extraction_mode="layout") or "") for page in reader.pages)
    folded = text.casefold()
    lines = [line for line in text.splitlines() if line.strip()]
    date_re = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
    date_lines = [line for line in lines if date_re.search(line)]
    multi_date_lines = [line for line in date_lines if len(date_re.findall(line)) >= 2]
    print(
        f"[MO pdf] pages={len(reader.pages)} text_chars={len(text)} nonempty_lines={len(lines)} "
        f"date_lines={len(date_lines)} multi_date_lines={len(multi_date_lines)} "
        f"tokens={{'suspended':{'suspend' in folded},'debarred':{'debar' in folded},'effective':{'effective' in folded},"
        f"'vendor':{'vendor' in folded},'reason':{'reason' in folded},'address':{'address' in folded}}}"
    )


def probe_ohio(client: httpx.Client) -> None:
    success = False
    for index, url in enumerate(OH_CANDIDATES):
        response = client.get(url)
        summary(f"OH {index}", response)
        if response.status_code < 400:
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
