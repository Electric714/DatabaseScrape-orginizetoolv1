from pathlib import Path


main = Path("app/main.py")
text = main.read_text(encoding="utf-8")

old = '''async def validate_dol_api_key(api_key: str) -> None:
    # Validate without placing the credential in a URL, source row, or activity log.
    test_url = DOL_INSPECTION_ENDPOINT + "?limit=1&fields=activity_nr"
    try:
        async with httpx.AsyncClient(
            transport=PublicTransport(),
            trust_env=False,
            follow_redirects=False,
            timeout=20.0,
        ) as client:
            response = await client.get(
                test_url,
                headers={"X-API-KEY": api_key, "Accept": "application/json"},
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ValueError("Could not reach the DOL Open Data API to test this key") from exc
    if response.status_code in {401, 403}:
        raise ValueError("DOL rejected this API key")
    if response.status_code < 200 or response.status_code >= 300:
        raise ValueError(f"DOL API key test failed with HTTP {response.status_code}")
    parse_dol_rows(response.text)


async def validate_sam_api_key(api_key: str) -> None:
    try:
        async with httpx.AsyncClient(
            transport=PublicTransport(),
            trust_env=False,
            follow_redirects=False,
            timeout=20.0,
        ) as client:
            response = await client.get(
                SAM_EXCLUSIONS_ENDPOINT,
                params={
                    "api_key": api_key,
                    "classification": "Firm",
                    "page": 0,
                    "size": 1,
                },
                headers={"Accept": "application/json"},
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ValueError("Could not reach the SAM.gov Exclusions API to test this key") from exc
    if response.status_code in {401, 403}:
        raise ValueError("SAM.gov rejected this API key")
    if response.status_code == 429:
        raise ValueError("SAM.gov rate limit reached; try the key again after the limit resets")
    if response.status_code < 200 or response.status_code >= 300:
        raise ValueError(f"SAM.gov API key test failed with HTTP {response.status_code}")
    parse_sam_payload(response.text)
'''

new = '''SAM_KEY_VALIDATION_FALLBACK = "https://api.sam.gov/contract-awards/v1/search"


def _normalized_api_key(value: str) -> str:
    key = (value or "").strip()
    if len(key) < 10:
        raise ValueError("API key appears incomplete")
    return key


async def validate_dol_api_key(api_key: str) -> dict:
    """Validate DOL authentication without depending on an OSHA data query."""
    key = _normalized_api_key(api_key)
    # DOL's v4 guide documents /json/metadata as the authentication-aware
    # metadata route. PublicTransport moves X-API-KEY into the documented query
    # parameter only at final network egress so the key never enters app URLs.
    test_url = DOL_INSPECTION_ENDPOINT + "/metadata"
    try:
        async with httpx.AsyncClient(
            transport=PublicTransport(),
            trust_env=False,
            follow_redirects=False,
            timeout=20.0,
        ) as client:
            response = await client.get(
                test_url,
                headers={"X-API-KEY": key, "Accept": "application/json"},
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ValueError("Could not reach the DOL Open Data API to test this key") from exc
    if response.status_code in {401, 403}:
        raise ValueError("DOL rejected this API key")
    if response.status_code == 429:
        raise ValueError("DOL API rate limit reached; try the key again after the limit resets")
    if response.status_code == 404 or response.status_code >= 500:
        raise ValueError(
            f"DOL authentication service is temporarily unavailable (HTTP {response.status_code}); "
            "the key was not marked invalid"
        )
    if response.status_code < 200 or response.status_code >= 300:
        raise ValueError(f"DOL API key test could not be completed (HTTP {response.status_code})")
    try:
        payload = json.loads(response.text)
    except json.JSONDecodeError as exc:
        raise ValueError("DOL accepted the request but returned an unreadable metadata response") from exc
    if not isinstance(payload, (dict, list)):
        raise ValueError("DOL accepted the request but returned an unexpected metadata response")
    return {"validation_service": "dol-metadata", "upstream_available": True}


async def validate_sam_api_key(api_key: str) -> dict:
    """Validate a SAM public API key while distinguishing service failure from bad auth."""
    key = _normalized_api_key(api_key)
    try:
        async with httpx.AsyncClient(
            transport=PublicTransport(),
            trust_env=False,
            follow_redirects=False,
            timeout=20.0,
        ) as client:
            response = await client.get(
                SAM_EXCLUSIONS_ENDPOINT,
                params={
                    "api_key": key,
                    "classification": "Firm",
                    "recordStatus": "Active",
                    "page": 0,
                    "size": 1,
                },
                headers={"Accept": "application/json"},
            )
            if 200 <= response.status_code < 300:
                if response.status_code != 204:
                    parse_sam_payload(response.text)
                return {
                    "validation_service": "sam-exclusions",
                    "exclusions_available": True,
                }
            if response.status_code in {401, 403}:
                raise ValueError("SAM.gov rejected this API key")
            if response.status_code == 429:
                raise ValueError("SAM.gov rate limit reached; try the key again after the limit resets")

            # OpenGSA still documents this exact Exclusions v4 URL, but clean live
            # requests currently can receive HTTP 404. If that happens, verify only
            # the credential against another official SAM public API using the same
            # Public API Key. Do not claim Exclusions itself is healthy.
            if response.status_code == 404 or response.status_code >= 500:
                fallback = await client.get(
                    SAM_KEY_VALIDATION_FALLBACK,
                    params={
                        "api_key": key,
                        "awardeeUniqueEntityId": "000000000000",
                        "limit": 1,
                        "offset": 0,
                    },
                    headers={"Accept": "application/json"},
                )
                if fallback.status_code in {401, 403}:
                    raise ValueError("SAM.gov rejected this API key")
                if fallback.status_code == 429:
                    raise ValueError("SAM.gov rate limit reached; try the key again after the limit resets")
                if fallback.status_code in {200, 204}:
                    return {
                        "validation_service": "sam-contract-awards-fallback",
                        "exclusions_available": False,
                        "warning": (
                            "SAM.gov accepted the API key, but its documented Exclusions endpoint "
                            f"returned HTTP {response.status_code}. The key was saved; Exclusions collection "
                            "may remain unavailable until SAM.gov restores that endpoint."
                        ),
                    }
                raise ValueError(
                    "SAM.gov public APIs are currently unavailable, so this key could not be validated; "
                    "the key was not marked invalid"
                )

            raise ValueError(f"SAM.gov API key test could not be completed (HTTP {response.status_code})")
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ValueError("Could not reach SAM.gov to test this key") from exc
'''

if old not in text:
    raise SystemExit("validator block did not match current main.py")
text = text.replace(old, new, 1)
text = text.replace(
    "from .osha_adapter import DOL_INSPECTION_ENDPOINT, _rows as parse_dol_rows",
    "from .osha_adapter import DOL_INSPECTION_ENDPOINT",
)

# Preserve validation metadata and always save the normalized value, not pasted whitespace.
text = text.replace(
'''async def configure_dol_integration(payload: DolApiKeyPayload):
    try:
        await validate_dol_api_key(payload.api_key)
        save_dol_api_key(payload.api_key)
''',
'''async def configure_dol_integration(payload: DolApiKeyPayload):
    key = _normalized_api_key(payload.api_key)
    try:
        validation = (await validate_dol_api_key(key)) or {}
        save_dol_api_key(key)
''', 1)
text = text.replace(
'''    return {"configured": True, "validated": True, "source_id": source["id"]}
''',
'''    return {"configured": True, "validated": True, "source_id": source["id"], **validation}
''', 1)
text = text.replace(
'''async def configure_sam_integration(payload: SamApiKeyPayload):
    try:
        await validate_sam_api_key(payload.api_key)
        save_sam_api_key(payload.api_key)
''',
'''async def configure_sam_integration(payload: SamApiKeyPayload):
    key = _normalized_api_key(payload.api_key)
    try:
        validation = (await validate_sam_api_key(key)) or {}
        save_sam_api_key(key)
''', 1)
# The SAM return is the next identical compact configured/validated response.
needle = '    return {"configured": True, "validated": True, "source_id": source["id"]}\n'
if needle in text:
    text = text.replace(needle, '    return {"configured": True, "validated": True, "source_id": source["id"], **validation}\n', 1)

main.write_text(text, encoding="utf-8")

js = Path("app/static/app.js")
body = js.read_text(encoding="utf-8")
body = body.replace(
    "const SAM_API_URL = 'https://api-alpha.sam.gov/entity-information/v4/exclusions';",
    "const SAM_API_URL = 'https://api.sam.gov/entity-information/v4/exclusions';",
)
body = body.replace("Built-in REST source · Alpha/test v4", "Built-in REST source · Production v4")
body = body.replace("Set test API key", "Set API key")
body = body.replace("Recollect SAM Alpha API records", "Recollect SAM production API records")

# Surface a successful fallback-validation warning without treating the warning itself as an error.
body = body.replace(
'''    await api('/api/integrations/sam', {method: 'POST', body: JSON.stringify({api_key: key})});
    notify('SAM.gov API key tested and saved.');
''',
'''    const result = await api('/api/integrations/sam', {method: 'POST', body: JSON.stringify({api_key: key})});
    notify(result.warning || 'SAM.gov API key tested and saved.');
''', 1)

if "api-alpha.sam.gov/entity-information/v4/exclusions" in body:
    raise SystemExit("stale SAM Alpha endpoint remains in app.js")
js.write_text(body, encoding="utf-8")
