"""One-shot branch patcher for the WI/IL state-source milestone.

This script is intentionally temporary. It lets CI patch large existing files,
run the suite against the result, and commit only if the patch applies cleanly.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(path: str, old: str, new: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit(f"Expected exactly one patch target in {path!r}, found {text.count(old)}")
    target.write_text(text.replace(old, new), encoding="utf-8")


def append_once(path: str, marker: str, addition: str) -> None:
    target = ROOT / path
    text = target.read_text(encoding="utf-8")
    if marker in text:
        return
    target.write_text(text.rstrip() + "\n\n" + addition.strip() + "\n", encoding="utf-8")


# Dependency for extracting text from WisDOT's official PDF.
requirements = ROOT / "requirements.txt"
req = requirements.read_text(encoding="utf-8")
if "pypdf==" not in req:
    requirements.write_text(req.rstrip() + "\npypdf==6.18.0\n", encoding="utf-8")

# Register exact state-source hosts.
replace_once(
    "app/adapters.py",
    "from .state_adapter import MinnesotaDebarmentAdapter\nfrom .violation_tracker_adapter import ViolationTrackerAdapter\n",
    "from .state_adapter import MinnesotaDebarmentAdapter\nfrom .state_sources import IllinoisDebarmentAdapter, WisconsinDebarmentAdapter\nfrom .violation_tracker_adapter import ViolationTrackerAdapter\n",
)
replace_once(
    "app/adapters.py",
    '    "mn.gov": MinnesotaDebarmentAdapter,\n    "www.bbb.org": BbbComplaintsAdapter,\n',
    '    "mn.gov": MinnesotaDebarmentAdapter,\n    "labor.illinois.gov": IllinoisDebarmentAdapter,\n    "wisconsindot.gov": WisconsinDebarmentAdapter,\n    "www.wisconsindot.gov": WisconsinDebarmentAdapter,\n    "www.bbb.org": BbbComplaintsAdapter,\n',
)

# Make the sources appear automatically in the local workspace.
replace_once(
    "app/main.py",
    "from .state_adapter import MN_URL\nfrom .violation_tracker_adapter import VT_URL\n",
    "from .state_adapter import MN_URL\nfrom .state_sources import IL_URL, WI_URL\nfrom .violation_tracker_adapter import VT_URL\n",
)
replace_once(
    "app/main.py",
    '    for name, url in [("Violation Tracker", VT_URL), ("Minnesota OSP debarment", MN_URL)]:\n',
    '    for name, url in [\n'
    '        ("Violation Tracker", VT_URL),\n'
    '        ("Minnesota OSP debarment", MN_URL),\n'
    '        ("Illinois public works debarment", IL_URL),\n'
    '        ("Wisconsin DOT debarment", WI_URL),\n'
    '    ]:\n',
)

# Explicit field ownership remains the only path from research evidence to proposals.
replace_once(
    "app/source_catalog.py",
    '    {"key": "state", "name": "State debarment — Minnesota OSP", "method": "Official finite HTML list", "status": "Live list retrieval verified",\n'
    '     "fields": ["state_federal_debarment"], "hosts": ["mn.gov"],\n'
    '     "note": "Checks active dates. Minnesota procurement coverage only; not Minnesota DLI or all-state clearance."},\n',
    '    {"key": "state_mn", "name": "State debarment — Minnesota OSP", "method": "Official finite HTML list", "status": "Live list retrieval verified",\n'
    '     "fields": ["state_federal_debarment"], "hosts": ["mn.gov"],\n'
    '     "note": "Checks active dates. Minnesota procurement coverage only; not Minnesota DLI or all-state clearance."},\n'
    '    {"key": "state_il", "name": "State debarment — Illinois IDOL public works", "method": "Official finite HTML list", "status": "Collector implemented",\n'
    '     "fields": ["state_federal_debarment", "prevailing_wage_violations"], "hosts": ["labor.illinois.gov"],\n'
    '     "note": "Positive-only exact listed-name evidence. The official page ties debarment to Prevailing Wage Act violations; absence never clears either field."},\n'
    '    {"key": "state_wi", "name": "State debarment — Wisconsin DOT", "method": "Official PDF", "status": "Collector implemented",\n'
    '     "fields": ["state_federal_debarment"], "hosts": ["wisconsindot.gov", "www.wisconsindot.gov"],\n'
    '     "note": "Positive-only targeted PDF matching with location and active-date corroboration. WisDOT list is not a complete federal/all-agency clearance."},\n',
)

# Narrow binary-source support: only adapters that explicitly opt in can fetch an asset.
replace_once(
    "app/crawler.py",
    '        if result.status >= 400:\n            raise ValueError(f"HTTP {result.status}")\n        if CHALLENGE.search(result.text):\n            raise ValueError("Explicit access challenge detected; no rendering attempted")\n        digest = hashlib.sha256(result.text.encode()).hexdigest()\n',
    '        if result.status >= 400:\n            raise ValueError(f"HTTP {result.status}")\n        decoder = getattr(self.adapter, "decode_body", None)\n        if decoder:\n            decoded = decoder(result.body, result.headers, result.url)\n            if not isinstance(decoded, str):\n                raise ValueError("Source adapter body decoder must return text")\n            result.text = decoded\n        if CHALLENGE.search(result.text):\n            raise ValueError("Explicit access challenge detected; no rendering attempted")\n        digest_source = result.body if decoder else result.text.encode()\n        digest = hashlib.sha256(digest_source).hexdigest()\n',
)
replace_once(
    "app/crawler.py",
    '        if result.status == 200 and not CHALLENGE.search(result.text) and (\n            self.render_mode == "browser" or self.render_mode == "auto" and self._looks_dynamic(result.text)\n        ):\n',
    '        if getattr(self.adapter, "decode_body", None):\n            return result\n        if result.status == 200 and not CHALLENGE.search(result.text) and (\n            self.render_mode == "browser" or self.render_mode == "auto" and self._looks_dynamic(result.text)\n        ):\n',
)
replace_once(
    "app/crawler.py",
    '        parts = urlsplit(url)\n        return parts.hostname == self.start_host and parts.port in {None, 80, 443} and not any(parts.path.lower().endswith(ext) for ext in ASSET_EXTENSIONS) and self.adapter.allowed_url(url)\n',
    '        parts = urlsplit(url)\n        path = parts.path.lower()\n        blocked_asset = any(path.endswith(ext) for ext in ASSET_EXTENSIONS)\n        allowed_assets = tuple(str(ext).lower() for ext in getattr(self.adapter, "allowed_asset_extensions", ()))\n        if blocked_asset and not any(path.endswith(ext) for ext in allowed_assets):\n            return False\n        return parts.hostname == self.start_host and parts.port in {None, 80, 443} and self.adapter.allowed_url(url)\n',
)
replace_once(
    "app/crawler.py",
    '            if result.status < 300 and result.status != 204:\n                kind = result.headers.get("content-type", "").lower()\n                if kind and not any(x in kind for x in ("text/", "html", "xml", "json", "javascript")):\n                    raise ValueError("Unsupported response content type")\n',
    '            if result.status < 300 and result.status != 204:\n                kind = result.headers.get("content-type", "").lower()\n                accepted = tuple(str(value).lower() for value in getattr(self.adapter, "accepted_content_types", ()))\n                normal = any(x in kind for x in ("text/", "html", "xml", "json", "javascript"))\n                adapter_ok = any(value in kind for value in accepted)\n                if kind and not normal and not adapter_ok:\n                    raise ValueError("Unsupported response content type")\n',
)

# The Wisconsin adapter is the only current binary source and must opt in explicitly.
replace_once(
    "app/state_sources.py",
    '    accepted_content_types = ("application/pdf",)\n',
    '    accepted_content_types = ("application/pdf",)\n    allowed_asset_extensions = (".pdf",)\n',
)

# Keep the review honest about what is implemented vs live-validated.
replace_once(
    "docs/POC-REVIEW.md",
    '| WI | [WisDOT list](https://wisconsindot.gov/hccidocs/debar.pdf), [DOA procurement](https://doa.wi.gov/Pages/StateEmployees/Procurement.aspx), [contract-compliance ineligible PDF](https://doa.wi.gov/Documents/DEO/WOCCELIIneligible.pdf) | Official resources identified. Separate scopes; PDF acquisition/parsing not implemented |\n',
    '| WI | [WisDOT list](https://wisconsindot.gov/hccidocs/debar.pdf), [DOA procurement](https://doa.wi.gov/Pages/StateEmployees/Procurement.aspx), [contract-compliance ineligible PDF](https://doa.wi.gov/Documents/DEO/WOCCELIIneligible.pdf) | WisDOT targeted PDF collector implemented with exact-name, location and active-date corroboration; live app retrieval still needs a permitted end-to-end run. Other Wisconsin lists remain separate/unimplemented |\n',
)
replace_once(
    "docs/POC-REVIEW.md",
    '| IL | [Labor public-works debarred contractors](https://labor.illinois.gov/laws-rules/conmed/debarred-contractors.html) | Public page identified; not implemented |\n',
    '| IL | [Labor public-works debarred contractors](https://labor.illinois.gov/laws-rules/conmed/debarred-contractors.html) | Finite HTML collector implemented; exact active listed names can propose debarment and prevailing-wage flags, with manual approval still required |\n',
)
append_once(
    "docs/POC-REVIEW.md",
    "## State-source implementation update",
    """## State-source implementation update\n\nIllinois IDOL and Wisconsin WisDOT collectors were added after the initial review. Illinois is parsed as a finite official HTML list and only exact active listed names can produce positive evidence. Because the current Illinois page does not publish location alongside the listed name, the identity basis is explicitly recorded and the finding still requires manual proposal approval. The Illinois source owns both `state_federal_debarment` and `prevailing_wage_violations` because the page explicitly describes the debarment as a Prevailing Wage Act consequence.\n\nWisconsin WisDOT is an official PDF. The crawler now has a narrow binary-content opt-in: ordinary asset URLs remain blocked, while an adapter may explicitly allow a content type and extension. The WisDOT adapter extracts PDF text and searches only selected master aliases; it requires an exact name plus corroborating city/state or ZIP and an active restriction date before proposing `state_federal_debarment=Y`. Missing, expired, malformed, or location-mismatched entries remain unknown/no-change. This does not make WisDOT a substitute for Wisconsin DOA, DWD, federal SAM, or other jurisdiction-specific lists.\n""",
)

# Small README status correction.
replace_once(
    "README.md",
    "PACER is excluded, OSHA/DOL share one collector, Minnesota OSP is implemented, and Violation Tracker has a provisional HTML adapter.",
    "PACER is excluded, OSHA/DOL share one collector, Minnesota OSP is implemented, Illinois IDOL and Wisconsin WisDOT collectors are implemented, and Violation Tracker has a provisional HTML adapter.",
)

print("State-source patch applied successfully")
