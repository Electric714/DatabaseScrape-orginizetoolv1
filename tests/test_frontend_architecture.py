from pathlib import Path


STATIC = Path("app/static")


def read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_frontend_is_one_direct_implementation():
    html = read("index.html")

    assert "ui-shell.js" not in html
    assert "research-desk.css" not in html
    # The consolidated UI intentionally has one base stylesheet plus the
    # professional presentation layer; there are no obsolete parallel shells.
    assert html.count('href="/static/styles.css"') == 1
    assert html.count('href="/static/professional.css"') == 1
    assert html.count('<link rel="stylesheet"') == 2
    assert html.count('<script src=') == 1

    expected_views = {
        "dashboard": "dashboardSection",
        "database": "recordsSection",
        "import": "importSection",
        "research": "researchSection",
        "comparison": "compareSection",
        "evidence": "evidenceSection",
        "sources": "sourceSection",
        "activity": "activitySection",
        "settings": "settingsSection",
    }
    for view, section_id in expected_views.items():
        assert f'data-view-link="{view}"' in html
        assert f'id="{section_id}"' in html
        assert f'data-view-section="{view}"' in html


def test_obsolete_override_layers_are_removed():
    assert not (STATIC / "ui-shell.js").exists()
    assert not (STATIC / "research-desk.css").exists()


def test_workflow_guardrails_are_explicit_in_ui():
    html = read("index.html")
    javascript = read("app.js")

    # Assert the current operator-facing safeguards semantically rather than
    # requiring retired pre-cleanup headings.
    assert "MASTER DATABASE" in html
    assert "Research evidence:" in html
    assert "outside-source records remain separate until reviewed" in html
    assert "research does not create new master contractors" in html
    assert "failed or partial research does not create a clean finding" in html
    assert 'data-review-mode="identity"' in html
    assert 'data-review-mode="resolved"' in html
    assert 'id="evidencePrevPage"' in html
    assert 'id="evidenceNextPage"' in html
    assert 'id="toggleAllFields"' in html

    assert "/api/bidder/proposals?status=all" in javascript
    assert "Cannot add from research" in javascript
    assert "Approve change" in javascript
    assert "Keep approved value" in javascript


def test_mobile_and_accessibility_hooks_exist():
    html = read("index.html")
    css = read("styles.css")

    assert 'class="skip-link"' in html
    assert 'aria-expanded="false"' in html
    assert 'role="tablist"' in html
    assert 'aria-live="polite"' in html
    assert ":focus-visible" in css
    assert "overflow-x: hidden" in css
    assert "@media (max-width: 900px)" in css
    assert "@media (max-width: 720px)" in css
