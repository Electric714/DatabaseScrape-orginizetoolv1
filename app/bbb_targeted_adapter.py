from __future__ import annotations

import hashlib
import json
import re
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .bbb_adapter import _is_complaints_url, _is_profile_url, _profile_base, _profile_details, _string
from .bbb_sitemap_adapter import BBB_SITEMAP_INDEX, BbbSitemapComplaintsAdapter
from .bidder_schema import normalize_match_text

PROFILE_CACHE_VERSION = 1
_PROFILE_STATE_PATH = re.compile(r"^/us/(?P<state>[a-z]{2})/", re.I)


def _identity_fingerprint(row: dict) -> str:
    aliases = row.get("_bbb_aliases") or [row.get("contractor_name")]
    payload = {
        "aliases": sorted({normalize_match_text(value) for value in aliases if normalize_match_text(value)}),
        "address": normalize_match_text(row.get("address_1")),
        "city": normalize_match_text(row.get("city")),
        "state": _string(row.get("state")).upper(),
        "zip": re.sub(r"\D", "", _string(row.get("zip")))[:5],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def _profile_state(url: str) -> str:
    match = _PROFILE_STATE_PATH.match(urlsplit(_profile_base(url)).path)
    return match.group("state").upper() if match else ""


def _safe_cached_profile(entry: dict, row: dict) -> str:
    if not isinstance(entry, dict) or entry.get("version") != PROFILE_CACHE_VERSION:
        return ""
    if entry.get("identity_fingerprint") != _identity_fingerprint(row):
        return ""
    profile = _profile_base(_string(entry.get("profile_url")))
    if not profile or not _is_profile_url(profile) or urlsplit(profile).query:
        return ""
    state = _string(row.get("state")).upper()
    if state and _profile_state(profile) and _profile_state(profile) != state:
        return ""
    return profile


def _label_value_pairs(html: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "lxml")
    pairs: dict[str, str] = {}
    for label in soup.find_all("dt"):
        value = label.find_next_sibling("dd")
        if not value:
            continue
        key = " ".join(label.stripped_strings).strip().rstrip(":").lower()
        text = " ".join(value.stripped_strings).strip()
        if key and text:
            pairs[key] = text[:500]
    for row in soup.find_all("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 2:
            continue
        key = " ".join(cells[0].stripped_strings).strip().rstrip(":").lower()
        text = " ".join(cells[1].stripped_strings).strip()
        if key and text:
            pairs.setdefault(key, text[:500])
    return pairs


def _public_profile_snapshot(html: str, url: str) -> dict:
    details = _profile_details(html, url)
    pairs = _label_value_pairs(html)

    def value(*labels: str) -> str:
        for label in labels:
            result = pairs.get(label.lower())
            if result:
                return result
        return ""

    return {
        "telephone": _string(details.get("telephone")),
        "alternate_names": details.get("alternate_names") or [],
        "bbb_rating": value("BBB Rating", "Rating"),
        "bbb_accreditation": value("BBB Accreditation", "Accreditation"),
        "accredited_since": value("Accredited Since"),
        "business_started": value("Business Started"),
        "business_started_locally": value("Business Started Locally"),
        "type_of_entity": value("Type of Entity"),
        "years_in_business": value("Years in Business"),
        "retrieved_at": details.get("retrieved_at"),
        "content_hash": details.get("content_hash"),
    }


class BbbTargetedProfileAdapter(BbbSitemapComplaintsAdapter):
    """Resolve master bidders to BBB profiles once, then re-check exact profiles directly."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._cache.setdefault("verified_profiles", {})
        self.profile_resolution_cache_hits: set[str] = set()
        self.profile_resolution_cache_misses: set[str] = set()
        self.profile_resolution_cache_invalidated: set[str] = set()
        self.profile_snapshots: dict[str, dict] = {}

    def _save_verified_profile(self, key: str, profile: str, details: dict) -> None:
        contractor = self.contractors.get(key)
        if not contractor:
            return
        self._cache.setdefault("verified_profiles", {})[key] = {
            "version": PROFILE_CACHE_VERSION,
            "identity_fingerprint": _identity_fingerprint(contractor),
            "profile_url": _profile_base(profile),
            "business_name": _string(details.get("name")),
            "city": _string(details.get("city")),
            "state": _string(details.get("state")).upper(),
            "zip": _string(details.get("zip")),
            "verified_at": details.get("retrieved_at"),
            "content_hash": details.get("content_hash"),
        }
        self._save_cache()

    def _invalidate_verified_profile(self, key: str) -> None:
        if self._cache.setdefault("verified_profiles", {}).pop(key, None) is not None:
            self.profile_resolution_cache_invalidated.add(key)
            self._save_cache()

    def _enable_sitemap_fallback(self, key: str) -> None:
        contractor = self.contractors.get(key) or {}
        state = _string(contractor.get("state")).upper()
        if state and contractor.get("_bbb_aliases") and _string(contractor.get("city") or contractor.get("zip")):
            if key not in self.state_contexts[state]:
                self.state_contexts[state].append(key)
            self.unsupported_states.add(state)
        else:
            self.incomplete_queries.add(key)
            self.unsupported_states.add(state or "UNKNOWN")

    def seed_urls(self, master_rows: list[dict]) -> list[str]:
        super().seed_urls(master_rows)
        self.profile_resolution_cache_hits.clear()
        self.profile_resolution_cache_misses.clear()
        self.profile_resolution_cache_invalidated.clear()
        self.profile_snapshots.clear()

        direct_profiles: list[str] = []
        verified = self._cache.setdefault("verified_profiles", {})
        unresolved_keys: set[str] = set()

        for key, contractor in self.contractors.items():
            entry = verified.get(key)
            profile = _safe_cached_profile(entry or {}, contractor)
            if profile:
                self.profile_resolution_cache_hits.add(key)
                self.cache_used = True
                if key not in self.profile_contexts[profile]:
                    self.profile_contexts[profile].append(key)
                direct_profiles.append(profile)
            else:
                if entry:
                    self._invalidate_verified_profile(key)
                self.profile_resolution_cache_misses.add(key)
                unresolved_keys.add(key)

        # Cached resolutions do not participate in broad sitemap discovery. The
        # old sitemap adapter remains the fallback for unresolved master bidders.
        for state in list(self.state_contexts):
            self.state_contexts[state] = [key for key in self.state_contexts[state] if key in unresolved_keys]
            if not self.state_contexts[state]:
                self.state_contexts.pop(state, None)
                self.unsupported_states.discard(state)

        if unresolved_keys:
            direct_profiles.append(BBB_SITEMAP_INDEX)
        return list(dict.fromkeys(direct_profiles))

    def links(self, html: str, url: str) -> list[str]:
        if _is_profile_url(url) and not _is_complaints_url(url):
            profile = _profile_base(url)
            contexts = list(self.profile_contexts.get(profile, []))
            links = super().links(html, url)
            details = _profile_details(html, url)
            self.profile_snapshots[profile] = _public_profile_snapshot(html, url)
            fallback = False

            for key in contexts:
                if profile in self.matched_profiles.get(key, {}):
                    self._save_verified_profile(key, profile, details)
                elif key in self.profile_resolution_cache_hits:
                    # A cached mapping is only a routing shortcut. The live profile
                    # must still verify name + location every run.
                    self._invalidate_verified_profile(key)
                    self.profile_resolution_cache_hits.discard(key)
                    self.profile_resolution_cache_misses.add(key)
                    self._enable_sitemap_fallback(key)
                    fallback = True

            if fallback:
                links.append(BBB_SITEMAP_INDEX)
            return list(dict.fromkeys(links))
        return super().links(html, url)

    def _key_for_record(self, record: dict) -> str:
        bidder_id = _string(record.get("bidder_id"))
        company = normalize_match_text(record.get("company"))
        for key, contractor in self.contractors.items():
            if bidder_id and bidder_id == _string(contractor.get("id")):
                return key
            if company and company == normalize_match_text(contractor.get("contractor_name")):
                return key
        return ""

    def finalize_records(self, complete: bool) -> list[dict]:
        records = super().finalize_records(complete=complete)
        for record in records:
            key = self._key_for_record(record)
            extra = record.setdefault("extra", {})
            profiles = extra.get("matched_profiles") or []
            ambiguous = extra.get("ambiguous_candidates") or []

            for evidence in profiles:
                snapshot = self.profile_snapshots.get(_profile_base(evidence.get("profile_url") or ""), {})
                evidence.update({name: value for name, value in snapshot.items() if value not in (None, "", [])})
                evidence["profile_snapshot_master_authority"] = False

            if profiles and not ambiguous and extra.get("complete_aggregate"):
                lookup_status = "matched"
            elif ambiguous:
                lookup_status = "ambiguous"
            elif extra.get("complete_aggregate"):
                lookup_status = "no_match"
            else:
                lookup_status = "incomplete"

            candidate_count = sum(1 for contexts in self.search_evidence.values() if key and key in contexts)
            extra.update({
                "discovery_method": "verified BBB profile cache first; BBB-published business-profile sitemap fallback",
                "profile_resolution_cache_hit": bool(key and key in self.profile_resolution_cache_hits),
                "profile_resolution_cache_miss": bool(key and key in self.profile_resolution_cache_misses),
                "profile_resolution_cache_invalidated": bool(key and key in self.profile_resolution_cache_invalidated),
                "bbb_lookup_status": lookup_status,
                "candidate_profiles_considered": candidate_count,
                "profile_snapshot_fields_are_evidence_only": True,
            })
            extra["narrative"] = _string(extra.get("narrative")).replace(
                "after BBB's published business-profile sitemap discovery",
                "after cached-profile verification and BBB sitemap discovery",
            )
        return records
