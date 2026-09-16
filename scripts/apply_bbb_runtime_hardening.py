from pathlib import Path

# Temporary branch-only helper. Removed before merge.


def patch_main():
    path = Path("app/main.py")
    text = path.read_text(encoding="utf-8")
    old = '''        "max_depth": 5,\n        "concurrency": 1,\n        "delay_ms": 500,\n        "render_mode": "http",\n        "respect_robots": True,\n'''
    new = '''        "max_depth": 5,\n        "concurrency": 1,\n        "delay_ms": 2000,\n        "render_mode": "http",\n        "respect_robots": True,\n'''
    if old not in text:
        raise SystemExit("BBB source settings block not found")
    text = text.replace(old, new, 1)
    path.write_text(text, encoding="utf-8")


def patch_crawler():
    path = Path("app/crawler.py")
    text = path.read_text(encoding="utf-8")
    marker = 'CHALLENGE = re.compile(r"captcha|verify (?:that )?you are human|checking your browser|access denied|cf-chl-|challenge-platform", re.I)\n'
    helper = marker + '''\n\ndef _has_access_challenge(text: str) -> bool:\n    """Detect actual block/challenge documents without scanning arbitrary user text.\n\n    Generic phrases such as ``access denied`` and ``captcha`` can legitimately\n    occur in BBB complaint narratives. Real WAF/challenge pages identify\n    themselves near the beginning of the document, so human-readable challenge\n    phrases are intentionally limited to the first 32 KiB.\n    """\n    return bool(CHALLENGE.search((text or "")[:32768]))\n'''
    if "def _has_access_challenge(" not in text:
        if marker not in text:
            raise SystemExit("challenge regex marker not found")
        text = text.replace(marker, helper, 1)
    replaced = text.count("CHALLENGE.search(result.text)")
    if replaced < 2:
        raise SystemExit(f"expected at least 2 result challenge checks, found {replaced}")
    text = text.replace("CHALLENGE.search(result.text)", "_has_access_challenge(result.text)")
    path.write_text(text, encoding="utf-8")


patch_main()
patch_crawler()
