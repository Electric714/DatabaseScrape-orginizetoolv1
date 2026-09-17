"""Temporary structure-only probe for official PDF sources; removed before merge."""
from __future__ import annotations
import io, re
from urllib.parse import parse_qs, unquote, urljoin, urlsplit
import httpx
from bs4 import BeautifulSoup
from pypdf import PdfReader

UA = "ParalegalResearchDesk-POC-OfficialSourceValidation/1.0"
MO_PAGE = "https://purch.oa.mo.gov/media/pdf/suspendeddebarred-vendors"
WI_CC = "https://doa.wi.gov/Documents/DEO/WOCCELIIneligible.pdf"
WI_TAX = "https://doa.wi.gov/Documents/DEO/CertList.pdf"


def get(client, url):
    r = client.get(url)
    print(f"GET {urlsplit(url).hostname}{urlsplit(url).path} {r.status_code} {r.headers.get('content-type','').split(';')[0]} {len(r.content)}")
    r.raise_for_status()
    return r


def pdf_text(body):
    if not body.startswith(b"%PDF"):
        raise ValueError("not pdf")
    reader = PdfReader(io.BytesIO(body), strict=False)
    text = "\n".join((p.extract_text(extraction_mode="layout") or "") for p in reader.pages)
    return reader, text

with httpx.Client(headers={"User-Agent": UA}, timeout=30, follow_redirects=True, trust_env=False) as client:
    landing = get(client, MO_PAGE)
    soup = BeautifulSoup(landing.text, "lxml")
    candidates=[]
    for a in soup.find_all("a", href=True):
        href=urljoin(str(landing.url), a["href"])
        candidates.append(href)
        p=urlsplit(href)
        file=(parse_qs(p.query).get("file") or [""])[0]
        if file: candidates.append(unquote(file))
    candidates += re.findall(r"https?://[^\"'<>\s]+\.pdf", landing.text, re.I)
    pdfs=[]
    for candidate in candidates:
        candidate=candidate.replace("&amp;","&")
        p=urlsplit(candidate)
        if p.hostname == "purch.oa.mo.gov" and p.path.lower().endswith(".pdf") and "suspven" in p.path.lower():
            pdfs.append(candidate)
    pdfs=list(dict.fromkeys(pdfs))
    print("MO pdfs", [urlsplit(u).path for u in pdfs])
    if len(pdfs)!=1: raise ValueError("Missouri PDF not uniquely identified")
    reader,text=pdf_text(get(client,pdfs[0]).content)
    print("MO pages",len(reader.pages))
    lines=[line.rstrip() for line in text.splitlines() if line.strip()]
    print("MO headings",[re.sub(r"\s+"," ",x).strip() for x in lines[:20]])
    dated=[]
    for line in lines:
        if re.search(r"\d{1,2}/\d{1,2}/\d{4}",line):
            parts=[p.strip() for p in re.split(r"\s{2,}",line.strip()) if p.strip()]
            if len(dated)<20: dated.append(parts)
    print("MO dated rows",dated)

    for label,url in (("WI_CC",WI_CC),("WI_TAX",WI_TAX)):
        reader,text=pdf_text(get(client,url).content)
        print(label,"pages",len(reader.pages))
        lines=[re.sub(r"\s+"," ",x).strip() for x in text.splitlines() if x.strip()]
        print(label,"first",lines[:20])
        if label=="WI_CC":
            print(label,"removed_count",len(re.findall(r"REMOVED FROM INELIGIBLE LIST",text,re.I)))
        else:
            m=re.search(r"Vendors Not In Compliance With Sec\.\s*77\.66.*?as of ([A-Za-z]+ \d{1,2}, \d{4})",re.sub(r"\s+"," ",text),re.I)
            print(label,"as_of",m.group(1) if m else None)
