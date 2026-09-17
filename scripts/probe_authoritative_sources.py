"""Temporary structure-only probe for Wisconsin DOR request shape; removed before merge."""
from __future__ import annotations
import httpx
from urllib.parse import urlsplit

API = "https://ww2.revenue.wi.gov/WebServicesPublicWeb/rest/delinquents/all"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36"
variants = [
    {"search":"ZZZNOSUCHVENDOR987654", "city":"", "rn_low":-1, "rn_high":250, "sort":"name", "revoked":"false"},
    {"search":"ZZZNOSUCHVENDOR987654", "city":"", "rn_low":-1, "rn_high":250, "sort":"", "revoked":"false"},
    {"search":"LLC", "city":"", "rn_low":-1, "rn_high":1, "sort":"name", "revoked":"false"},
]
with httpx.Client(headers={"User-Agent":UA,"Accept":"application/json, text/javascript, */*; q=0.01","Referer":"https://www.revenue.wi.gov/Pages/Delqlist/DelqSearch.aspx","X-Requested-With":"XMLHttpRequest"}, timeout=20, follow_redirects=False, trust_env=False) as client:
    for i,params in enumerate(variants):
        try:
            response=client.get(API,params=params)
            print("variant",i,"status",response.status_code,"type",response.headers.get("content-type",""),"bytes",len(response.content))
            if response.status_code != 200:
                print("body_prefix",response.text[:160].replace("\n"," "))
                continue
            payload=response.json(); subjects=payload.get("subject") or []
            print("result",payload.get("result"),"count_type",type(payload.get("count")).__name__,"subjects",len(subjects),"keys",sorted(subjects[0]) if subjects else [])
            if subjects:
                print("types",{k:type(v).__name__ for k,v in subjects[0].items()})
        except Exception as exc:
            print("variant",i,"error",type(exc).__name__)
