from pathlib import Path


js = Path("app/static/app.js")
body = js.read_text(encoding="utf-8")
replacements = {
    "Checking current SAM.gov Alpha API key status…": "Checking current SAM.gov API key status…",
    "A SAM.gov Alpha/test API key is already saved locally. Enter a new key only if you want to replace it; it will be tested before replacement.": "A SAM.gov Public API key is already saved locally. Enter a new key only if you want to replace it; it will be tested before replacement.",
    "No SAM.gov Alpha/test API key is saved yet. Paste the test key below; the app will validate it against the official v4 Alpha Exclusions API.": "No SAM.gov Public API key is saved yet. Paste the key below; the app will validate it against the official production APIs.",
    "Testing key with the SAM.gov Alpha Exclusions API…": "Testing key with the SAM.gov production API…",
    "SAM.gov Alpha API key could not be validated.": "SAM.gov API key could not be validated.",
    "SAM.gov Alpha API key tested and saved locally. Federal debarment collection is ready.": "SAM.gov API key tested and saved locally. Federal debarment collection is ready.",
    "Built-in REST source · Alpha/test v4": "Built-in REST source · Production v4",
    "Set test API key": "Set API key",
    "Recollect SAM Alpha API records": "Recollect SAM production API records",
}
for old, new in replacements.items():
    body = body.replace(old, new)

old_success = """    notify('SAM.gov API key tested and saved locally. Federal debarment collection is ready.');"""
new_success = """    notify(result.warning || 'SAM.gov API key tested and saved locally. Federal debarment collection is ready.');"""
body = body.replace(old_success, new_success, 1)

if "SAM.gov Alpha" in body or "Alpha/test" in body:
    raise SystemExit("stale SAM Alpha/test wording remains in app.js")
if "api-alpha.sam.gov/entity-information/v4/exclusions" in body:
    raise SystemExit("stale SAM Alpha endpoint remains in app.js")
js.write_text(body, encoding="utf-8")

html = Path("app/static/index.html")
markup = html.read_text(encoding="utf-8")
markup = markup.replace(
    'SAM.gov Alpha/test API key<input id="samApiKey"',
    'SAM.gov Public API key<input id="samApiKey"',
)
markup = markup.replace(
    'placeholder="Paste SAM.gov test API key"',
    'placeholder="Paste SAM.gov Public API key"',
)
if "SAM.gov Alpha" in markup or "Alpha/test" in markup:
    raise SystemExit("stale SAM Alpha/test wording remains in index.html")
html.write_text(markup, encoding="utf-8")
