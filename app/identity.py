"""Conservative location corroboration shared by targeted source adapters."""
from .bidder_schema import normalize_match_text


def location_corroborates(candidate: dict, master: dict) -> bool:
    # A job site can differ from headquarters. Such records need review, not
    # automatic attribution based on a matching name alone.
    for prefix in ("", "additional_address_"):
        target = {k: master.get(prefix + k, "") for k in ("city", "state", "zip")}
        target["address"] = master.get("additional_address" if prefix else "address_1", "")
        left = {k: normalize_match_text(candidate.get(k) or "") for k in target}
        right = {k: normalize_match_text(v or "") for k, v in target.items()}
        if left["state"] and right["state"] and left["state"] != right["state"]:
            continue
        if left["zip"] and right["zip"] and left["zip"][:5] == right["zip"][:5]:
            return True
        if left["city"] and left["city"] == right["city"] and left["state"] and left["state"] == right["state"]:
            return True
        if left["address"] and left["address"] == right["address"] and left["state"] and left["state"] == right["state"]:
            return True
    return False
