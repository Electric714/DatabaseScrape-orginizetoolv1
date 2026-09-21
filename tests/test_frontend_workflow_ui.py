import os
import socket
import threading
import time
from urllib.parse import parse_qs, urlparse

import pytest


pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="Set RUN_BROWSER_TESTS=1 after installing Chromium",
)


def test_research_desk_review_evidence_failure_and_focus_states(database, monkeypatch):
    import uvicorn
    from playwright.sync_api import expect, sync_playwright
    from app import main

    monkeypatch.setattr(main, "dol_api_key_configured", lambda: False)

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(main.app, log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()

    evidence = [
        {
            "id": index,
            "company": f"Acme {index:02d}",
            "source_name": "Fixture source",
            "source_url": f"https://fixture.test/evidence/{index}",
            "last_seen": "2026-09-17T16:00:00Z",
            "active": True,
            "state_federal_debarment": "Y" if index % 2 else "",
            "extra": {
                "match_status": "exact name + location",
                "collection_state": "partial" if index == 3 else "complete",
                "summary": f"Synthetic evidence {index}",
            },
        }
        for index in range(1, 27)
    ]
    proposals = [
        {
            "id": 101,
            "status": "pending",
            "proposal_type": "field_update",
            "master_contractor": "Acme 01",
            "field_name": "state_federal_debarment",
            "old_value": "N",
            "new_value": "Y",
            "source_name": "Fixture source",
            "source_url": "https://fixture.test/evidence/1",
        },
        {
            "id": 102,
            "status": "pending",
            "proposal_type": "ambiguous",
            "master_contractor": "Acme Shared Name",
            "source_name": "Fixture source",
            "source_url": "https://fixture.test/evidence/2",
        },
        {
            "id": 103,
            "status": "pending",
            "proposal_type": "new_record",
            "proposed": {"contractor_name": "Outside Master LLC", "city": "Madison", "state": "WI"},
            "source_name": "Fixture source",
            "source_url": "https://fixture.test/evidence/3",
        },
        {
            "id": 104,
            "status": "dismissed",
            "proposal_type": "field_update",
            "master_contractor": "Acme 04",
            "field_name": "osha",
            "old_value": "",
            "new_value": "Y",
            "source_name": "Fixture source",
            "source_url": "https://fixture.test/evidence/4",
        },
    ]
    sources = [
        {
            "id": 77,
            "name": "Fixture source",
            "start_url": "https://fixture.test/records",
            "auto_scan": False,
            "interval_minutes": 60,
            "max_pages": 10,
            "max_depth": 1,
            "concurrency": 1,
            "delay_ms": 0,
            "render_mode": "http",
            "respect_robots": True,
            "last_status": "failed",
            "last_scan_at": "2026-09-17T15:30:00Z",
        }
    ]
    jobs = [
        {
            "id": 11,
            "source_id": 77,
            "source_name": "Fixture source",
            "status": "failed",
            "started_at": "2026-09-17T15:30:00Z",
            "message": "Synthetic source failure",
            "pages_processed": 1,
            "pages_discovered": 2,
            "records_found": 0,
            "records_new": 0,
            "records_updated": 0,
            "errors": 1,
        },
        {
            "id": 10,
            "source_id": 77,
            "source_name": "Fixture source",
            "status": "completed",
            "started_at": "2026-09-17T14:30:00Z",
            "message": "Prior run completed",
            "pages_processed": 2,
            "pages_discovered": 2,
            "records_found": 2,
            "records_new": 2,
            "records_updated": 0,
            "errors": 0,
        },
    ]

    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.01)
        assert server.started

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": 1180, "height": 900})
                page_errors = []
                page.on("pageerror", lambda error: page_errors.append(str(error)))

                page.route("**/api/sources", lambda route: route.fulfill(json=sources))
                page.route("**/api/jobs?*", lambda route: route.fulfill(json=jobs))
                page.route("**/api/bidder/proposals?*", lambda route: route.fulfill(json=proposals))

                def evidence_route(route):
                    parsed = urlparse(route.request.url)
                    query = parse_qs(parsed.query)
                    offset = int(query.get("offset", ["0"])[0])
                    limit = int(query.get("limit", ["25"])[0])
                    q = query.get("q", [""])[0].lower()
                    source_id = query.get("source_id", [""])[0]
                    items = evidence
                    if q:
                        items = [item for item in items if q in item["company"].lower()]
                    if source_id:
                        items = items[:2] if source_id == "77" else []
                    route.fulfill(json={"items": items[offset:offset + limit], "total": len(items)})

                page.route("**/api/records?*", evidence_route)
                page.goto(f"http://127.0.0.1:{port}")

                expect(page.locator("#dashProblemCount")).to_have_text("1", timeout=10000)
                expect(page.locator("#dashboardProblems")).to_contain_text("Synthetic source failure")

                page.locator('[data-view-link="sources"]').click()
                expect(page.locator("#sources")).to_contain_text("Fixture source")
                expect(page.locator("#sources .tag.failed")).to_have_text("failed")

                page.locator('[data-view-link="comparison"]').click()
                expect(page.locator("#deskChangeCount")).to_have_text("(1)")
                expect(page.locator("#deskIdentityCount")).to_have_text("(2)")
                expect(page.locator("#deskResolvedCount")).to_have_text("(1)")
                page.locator('[data-review-mode="identity"]').click()
                expect(page.locator("#proposals")).to_contain_text("AMBIGUOUS IDENTITY")
                expect(page.locator("#proposals")).to_contain_text("OUTSIDE MASTER")
                expect(page.locator("#proposals [data-apply-proposal]")).to_have_count(0)
                expect(page.locator("#proposals")).to_contain_text("Cannot add from research")
                page.locator('[data-review-mode="resolved"]').click()
                expect(page.locator("#proposals")).to_contain_text("dismissed")

                page.locator('[data-view-link="evidence"]').click()
                expect(page.locator("#deskEvidenceList .evidence-card-item")).to_have_count(25, timeout=10000)
                expect(page.locator("#evidencePageInfo")).to_have_text("1–25 of 26 evidence records")
                page.locator("#evidenceNextPage").click()
                expect(page.locator("#deskEvidenceList .evidence-card-item")).to_have_count(1)
                expect(page.locator("#evidencePageInfo")).to_have_text("26–26 of 26 evidence records")

                page.locator("#deskEvidenceSearch").fill("Acme 03")
                page.get_by_role("button", name="Search evidence", exact=True).click()
                expect(page.locator("#deskEvidenceList .evidence-card-item")).to_have_count(1)
                expect(page.locator("#deskEvidenceList")).to_contain_text("Acme 03")
                expect(page.locator("#deskEvidenceList .evidence-state.partial")).to_have_text("partial")

                page.locator("#deskEvidenceSearch").fill("")
                page.locator("#deskEvidenceSource").select_option("77")
                expect(page.locator("#deskEvidenceList .evidence-card-item")).to_have_count(2)
                expect(page.locator("#evidencePageInfo")).to_have_text("1–2 of 2 evidence records")

                page.locator('[data-view-link="settings"]').click()
                settings_button = page.locator("#oshaApiKeyButton")
                settings_button.click()
                expect(page.locator("#dolKeyDialog")).to_be_visible()
                expect(page.locator("#dolApiKey")).to_be_focused()
                page.locator("#dolKeyDialog [data-close='dolKeyDialog']").last.click()
                expect(settings_button).to_be_focused()
                assert page.evaluate("location.hash") == "#settings"

                page.locator('[data-view-link="activity"]').click()
                expect(page.locator("#jobs")).to_contain_text("Synthetic source failure")
                expect(page.locator("#jobs .tag.failed")).to_have_text("failed")

                page.set_viewport_size({"width": 820, "height": 900})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                expect(page.locator("#deskMenuButton")).to_be_visible()
                page.locator("#deskMenuButton").click()
                expect(page.locator(".sidebar")).to_be_visible()
                expect(page.locator("#deskMenuButton")).to_have_attribute("aria-expanded", "true")
                page.locator(".desk-nav-backdrop").click(position={"x": 800, "y": 100})
                expect(page.locator("#deskMenuButton")).to_have_attribute("aria-expanded", "false")

                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
                assert not page_errors
            finally:
                browser.close()
    finally:
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        assert not thread.is_alive()
