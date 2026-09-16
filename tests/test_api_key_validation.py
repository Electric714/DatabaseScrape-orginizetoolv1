import json

import httpx
import pytest

from app import main


class FakeClient:
    responses = []
    requests = []

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url, **kwargs):
        self.__class__.requests.append((url, kwargs))
        if not self.__class__.responses:
            raise AssertionError("unexpected HTTP request")
        status, payload = self.__class__.responses.pop(0)
        if isinstance(payload, (dict, list)):
            text = json.dumps(payload)
        else:
            text = str(payload)
        return httpx.Response(status, text=text, request=httpx.Request("GET", url))


def install_client(monkeypatch, responses):
    FakeClient.responses = list(responses)
    FakeClient.requests = []
    monkeypatch.setattr(main.httpx, "AsyncClient", FakeClient)
    return FakeClient


@pytest.mark.asyncio
async def test_dol_validation_trims_key_and_uses_metadata_endpoint(monkeypatch):
    client = install_client(monkeypatch, [(200, {"dataset": "inspection"})])

    result = await main.validate_dol_api_key("  synthetic-dol-key  ")

    assert result == {"validation_service": "dol-metadata", "upstream_available": True}
    assert len(client.requests) == 1
    url, kwargs = client.requests[0]
    assert url.endswith("/v4/get/OSHA/inspection/json/metadata")
    assert "limit=" not in url
    assert kwargs["headers"]["X-API-KEY"] == "synthetic-dol-key"


@pytest.mark.asyncio
async def test_dol_service_404_is_not_reported_as_bad_key(monkeypatch):
    install_client(monkeypatch, [(404, "not found")])

    with pytest.raises(ValueError, match="not marked invalid"):
        await main.validate_dol_api_key("synthetic-dol-key")


@pytest.mark.asyncio
async def test_dol_401_is_reported_as_rejected_key(monkeypatch):
    install_client(monkeypatch, [(401, "invalid")])

    with pytest.raises(ValueError, match="DOL rejected this API key"):
        await main.validate_dol_api_key("synthetic-dol-key")


@pytest.mark.asyncio
async def test_sam_validation_uses_production_exclusions_and_trims_key(monkeypatch):
    client = install_client(monkeypatch, [(200, {"totalRecords": 0, "excludedEntity": []})])

    result = await main.validate_sam_api_key("  synthetic-sam-key  ")

    assert result["validation_service"] == "sam-exclusions"
    assert result["exclusions_available"] is True
    assert len(client.requests) == 1
    url, kwargs = client.requests[0]
    assert url == main.SAM_EXCLUSIONS_ENDPOINT
    assert kwargs["params"]["api_key"] == "synthetic-sam-key"
    assert kwargs["params"]["recordStatus"] == "Active"


@pytest.mark.asyncio
async def test_sam_exclusions_404_falls_back_to_official_public_api(monkeypatch):
    client = install_client(monkeypatch, [
        (404, ""),
        (204, ""),
    ])

    result = await main.validate_sam_api_key("synthetic-sam-key")

    assert result["validation_service"] == "sam-contract-awards-fallback"
    assert result["exclusions_available"] is False
    assert "accepted the API key" in result["warning"]
    assert len(client.requests) == 2
    fallback_url, fallback_kwargs = client.requests[1]
    assert fallback_url == main.SAM_KEY_VALIDATION_FALLBACK
    assert fallback_kwargs["params"]["api_key"] == "synthetic-sam-key"
    assert fallback_kwargs["params"]["awardeeUniqueEntityId"] == "000000000000"


@pytest.mark.asyncio
async def test_sam_403_is_rejected_without_fallback(monkeypatch):
    client = install_client(monkeypatch, [(403, {"message": "invalid key"})])

    with pytest.raises(ValueError, match="SAM.gov rejected this API key"):
        await main.validate_sam_api_key("synthetic-sam-key")

    assert len(client.requests) == 1


@pytest.mark.asyncio
async def test_sam_fallback_outage_does_not_call_key_invalid(monkeypatch):
    install_client(monkeypatch, [
        (404, ""),
        (500, "unavailable"),
    ])

    with pytest.raises(ValueError, match="could not be validated") as exc:
        await main.validate_sam_api_key("synthetic-sam-key")

    assert "rejected" not in str(exc.value).lower()
