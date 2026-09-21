from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from lxml import etree

from .bbb_adapter import (
    BBB_BASE,
    BBB_SEARCH_ENDPOINT,
    BbbComplaintsAdapter,
    _candidate_name_from_slug,
    _is_complaints_url,
    _is_profile_url,
    _profile_base,
    _string,
)
from .config import RUNTIME_DIR

BBB_SITEMAP_INDEX = f"{BBB_BASE}/sitemap-business-profiles-index.xml"
BBB_SITEMAP_REFRESH_BATCH = 100

_SITEMAP_CHILD_PATH = re.compile(r"^/sitemap-business-profiles-(?P<number>\d+)\.xml$", re.I)
_PROFILE_LOCATION_PATH = re.compile(
    r"^/us/(?P<state>[a-z]{2})/(?P<city>[^/]+)/profile/[^/]+/[^/?#]+/?$", re.I,
)


def _xml_entries(text: str) -> list[tuple[str, str]]:
    """Return loc/lastmod pairs without allowing DTDs or network entities."""
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, recover=False)
        root = etree.fromstring(text.encode("utf-8", errors="replace"), parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ValueError("BBB sitemap XML could not be parsed") from exc
    values: list[tuple[str, str]] = []
    for parent in root.iter():
        children = {}
        for child in parent:
            try:
                children[etree.QName(child).localname] = (child.text or "").strip()
            except ValueError:
                continue
        if children.get("loc"):
            values.append((children["loc"], children.get("lastmod", "")))
    return values


def _xml_locs(text: str) -> list[str]:
    return [url for url, _ in _xml_entries(text)]


def _expanded_sitemap_numbers(state: str) -> set[int]:
    """Compatibility shim: sitemap membership is now discovered, never guessed."""
    return set()


class BbbSitemapComplaintsAdapter(BbbComplaintsAdapter):
    """Two-stage BBB acquisition: guarded sitemap HTTP, then one browser session."""

    canonical_start_url = BBB_SITEMAP_INDEX
    query_mode = True
    always_parse = True
    fail_fast_access_errors = True
    ignore_robots = False
    browser_respect_robots = True
    visible_browser = True

    def __init__(self, *, cache_path: str | Path | None = None, refresh_batch_size: int = BBB_SITEMAP_REFRESH_BATCH):
        super().__init__()
        configured = os.environ.get("BBB_SITEMAP_CACHE_PATH")
        self.cache_path = Path(cache_path or configured or (RUNTIME_DIR / "bbb_sitemap_state_index.json"))
        self.refresh_batch_size = max(1, int(refresh_batch_size))
        self.state_contexts: dict[str, list[str]] = defaultdict(list)
        self.sitemap_contexts: dict[str, list[str]] = defaultdict(list)
        self.selected_sitemaps: set[str] = set()
        self.unsupported_states: set[str] = set()
        self.index_refresh_incomplete = False
        self.acquisition_state = "sitemap_http"
        self.blocked_stage = ""
        self.blocked_status: int | None = None
        self.challenge_classification = "none"
        self.manual_action_required = False
        self.circuit_open = False
        self.cache_used = False
        self.telemetry: list[dict] = []
        self._cache = self._load_cache()
        self._refresh_urls: set[str] = set()

    def _load_cache(self) -> dict:
        try:
            value = json.loads(self.cache_path.read_text(encoding="utf-8"))
            if isinstance(value, dict) and value.get("version") == 1:
                return value
        except (OSError, ValueError, TypeError):
            pass
        return {"version": 1, "fingerprint": "", "children": [], "cursor": 0, "states": {}, "members": {}}

    def _save_cache(self) -> None:
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(json.dumps(self._cache, sort_keys=True), encoding="utf-8")
        os.replace(temporary, self.cache_path)

    def seed_urls(self, master_rows: list[dict]) -> list[str]:
        # Inherit the alias normalization only. Search URLs are deliberately discarded.
        super().seed_urls(master_rows)
        self.search_contexts.clear()
        self.query_urls.clear()
        self.state_contexts.clear()
        self.sitemap_contexts.clear()
        self.selected_sitemaps.clear()
        self.unsupported_states.clear()
        for key, contractor in self.contractors.items():
            state = _string(contractor.get("state")).upper()
            if state and contractor.get("_bbb_aliases") and _string(contractor.get("city") or contractor.get("zip")):
                self.state_contexts[state].append(key)
                self.unsupported_states.add(state)  # unknown until cache/index proves coverage
            else:
                self.incomplete_queries.add(key)
                self.unsupported_states.add(state or "UNKNOWN")
            if state and not self._cache.get("fingerprint"):
                self.unsupported_states.add(state)
        return [BBB_SITEMAP_INDEX] if self.contractors else []

    def browser_fetch_url(self, url: str) -> bool:
        return not self.circuit_open and (_is_profile_url(url) or _is_complaints_url(url))

    def browser_allowed_url(self, url: str) -> bool:
        parts = urlsplit(url)
        return (parts.hostname or "").lower() == "www.bbb.org" and not parts.query and (
            _is_profile_url(url) or _is_complaints_url(url)
        )

    def allowed_url(self, url: str) -> bool:
        parts = urlsplit(url)
        if (parts.hostname or "").lower() != "www.bbb.org" or parts.query:
            return False
        if parts.path == urlsplit(BBB_SITEMAP_INDEX).path:
            return True
        if _SITEMAP_CHILD_PATH.match(parts.path):
            return url in self.selected_sitemaps
        return not self.circuit_open and self.browser_allowed_url(url)

    def acquisition_stage_for(self, url: str) -> str:
        if _is_complaints_url(url):
            return "browser_complaints"
        if _is_profile_url(url):
            return "browser_probe" if not self.matched_profiles else "browser_profile"
        return "sitemap_http"

    def note_acquisition(self, url: str, *, status: int | None, challenge: str = "none", blocked: bool = False) -> None:
        stage = self.acquisition_stage_for(url)
        self.acquisition_state = "blocked" if blocked else stage
        if blocked:
            self.circuit_open = True
            self.blocked_stage, self.blocked_status = stage, status
            self.challenge_classification = challenge
            self.manual_action_required = challenge in {"challenge_html", "cloudflare_or_interstitial"}
            self.incomplete_queries.update(self.contractors)
        self.telemetry.append({
            "mode": "browser" if stage.startswith("browser_") else "http",
            "stage": stage,
            "profile_identifier": urlsplit(_profile_base(url)).path.rsplit("/", 1)[-1] if stage.startswith("browser_") else "",
            "upstream_status": status,
            "challenge_classification": challenge,
            "circuit_breaker": "opened" if blocked else "closed",
            "cache_used": self.cache_used,
            "browser_engine": "Chromium",
            "browser_version": getattr(self, "browser_engine_version", "unknown"),
        })

    def _index_links(self, text: str) -> list[str]:
        children: list[dict[str, str]] = []
        for value, lastmod in _xml_entries(text):
            parts = urlsplit(value)
            if (parts.hostname or "").lower() == "www.bbb.org" and not parts.query and _SITEMAP_CHILD_PATH.match(parts.path):
                children.append({"url": value, "lastmod": lastmod})
        fingerprint = hashlib.sha256(json.dumps(children, sort_keys=True).encode()).hexdigest()
        if fingerprint != self._cache.get("fingerprint"):
            self._cache.update({"fingerprint": fingerprint, "children": children, "cursor": 0, "states": {}, "members": {}})

        cursor = min(int(self._cache.get("cursor", 0)), len(children))
        end = min(cursor + self.refresh_batch_size, len(children))
        refresh = [item["url"] for item in children[cursor:end]]
        self._refresh_urls = set(refresh)
        # The durable cursor advances only after each child has parsed. A crash
        # therefore repeats (rather than silently skips) the unfinished member.
        self.index_refresh_incomplete = end < len(children)

        links = list(refresh)
        states = self._cache.get("states", {})
        for state, keys in self.state_contexts.items():
            cached_members = states.get(state, [])
            if cached_members:
                self.cache_used = True
                self.unsupported_states.discard(state)
            elif not self.index_refresh_incomplete:
                self.unsupported_states.add(state)
            if self.index_refresh_incomplete:
                self.incomplete_queries.update(keys)
            for child in cached_members:
                if child not in {item["url"] for item in children}:
                    continue
                links.append(child)
                self.selected_sitemaps.add(child)
                self.sitemap_contexts[child].extend(k for k in keys if k not in self.sitemap_contexts[child])
        for child in refresh:
            self.selected_sitemaps.add(child)
            # Refresh files are parsed for all requested states to rebuild the map.
            self.sitemap_contexts[child] = list(dict.fromkeys(k for keys in self.state_contexts.values() for k in keys))
        self._save_cache()
        return list(dict.fromkeys(links))

    def _sitemap_profile_links(self, text: str, sitemap_url: str) -> list[str]:
        observed_states: set[str] = set()
        links: list[str] = []
        for value in _xml_locs(text):
            parts = urlsplit(value)
            match = _PROFILE_LOCATION_PATH.match(parts.path) if (parts.hostname or "").lower() == "www.bbb.org" else None
            if not match or parts.query:
                continue
            state = match.group("state").upper()
            observed_states.add(state)
            contexts = self.state_contexts.get(state, [])
            if not contexts:
                continue
            profile = _profile_base(value)
            candidate = {"profile_url": profile, "name": _candidate_name_from_slug(profile), "address": "",
                         "city": match.group("city").replace("-", " "), "state": state, "zip": "",
                         "card_text": "BBB published business-profile sitemap"}
            if self._remember_candidate(candidate, contexts):
                links.append(profile)
        if sitemap_url in self._refresh_urls:
            members = self._cache.setdefault("members", {})
            old_states = set(members.get(sitemap_url, []))
            for state in old_states - observed_states:
                self._cache.setdefault("states", {}).setdefault(state, [])[:] = [u for u in self._cache["states"][state] if u != sitemap_url]
            members[sitemap_url] = sorted(observed_states)
            for state in observed_states:
                self.unsupported_states.discard(state)
                bucket = self._cache.setdefault("states", {}).setdefault(state, [])
                if sitemap_url not in bucket:
                    bucket.append(sitemap_url)
            child_urls = [item["url"] for item in self._cache.get("children", [])]
            cursor = int(self._cache.get("cursor", 0))
            while cursor < len(child_urls) and child_urls[cursor] in members:
                cursor += 1
            self._cache["cursor"] = cursor
            self.index_refresh_incomplete = cursor < len(child_urls)
            self._save_cache()
        return list(dict.fromkeys(links))

    def links(self, html: str, url: str) -> list[str]:
        parts = urlsplit(url)
        if parts.path == urlsplit(BBB_SITEMAP_INDEX).path:
            return self._index_links(html)
        if _SITEMAP_CHILD_PATH.match(parts.path):
            return self._sitemap_profile_links(html, url)
        return super().links(html, url)

    def finalize_records(self, complete: bool) -> list[dict]:
        complete = complete and not self.index_refresh_incomplete and not self.circuit_open
        records = super().finalize_records(complete=complete)
        for record in records:
            if record.get("source_url") == BBB_SEARCH_ENDPOINT:
                record["source_url"] = BBB_SITEMAP_INDEX
            extra = record.setdefault("extra", {})
            extra.update({
                "discovery_method": "BBB-published business-profile sitemap index",
                "profile_fetch_method": "persistent visible local Chromium document navigation",
                "sitemap_refresh_incomplete": self.index_refresh_incomplete,
                "unsupported_selected_states": sorted(self.unsupported_states),
                "acquisition_state": self.acquisition_state,
                "blocked_stage": self.blocked_stage,
                "blocked_status": self.blocked_status,
                "challenge_classification": self.challenge_classification,
                "manual_action_required": self.manual_action_required,
                "cache_used": self.cache_used,
                "acquisition_telemetry": self.telemetry,
            })
            extra["sitemap_blocks_considered"] = extra.pop("query_count", 0)
            extra["narrative"] = _string(extra.get("narrative")).replace(
                "after the targeted company/location search", "after BBB's published business-profile sitemap discovery"
            )
        return records
