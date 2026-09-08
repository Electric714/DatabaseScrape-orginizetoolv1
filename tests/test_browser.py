import os
import httpx
import pytest
from app.crawler import CrawlEngine
from app.bidder_schema import BIDDER_COLUMNS


def test_live_gui_smoke(database, monkeypatch):
    import socket
    import threading
    import time
    import uvicorn
    from playwright.sync_api import sync_playwright
    from app import main
    from fixture_site import DirectorySite
    class ResearchDirectorySite(DirectorySite):
        def profile(self, identifier, name, phone):
            # Synthetic research evidence, never a claim about a real business.
            owner = "Sample Owner" if identifier == "P1" else "Other Owner"
            status = "Open" if identifier == "P1" else ""
            return ("<table><tr><th>Record ID</th><th>Person name</th><th>Business name</th>"
                    "<th>Owner</th><th>Address</th><th>Location</th><th>OSHA status</th><th>OSHA violations</th></tr>"
                    f"<tr><td>{identifier}</td><td>{name}</td><td>Example Builders {identifier}</td>"
                    f"<td>{owner}</td><td>12 Oak Rd</td><td>Madison, WI</td><td>{status}</td>"
                    "<td>Illustrative fixture only</td></tr></table>")
    site = ResearchDirectorySite()
    class FixtureEngine(CrawlEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, transport=httpx.MockTransport(site))
    async def public(url):
        return ["93.184.216.34"]
    monkeypatch.setattr(main, "CrawlEngine", FixtureEngine)
    monkeypatch.setattr(main, "validate_public_url", public)
    monkeypatch.setattr(main, "dol_api_key_configured", lambda: False)
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
                page = browser.new_page(viewport={"width": 1440, "height": 1100}, device_scale_factor=1)
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                page.goto(f"http://127.0.0.1:{port}")
                from playwright.sync_api import expect
                expect(page.locator("#uploadCsvButton")).to_be_visible()
                expect(page.locator("#uploadCsvButton")).to_have_text("Upload master CSV")
                assert page.locator("#sourceSection").count() == 1
                expect(page.get_by_role("heading", name="OSHA / DOL Enforcement API")).to_be_visible()
                expect(page.get_by_role("button", name="Set API key")).to_be_visible()
                expect(page.get_by_role("button", name="Collect OSHA")).to_be_disabled()
                page.get_by_role("button", name="Set API key").click()
                expect(page.locator("#dolKeyDialog")).to_be_visible()
                expect(page.get_by_role("button", name="Test & save API key")).to_be_visible()
                page.get_by_role("button", name="Cancel").last.click()
                table_layout = page.evaluate("""() => {
                    const wrap = document.querySelector('.bidder-table-wrap');
                    const table = document.querySelector('.bidder-table');
                    const first = table.querySelector('th:nth-child(1)');
                    const second = table.querySelector('th:nth-child(2)');
                    return {
                        layout: getComputedStyle(table).tableLayout,
                        scrollWidth: wrap.scrollWidth,
                        clientWidth: wrap.clientWidth,
                        firstWidth: first.getBoundingClientRect().width,
                        secondWidth: second.getBoundingClientRect().width
                    };
                }""")
                assert table_layout["layout"] == "auto"
                assert table_layout["scrollWidth"] > table_layout["clientWidth"] * 2
                assert table_layout["firstWidth"] >= 80
                assert table_layout["secondWidth"] >= 240
                page.locator("[data-open-source]").first.click()
                page.locator("#sourceName").fill("GUI fixture")
                page.locator("#sourceUrl").fill("https://fixture.test/")
                page.locator("summary").click()
                page.locator("#renderMode").select_option("http")
                page.locator("#delayMs").fill("0")
                page.get_by_role("button", name="Save research site", exact=True).click()
                page.get_by_role("button", name="Collect records", exact=True).wait_for()
                import csv, io
                baseline_one = {column: "" for column in BIDDER_COLUMNS}
                baseline_one.update({"id":"P1","contractor_name":"Example Builders P1","address_1":"12 Oak Rd",
                                     "city":"Madison","state":"WI","osha":"N"})
                baseline_two = {column: "" for column in BIDDER_COLUMNS}
                baseline_two.update({"id":"P2","contractor_name":"Example Builders P2","address_1":"12 Oak Rd",
                                     "city":"Madison","state":"WI"})
                stream = io.StringIO()
                writer = csv.DictWriter(stream, fieldnames=BIDDER_COLUMNS)
                writer.writeheader()
                writer.writerows([baseline_one, baseline_two])
                page.locator("#csvUpload").set_input_files({
                    "name":"baseline.csv","mimeType":"text/csv","buffer":stream.getvalue().encode()
                })
                expect(page.locator("#masterCount")).to_have_text("2", timeout=10000)
                expect(page.locator("#uploadCsvButton")).to_have_text("Import another CSV", timeout=10000)
                expect(page.locator("#records")).to_contain_text("Example Builders P1", timeout=10000)
                sticky_positions = page.evaluate("""() => {
                    const wrap = document.querySelector('.bidder-table-wrap');
                    wrap.scrollLeft = wrap.scrollWidth;
                    const box = wrap.getBoundingClientRect();
                    const first = document.querySelector('.bidder-table tbody td:nth-child(1)').getBoundingClientRect();
                    const second = document.querySelector('.bidder-table tbody td:nth-child(2)').getBoundingClientRect();
                    return {
                        firstLeft: Math.round(first.left - box.left),
                        secondLeft: Math.round(second.left - box.left),
                        firstBg: getComputedStyle(document.querySelector('.bidder-table tbody td:nth-child(1)')).backgroundColor,
                        secondBg: getComputedStyle(document.querySelector('.bidder-table tbody td:nth-child(2)')).backgroundColor
                    };
                }""")
                assert abs(sticky_positions["firstLeft"]) <= 2
                assert 80 <= sticky_positions["secondLeft"] <= 85
                assert sticky_positions["firstBg"] != "rgba(0, 0, 0, 0)"
                assert sticky_positions["secondBg"] != "rgba(0, 0, 0, 0)"

                page.get_by_role("button", name="Collect records", exact=True).click()
                expect(page.locator("#sourceRecordCount")).to_have_text("2", timeout=15000)
                page.locator("#compareButton").click()
                expect(page.locator("#proposals")).to_contain_text("osha", timeout=10000)
                expect(page.locator("#proposals")).to_contain_text("N")
                expect(page.locator("#proposals")).to_contain_text("Y")
                page.locator("[data-apply-proposal]").first.click()
                expect(page.locator("#pendingUpdateCount")).to_have_text("0", timeout=10000)

                page.locator("#fieldFilter").select_option("contractor")
                page.locator("#search").fill("Example Builders P1")
                page.get_by_role("button", name="Search records", exact=True).click()
                expect(page.locator("#records tr")).to_have_count(1)
                expect(page.locator("#records")).to_contain_text("Example Builders P1")
                expect(page.locator("#records")).to_contain_text("Y")
                page.locator("#clearFilters").click()
                from app import activity
                from playwright.sync_api import expect
                activity.emit("ERROR", "Fixture connection error", error="Demonstration of a failed page")
                expect(page.locator("#consoleFeed")).to_contain_text("Fixture connection error", timeout=10000)
                page.locator('[data-log-level="ERROR"]').click()
                assert page.locator("#consoleFeed .log-entry.INFO").count() == 0
                page.locator("#logSearch").fill("no-such-log")
                expect(page.locator("#consoleFeed")).to_contain_text("Nothing here matches")
                page.locator("#logSearch").fill("")
                page.locator('[data-log-level="all"]').click()
                page.locator("#pauseLogs").click()
                activity.emit("INFO", "Paused event fixture")
                page.wait_for_timeout(3000)
                assert "Paused event fixture" not in page.locator("#consoleFeed").inner_text()
                page.locator("#pauseLogs").click()
                expect(page.locator("#consoleFeed")).to_contain_text("Paused event fixture", timeout=10000)
                page.locator("#snapshotButton").click()
                expect(page.locator("#consoleFeed")).to_contain_text("Diagnostic snapshot captured", timeout=10000)
                with page.expect_download() as saved:
                    page.locator("#exportLogs").click()
                import zipfile
                with zipfile.ZipFile(saved.value.path()) as archive:
                    assert "activity.json" in archive.namelist()
                if os.getenv("UI_SCREENSHOTS"):
                    from pathlib import Path
                    directory = Path(os.environ["UI_SCREENSHOTS"])
                    directory.mkdir(parents=True, exist_ok=True)
                    page.evaluate("window.scrollTo(0,0)")
                    page.screenshot(path=str(directory / "workspace-desktop.png"), full_page=True)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                if os.getenv("UI_SCREENSHOTS"):
                    page.screenshot(path=str(directory / "workspace-mobile.png"), full_page=True)
                page.route("**/api/**", lambda route: route.abort())
                expect(page.locator("#health")).to_contain_text("Reconnecting", timeout=15000)
                with page.expect_download() as saved:
                    page.locator("#exportLogs").click()
                import json
                with open(saved.value.path(), encoding="utf-8") as file:
                    assert "Backend unavailable" in json.load(file)["note"]
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

