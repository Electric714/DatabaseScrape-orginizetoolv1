import os
import httpx
import pytest
from app.crawler import CrawlEngine


def test_live_gui_smoke(database, monkeypatch):
    import socket
    import threading
    import time
    import uvicorn
    from playwright.sync_api import sync_playwright
    from app import main
    from fixture_site import DirectorySite
    site = DirectorySite()
    class FixtureEngine(CrawlEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, transport=httpx.MockTransport(site))
    async def public(url):
        return ["93.184.216.34"]
    monkeypatch.setattr(main, "CrawlEngine", FixtureEngine)
    monkeypatch.setattr(main, "validate_public_url", public)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(main.app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.01)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}")
                page.locator("#sourceName").fill("GUI fixture")
                page.locator("#sourceUrl").fill("https://fixture.test/")
                page.locator("summary").click()
                page.locator("#renderMode").select_option("http")
                page.locator("#delayMs").fill("0")
                page.get_by_role("button", name="Add source", exact=True).click()
                page.get_by_role("button", name="Scan", exact=True).wait_for()
                page.get_by_role("button", name="Scan", exact=True).click()
                page.get_by_role("cell", name="Jane Doe", exact=True).wait_for(timeout=15000)
                page.locator("#sourceFilter").select_option(label="GUI fixture")
                page.wait_for_timeout(3500)
                assert page.locator("#sourceFilter option:checked").inner_text() == "GUI fixture"
                assert not errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()

pytestmark = pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Set RUN_BROWSER_TESTS=1 after installing Chromium")


async def test_actual_javascript_rendering_and_cookies(source):
    requests = []
    def site(request):
        requests.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(200, text='<div id="root"></div><script src="/app.js"></script>',
                                  headers={"content-type": "text/html", "set-cookie": "session=fixture; Path=/"})
        if request.url.path == "/app.js":
            return httpx.Response(200, text="fetch('/data').then(r=>r.json()).then(d=>{document.getElementById('root').innerHTML='<dl><dt>Name</dt><dd>'+d.name+'</dd><dt>Phone</dt><dd>6085551111</dd></dl>';})",
                                  headers={"content-type": "text/javascript"})
        if request.url.path == "/data":
            assert "session=fixture" in request.headers.get("cookie", "")
            return httpx.Response(200, json={"name": "Rendered Jane"})
        return httpx.Response(404)
    engine = CrawlEngine({**source, "render_mode": "auto"}, 0)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as client:
            result = await engine._fetch(client, source["start_url"], None)
        assert result.rendered
        assert "Rendered Jane" in result.text
        assert "/data" in requests
    finally:
        await engine.renderer.close()


async def test_browser_internal_resource_blocked(source):
    requests = []
    def site(request):
        requests.append(str(request.url))
        return httpx.Response(200, text='<script src="http://127.0.0.1/private.js"></script>', headers={"content-type": "text/html"})
    engine = CrawlEngine({**source, "render_mode": "auto"}, 0)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as client:
            with pytest.raises(ValueError, match="Rendering incomplete"):
                await engine._fetch(client, source["start_url"], None)
        assert all("127.0.0.1" not in url for url in requests)
    finally:
        await engine.renderer.close()
