import os
import httpx
import pytest
from app.crawler import CrawlEngine
from app.bidder_schema import BIDDER_COLUMNS


def test_live_gui_smoke(database, monkeypatch):
    import csv
    import io
    import json
    import socket
    import threading
    import time
    import uvicorn
    import zipfile
    from playwright.sync_api import expect, sync_playwright
    from app import activity, main

    def targeted_site(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", headers={"content-type": "text/plain"})
        if request.url.host == "mn.gov" and request.url.path == "/admin/osp/government/suspended-debarred/":
            return httpx.Response(
                200,
                text='''<div class="search-results">Results 1 - 1 of 1
                <div class="results"><div class="result-link"><a id="fixture">Example Builders P1</a></div>
                <table><tr><td>12 Oak Rd</td></tr><tr><td>Madison, WI</td></tr>
                <tr><td>Debarment Date:</td><td>09/01/2026</td></tr>
                <tr><td>Debarment End Date:</td><td>09/30/2026</td></tr></table></div></div>''',
                headers={"content-type": "text/html"},
            )
        return httpx.Response(404, text="not found")

    class FixtureEngine(CrawlEngine):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs, transport=httpx.MockTransport(targeted_site))

    monkeypatch.setattr(main, "CrawlEngine", FixtureEngine)
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

                expect(page.locator("#deskPageTitle")).to_have_text("Dashboard")
                expect(page.locator('[data-view-link="dashboard"]')).to_have_class("nav-link active")
                expect(page.locator("#dashboardSection")).to_be_visible()
                expect(page.locator("#recordsSection")).to_be_hidden()

                page.locator('[data-view-link="settings"]').click()
                expect(page.locator("#deskPageTitle")).to_have_text("Settings")
                expect(page.locator("#oshaApiKeyButton")).to_have_text("Set OSHA API key")
                page.locator("#oshaApiKeyButton").click()
                expect(page.locator("#dolKeyDialog")).to_be_visible()
                expect(page.get_by_role("button", name="Test & save API key")).to_be_visible()
                page.locator("#dolKeyDialog [data-close='dolKeyDialog']").last.click()

                page.locator('[data-view-link="sources"]').click()
                expect(page.get_by_role("heading", name="OSHA / DOL Enforcement API")).to_be_visible()
                expect(page.get_by_role("button", name="Collect OSHA", exact=True)).to_be_disabled()
                expect(page.get_by_role("heading", name="SAM.gov Federal Debarment / Exclusions")).to_be_visible()
                expect(page.get_by_role("button", name="Collect federal debarment", exact=True)).to_be_enabled()
                expect(page.get_by_role("heading", name="BBB Business Profiles / Complaints")).to_be_visible()
                expect(page.get_by_role("button", name="Collect BBB complaints", exact=True)).to_be_enabled()
                expect(page.get_by_role("heading", name="Minnesota OSP debarment")).to_be_visible()

                page.locator('[data-view-link="import"]').click()
                expect(page.locator("#uploadCsvButton")).to_have_text("Upload master CSV")

                baseline_one = {column: "" for column in BIDDER_COLUMNS}
                baseline_one.update({
                    "id": "P1",
                    "contractor_name": "Example Builders P1",
                    "address_1": "12 Oak Rd",
                    "city": "Madison",
                    "state": "WI",
                    "state_federal_debarment": "N",
                })
                baseline_two = {column: "" for column in BIDDER_COLUMNS}
                baseline_two.update({
                    "id": "P2",
                    "contractor_name": "Example Builders P2",
                    "address_1": "12 Oak Rd",
                    "city": "Madison",
                    "state": "WI",
                })
                stream = io.StringIO()
                writer = csv.DictWriter(stream, fieldnames=BIDDER_COLUMNS)
                writer.writeheader()
                writer.writerows([baseline_one, baseline_two])
                page.locator("#csvUpload").set_input_files({
                    "name": "baseline.csv",
                    "mimeType": "text/csv",
                    "buffer": stream.getvalue().encode(),
                })
                expect(page.locator("#masterCount")).to_have_text("2", timeout=10000)
                expect(page.locator("#uploadCsvButton")).to_have_text("Import another CSV", timeout=10000)
                expect(page.locator("#researchContractors option")).to_have_count(2, timeout=10000)
                assert page.locator("#researchContractors option:checked").count() == 2

                page.locator('[data-view-link="database"]').click()
                expect(page.locator("#records")).to_contain_text("Example Builders P1", timeout=10000)
                compact = page.evaluate("""() => {
                    const wrap = document.querySelector('.bidder-table-wrap');
                    const table = document.querySelector('.bidder-table');
                    return {
                        layout: getComputedStyle(table).tableLayout,
                        scrollWidth: wrap.scrollWidth,
                        clientWidth: wrap.clientWidth,
                        firstWidth: table.querySelector('th:nth-child(1)').getBoundingClientRect().width,
                        secondWidth: table.querySelector('th:nth-child(2)').getBoundingClientRect().width,
                        relatedDisplay: getComputedStyle(table.querySelector('th:nth-child(3)')).display,
                        cityDisplay: getComputedStyle(table.querySelector('th:nth-child(5)')).display
                    };
                }""")
                assert compact["layout"] == "auto"
                assert compact["scrollWidth"] <= compact["clientWidth"] * 1.25
                assert compact["firstWidth"] >= 90
                assert compact["secondWidth"] >= 300
                assert compact["relatedDisplay"] == "none"
                assert compact["cityDisplay"] == "table-cell"

                page.locator("#toggleAllFields").click()
                expect(page.locator("#toggleAllFields")).to_have_text("Compact view")
                full = page.evaluate("""() => {
                    const wrap = document.querySelector('.bidder-table-wrap');
                    const table = document.querySelector('.bidder-table');
                    return {
                        scrollWidth: wrap.scrollWidth,
                        clientWidth: wrap.clientWidth,
                        firstWidth: table.querySelector('th:nth-child(1)').getBoundingClientRect().width,
                        secondWidth: table.querySelector('th:nth-child(2)').getBoundingClientRect().width,
                        relatedDisplay: getComputedStyle(table.querySelector('th:nth-child(3)')).display
                    };
                }""")
                assert full["scrollWidth"] > full["clientWidth"] * 2
                assert full["firstWidth"] >= 80
                assert full["secondWidth"] >= 240
                assert full["relatedDisplay"] == "table-cell"

                page.locator('[data-view-link="sources"]').click()
                mn_row = page.locator(".source-row").filter(has_text="Minnesota OSP debarment")
                mn_row.get_by_role("button", name="Collect records", exact=True).click()
                expect(page.locator("#sourceRecordCount")).to_have_text("2", timeout=15000)

                page.locator('[data-view-link="comparison"]').click()
                page.locator("#compareButton").click()
                expect(page.locator("#proposals")).to_contain_text("state_federal_debarment", timeout=10000)
                expect(page.locator("#proposals")).to_contain_text("N")
                expect(page.locator("#proposals")).to_contain_text("Y")
                expect(page.locator("#deskChangeCount")).to_have_text("(1)")
                page.locator("[data-apply-proposal]").first.click()
                expect(page.locator("#pendingUpdateCount")).to_have_text("0", timeout=10000)

                page.locator('[data-view-link="evidence"]').click()
                expect(page.locator("#deskEvidenceList")).to_contain_text("Example Builders P1", timeout=10000)
                evidence_buttons = page.locator("#deskEvidenceList [data-record]")
                expect(evidence_buttons).to_have_count(2)
                expect(evidence_buttons.first).to_be_visible()

                page.locator('[data-view-link="database"]').click()
                page.locator("#fieldFilter").select_option("contractor")
                page.locator("#search").fill("Example Builders P1")
                page.get_by_role("button", name="Search records", exact=True).click()
                expect(page.locator("#records tr")).to_have_count(1)
                expect(page.locator("#records")).to_contain_text("Example Builders P1")
                page.locator("#toggleAllFields").click()
                expect(page.locator("#records")).to_contain_text("Y")
                page.locator("#clearFilters").click()

                page.locator('[data-view-link="activity"]').click()
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
                with zipfile.ZipFile(saved.value.path()) as archive:
                    assert "activity.json" in archive.namelist()

                if os.getenv("UI_SCREENSHOTS"):
                    from pathlib import Path
                    directory = Path(os.environ["UI_SCREENSHOTS"])
                    directory.mkdir(parents=True, exist_ok=True)
                    page.locator('[data-view-link="dashboard"]').click()
                    page.screenshot(path=str(directory / "workspace-desktop.png"), full_page=True)
                    page.locator('[data-view-link="activity"]').click()

                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                expect(page.locator("#deskMenuButton")).to_be_visible()
                page.locator("#deskMenuButton").click()
                expect(page.locator(".sidebar")).to_be_visible()
                page.locator(".desk-nav-backdrop").click(position={"x": 370, "y": 100})

                if os.getenv("UI_SCREENSHOTS"):
                    page.locator("#deskMenuButton").click()
                    page.screenshot(path=str(directory / "workspace-mobile.png"), full_page=True)
                    page.locator(".desk-nav-backdrop").click(position={"x": 370, "y": 100})

                page.route("**/api/**", lambda route: route.abort())
                expect(page.locator("#health")).to_contain_text("Reconnecting", timeout=15000)
                with page.expect_download() as saved:
                    page.locator("#exportLogs").click()
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


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="Set RUN_BROWSER_TESTS=1 after installing Chromium",
)


async def test_actual_javascript_rendering_and_cookies(source):
    requests = []

    def site(request):
        requests.append(request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                200,
                text='<div id="root"></div><script src="/app.js"></script>',
                headers={"content-type": "text/html", "set-cookie": "session=fixture; Path=/"},
            )
        if request.url.path == "/app.js":
            return httpx.Response(
                200,
                text="fetch('/data').then(r=>r.json()).then(d=>{document.getElementById('root').innerHTML='<dl><dt>Name</dt><dd>'+d.name+'</dd><dt>Phone</dt><dd>6085551111</dd></dl>';})",
                headers={"content-type": "text/javascript"},
            )
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
        return httpx.Response(
            200,
            text='<script src="http://127.0.0.1/private.js"></script>',
            headers={"content-type": "text/html"},
        )

    engine = CrawlEngine({**source, "render_mode": "auto"}, 0)
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as client:
            with pytest.raises(ValueError, match="Rendering incomplete"):
                await engine._fetch(client, source["start_url"], None)
        assert all("127.0.0.1" not in url for url in requests)
    finally:
        await engine.renderer.close()
