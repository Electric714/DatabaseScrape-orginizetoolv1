"""Temporary structure-only probe for the official Wisconsin DOR delinquent-taxpayer API.
Removed before integration.
"""
from __future__ import annotations

import json
from urllib.parse import urlencode, urlsplit

import httpx

API = "https://ww2.revenue.wi.gov/WebServicesPublicWeb/rest/delinquents/all"
UA = "ParalegalResearchDesk-POC-OfficialSourceValidation/1.0"


def probe(client: httpx.Client, search: str, rn_high: int = 1) -> None:
    params = {
        "search": search,
        "city": "",
        "rn_low": 1,
        "rn_high": rn_high,
        "sort": "name",
        "revoked": "false",
    }
    response = client.get(API, params=params)
    print(
        "GET",
        urlsplit(str(response.url)).hostname,
        urlsplit(str(response.url)).path,
        response.status_code,
        response.headers.get("content-type", "").split(";")[0],
        len(response.content),
    )
    response.raise_for_status()
    payload = response.json()
    subjects = payload.get("subject") or []
    print(
        "result=", payload.get("result"),
        "count_type=", type(payload.get("count")).__name__,
        "subject_len=", len(subjects),
        "subject_keys=", sorted(subjects[0].keys()) if subjects else [],
    )
    if subjects:
        print("subject_types=", {key: type(value).__name__ for key, value in subjects[0].items()})


with httpx.Client(headers={"User-Agent": UA, "Accept": "application/json"}, timeout=20, follow_redirects=False, trust_env=False) as client:
    # Impossible query proves the no-match response shape without logging any taxpayer data.
    probe(client, "ZZZ-NO-SUCH-VENDOR-OPENAI-987654", 1)
    # A broad entity suffix is used only to inspect the returned field names/types; values are never logged.
    probe(client, "LLC", 1)
