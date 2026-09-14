"""Temporary structure-only probe for Missouri's official debarment PDF."""
from __future__ import annotations

import io
import re
from urllib.parse import urljoin, urlsplit

import httpx
from pypdf import PdfReader

MO_PAGE = "https://purch.oa.mo.gov/media/pdf/suspendeddebarred-vendors"
OH_URLS = [
    "https://procure.ohio.gov/state-and-local-agencies/resources/08_debarment-csv-list",
    "https://procure.ohio.gov/bidders-and-suppliers/resources/08_debarment-csv-list",
]


def shape(line: str) -> str:
    value = re.sub(r"[A-Za-z]", "X", line)
    value = re.sub(r"\d", "9", value)
    return value[:180]


def main() -> None:
    with httpx.Client(headers={"User-Agent": "ParalegalDatabaseTool-POC-SourceValidation/1.0"}, timeout=30.0, follow_redirects=True, trust_env=False) as client:
        landing = client.get(MO_PAGE)
        paths = sorted(set(re.findall(r"(?:https?://[^\"'<>\s]+\.pdf|/[^\"'<>\s]+\.pdf)", landing.text, re.I)))
        direct = []
        for raw in paths:
            candidate = urljoin(str(landing.url), raw)
            parsed = urlsplit(candidate)
            if "suspven" in parsed.path.casefold() and parsed.path.casefold().endswith(".pdf"):
                direct.append(candidate)
        if len(direct) != 1:
            raise ValueError("Expected one direct Missouri PDF")
        response = client.get(direct[0])
        if not response.content.startswith(b"%PDF"):
            raise ValueError("Missouri resource is not a PDF")
        reader = PdfReader(io.BytesIO(response.content), strict=False)
        pages = [page.extract_text(extraction_mode="layout") or "" for page in reader.pages]
        lines = [line for line in pages[0].splitlines() if line.strip()]
        date_re = re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b")
        print(f"[MO] pages={len(pages)} first_page_lines={len(lines)}")
        for index, line in enumerate(lines[:28]):
            print(f"[MO shape {index:02d}] len={len(line)} dates={len(date_re.findall(line))} gaps={len(re.findall(r' {2,}', line))} :: {shape(line)}")
        date_lines = [line for page in pages for line in page.splitlines() if date_re.search(line)]
        print(f"[MO] date_line_shapes={len(date_lines)}")
        for index, line in enumerate(date_lines[:8]):
            print(f"[MO date {index:02d}] len={len(line)} gaps={len(re.findall(r' {2,}', line))} :: {shape(line)}")
        statuses = [client.get(url).status_code for url in OH_URLS]
        print(f"[OH] documented_url_statuses={statuses}")


if __name__ == "__main__":
    main()
