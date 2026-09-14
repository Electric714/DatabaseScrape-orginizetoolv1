"""Temporary structure-only probe for Missouri's official vendor report."""
from __future__ import annotations
import io, re
from collections import Counter
from urllib.parse import urljoin, urlsplit
import httpx
from pypdf import PdfReader

MO_PAGE = "https://purch.oa.mo.gov/media/pdf/suspendeddebarred-vendors"
DATE = re.compile(r"\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{4}")

with httpx.Client(headers={"User-Agent": "ParalegalDatabaseTool-POC-SourceValidation/1.0"}, timeout=30, follow_redirects=True, trust_env=False) as client:
    landing = client.get(MO_PAGE)
    paths = set(re.findall(r"(?:https?://[^\"'<>\s]+\.pdf|/[^\"'<>\s]+\.pdf)", landing.text, re.I))
    urls = []
    for raw in paths:
        url = urljoin(str(landing.url), raw)
        parsed = urlsplit(url)
        if "suspven" in parsed.path.casefold() and parsed.path.casefold().endswith(".pdf"):
            urls.append(url)
    if len(urls) != 1:
        raise ValueError("Expected one direct report PDF")
    response = client.get(urls[0])
    reader = PdfReader(io.BytesIO(response.content), strict=False)
    lines = [line for page in reader.pages for line in (page.extract_text(extraction_mode="layout") or "").splitlines() if line.strip()]
    rows = []
    for line in lines:
        match = DATE.search(line)
        if not match:
            continue
        parts = [part.strip() for part in re.split(r"\s{2,}", line.strip()) if part.strip()]
        date_index = next((i for i, part in enumerate(parts) if DATE.search(part)), -1)
        rows.append((len(parts), date_index, tuple(len(part) for part in parts)))
    print(f"rows={len(rows)} part_counts={dict(Counter(r[0] for r in rows))} date_indexes={dict(Counter(r[1] for r in rows))}")
    for index, row in enumerate(rows[:12]):
        print(f"row{index:02d}: parts={row[0]} date_index={row[1]} lengths={row[2]}")
