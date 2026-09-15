from urllib.parse import parse_qs

import httpx

from app import security
from app.adapters import adapter_for_url
from app.bbb_adapter import BbbComplaintsAdapter


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

    async with httpx.AsyncClient(transport=security.PublicTransport()) as client:
        response = await client.get(
            "https://apiprod.dol.gov/v4/get/OSHA/inspection/json?limit=1",
            headers={"X-API-KEY": "synthetic-dol-key", "Accept": "application/json"},
        )

    assert response.status_code == 200
    assert "X-API-KEY" not in {name.upper(): value for name, value in observed["headers"].items()}
    assert "x-api-key" not in observed["headers"]
    assert parse_qs(httpx.URL(observed["wire_url"]).query.decode())["X-API-KEY"] == ["synthetic-dol-key"]
    assert parse_qs(httpx.URL(observed["validated_url"]).query.decode())["X-API-KEY"] == ["synthetic-dol-key"]


def test_bbb_registered_adapter_uses_local_browser_and_keeps_fail_closed_behavior():
    adapter = adapter_for_url("https://www.bbb.org/search")
    assert isinstance(adapter, BbbComplaintsAdapter)
    assert adapter.direct_browser is True
    assert adapter.fail_fast_access_errors is True
    assert adapter.allowed_url("https://www.bbb.org/search?find_text=Example+Builders")
    assert not adapter.allowed_url("https://example.com/search")
