import hashlib
import json
import re
from datetime import datetime
from typing import Any

PHONE_DIGITS = re.compile(r"\D+")
WHITESPACE = re.compile(r"\s+")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return WHITESPACE.sub(" ", str(value)).strip()


def normalize_phone(value: str) -> str:
    digits = PHONE_DIGITS.sub("", value or "")
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return clean_text(value)


def normalize_date(value: str) -> str:
    raw = clean_text(value)
    if not raw:
        return ""
    formats = (
        "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y",
        "%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y",
    )
    for fmt in formats:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            pass
    return raw


def canonical_record(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": clean_text(record.get("name")),
        "company": clean_text(record.get("company")),
        "phone": normalize_phone(clean_text(record.get("phone"))),
        "address": clean_text(record.get("address")),
        "date": normalize_date(clean_text(record.get("date"))),
        "external_id": clean_text(record.get("external_id")),
        "source_url": clean_text(record.get("source_url")),
        "extra": record.get("extra") or {},
    }


def entity_key(record: dict[str, Any]) -> str:
    r = canonical_record(record)
    if r["external_id"]:
        identity = f"id|{r['external_id']}"
    elif r["name"] or r["company"]:
        identity = json.dumps(["named", r["source_url"], r["name"].casefold(), r["company"].casefold()])
    elif r["address"]:
        identity = json.dumps(["address", r["source_url"], r["address"].casefold()])
    else:
        identity = f"url|{r['source_url']}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def record_hash(record: dict[str, Any]) -> str:
    payload = canonical_record(record)
    payload.pop("source_url", None)  # Provenance changes are not field changes.
    payload["extra"] = {k: v for k, v in payload["extra"].items() if k not in {"raw_text", "extraction", "jsonld_type"}}
    stable = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()
