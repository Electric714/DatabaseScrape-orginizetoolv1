from urllib.parse import parse_qs

import httpx

from app import security
from app.adapters import adapter_for_url
from app.bbb_adapter import BbbComplaintsAdapter
from app.source_catalog import SOURCE_CATALOG


async def test_dol_api_key_moves_to_documented_query_parameter_at_egress(monkeypatch):
    observed = {}

    async def resolve(url):
        observed["validated_url"] = url
        return ["93.184.216.34"]

    async def send(self, request):
        observed["wire_url"] = str(request.url)
        observed["headers"] = dict(request.headers)
        assert request.url.host == "93.184.216.34"
        assert request.headers["host"] == "apiprod.dol.gov"
        assert request.extensions["sni_hostname"] == "apiprod.dol.gov"
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(security, "validate_public_url", resolve)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send)

    logical_url = "https://apiprod.dol.gov/v4/get/OSHA/inspection/json?limit=1"
    async with httpx.AsyncClient(transport=security.PublicTransport()) as client:
        response = await client.get(
            logical_url,
            headers={"X-API-KEY": "synthetic-dol-key", "Accept": "application/json"},
        )

    assert response.status_code == 200
    assert "x-api-key" not in observed["headers"]
    assert parse_qs(httpx.URL(observed["wire_url"]).query.decode())["X-API-KEY"] == ["synthetic-dol-key"]
    assert parse_qs(httpx.URL(observed["validated_url"]).query.decode())["X-API-KEY"] == ["synthetic-dol-key"]
    assert str(response.url) == logical_url
    assert "synthetic-dol-key" not in str(response.url)


def test_bbb_stays_fail_closed_until_supported_acquisition_is_validated():
    adapter = adapter_for_url("https://www.bbb.org/search")
    assert isinstance(adapter, BbbComplaintsAdapter)
    assert not getattr(adapter, "direct_browser", False)
    assert adapter.fail_fast_access_errors is True

    catalog = next(source for source in SOURCE_CATALOG if source["key"] == "bbb")
    assert catalog["status"] == "Blocked before parsing"
    assert "access challenge" in catalog["note"]
    assert "robots" in catalog["note"].lower()
