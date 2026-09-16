from pathlib import Path

browser = Path("tests/test_browser.py")
text = browser.read_text(encoding="utf-8")
text = text.replace(
    'expect(page.get_by_role("button", name="Set API key")).to_be_visible()',
    'expect(page.locator("[data-dol-key]")).to_be_visible()',
    1,
)
text = text.replace(
    'page.get_by_role("button", name="Set API key").click()',
    'page.locator("[data-dol-key]").click()',
    1,
)
if 'page.get_by_role("button", name="Set API key")' in text:
    raise SystemExit("ambiguous API-key button locator remains in browser smoke test")
browser.write_text(text, encoding="utf-8")

regression = Path("tests/test_live_source_regressions.py")
text = regression.read_text(encoding="utf-8")
text = text.replace(
    'def test_bbb_stays_fail_closed_until_supported_acquisition_is_validated():',
    'def test_legacy_bbb_search_stays_fail_closed_while_sitemap_acquisition_is_current():',
    1,
)
old = '''    catalog = next(source for source in SOURCE_CATALOG if source["key"] == "bbb")
    assert catalog["status"] == "Blocked before parsing"
    assert "access challenge" in catalog["note"]
    assert "robots" in catalog["note"].lower()
'''
new = '''    catalog = next(source for source in SOURCE_CATALOG if source["key"] == "bbb")
    assert catalog["status"] == "Sitemap discovery verified; local profile access needs validation"
    assert "sitemap" in catalog["note"].lower()
    assert "/search is never used" in catalog["note"]
    assert "challenge" in catalog["note"].lower()
'''
if old not in text:
    raise SystemExit("stale BBB regression block not found")
text = text.replace(old, new, 1)
regression.write_text(text, encoding="utf-8")
