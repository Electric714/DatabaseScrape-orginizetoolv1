"""Temporary structure-only probe for official dynamic sources; removed before merge."""
from __future__ import annotations
import re
from urllib.parse import urljoin, urlsplit
import httpx
from bs4 import BeautifulSoup

UA = "ParalegalResearchDesk-POC-OfficialSourceValidation/1.0"
URLS = {
    "fl_suspended": "https://www.dms.myflorida.com/business_operations/state_purchasing/state_agency_resources/vendor_registration_and_vendor_lists/suspended_vendor_list",
    "fl_convicted": "https://www.dms.myflorida.com/business_operations/state_purchasing/state_agency_resources/vendor_registration_and_vendor_lists/convicted_vendor_list",
    "wi_dor": "https://www.revenue.wi.gov/Pages/Delqlist/DelqSearch.aspx",
}

with httpx.Client(headers={"User-Agent": UA}, timeout=20, follow_redirects=True, trust_env=False) as client:
    for label,url in URLS.items():
        try:
            r=client.get(url)
            print(f"GET {label} {r.status_code} {r.headers.get('content-type','').split(';')[0]} {len(r.content)}")
            r.raise_for_status()
        except Exception as exc:
            print(label,"ERROR",type(exc).__name__)
            continue
        soup=BeautifulSoup(r.text,"lxml")
        scripts=[]
        for s in soup.find_all("script"):
            if s.get("src"):
                scripts.append(urljoin(str(r.url),s["src"]))
        print(label,"script_hosts",sorted({urlsplit(x).hostname for x in scripts}),"script_paths",[urlsplit(x).path for x in scripts if urlsplit(x).hostname==urlsplit(url).hostname][-12:])
        needles=("liability_amt","Building Maintenance of America","There are currently no vendors on this list","DelqSearch","delq","ajax","api")
        for s in soup.find_all("script"):
            text=s.string or s.get_text(" ")
            low=text.casefold()
            hits=[n for n in needles if n.casefold() in low]
            if not hits: continue
            compact=re.sub(r"\s+"," ",text)
            indexes=[low.find(n.casefold()) for n in hits if low.find(n.casefold())>=0]
            index=min(indexes) if indexes else 0
            start=max(0,index-600); end=min(len(compact),index+1800)
            print(label,"inline_hits",hits,"snippet",compact[start:end])
        for src in scripts:
            if urlsplit(src).hostname != urlsplit(url).hostname: continue
            try:
                js=client.get(src)
                if js.status_code!=200 or len(js.content)>3_000_000: continue
            except Exception: continue
            low=js.text.casefold()
            hits=[n for n in needles if n.casefold() in low]
            if not hits: continue
            index=min(low.find(n.casefold()) for n in hits if low.find(n.casefold())>=0)
            print(label,"external_hits",urlsplit(src).path,hits,"snippet",re.sub(r"\s+"," ",js.text[max(0,index-700):index+2200]))
