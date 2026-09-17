from pathlib import Path

path = Path(__file__).with_name("apply_reliability_hardening.py")
text = path.read_text(encoding="utf-8")
old = "from . import bidder_master as bidder_master_db"
new = "from . import bidder_master as bidder_db"
if old not in text:
    raise SystemExit("expected stale bidder import alias not found")
path.write_text(text.replace(old, new), encoding="utf-8")
print("patched reliability runner import alias")
