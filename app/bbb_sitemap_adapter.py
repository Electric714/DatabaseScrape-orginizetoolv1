from __future__ import annotations

import re
from collections import defaultdict
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

BBB_SITEMAP_INDEX = f"{BBB_BASE}/sitemap-business-profiles-index.xml"
BBB_SITEMAP_BOUNDARY_MARGIN = 1

# One-time live validation on 2026-09-15 sampled the first 64 KiB of all 575
# BBB-published business-profile sitemaps. The files are geographically clustered.
# These are the dominant sitemap ranges for the six states represented by the
# current POC bidder database. We include one neighboring sitemap on each side at
# runtime to cover chapter/state boundaries. If BBB changes the index shape, the
# adapter fails closed and absence never becomes a negative complaint result.
BBB_STATE_SITEMAP_RANGES: dict[str, tuple[tuple[int, int], ...]] = {
    "FL": ((185, 185), (188, 191), (269, 290), (321, 324), (358, 367)),
    "IL": ((291, 310), (357, 357)),
    "MN": ((332, 344),),
    "MO": ((346, 346), (356, 356), (368, 376)),
    "OH": ((136, 156), (192, 195)),
    "WI": ((325, 331),),
}

_SITEMAP_CHILD_PATH = re.compile(r"^/sitemap-business-profiles-(?P<number>\d+)\.xml$", re.I)
_PROFILE_LOCATION_PATH = re.compile(
    r"^/us/(?P<state>[a-z]{2})/(?P<city>[^/]+)/profile/[^/]+/[^/?#]+/?$",
    re.I,
)


def _xml_locs(text: str) -> list[str]:
    try:
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, recover=False)
        root = etree.fromstring(text.encode("utf-8", errors="replace"), parser)
    except (etree.XMLSyntaxError, ValueError) as exc:
        raise ValueError("BBB sitemap XML could not be parsed") from exc

    values: list[str] = []
    for element in root.iter():
        try:
            local_name = etree.QName(element).localname
        except ValueError:
            continue
        if local_name != "loc" or not element.text:
            continue
        value = element.text.strip()
        if value:
            values.append(value)
    return values


def _expanded_sitemap_numbers(state: str) -> set[int]:
    ranges = BBB_STATE_SITEMAP_RANGES.get(state.upper(), ())
    values: set[int] = set()
    for start, end in ranges:
        low = max(1, start - BBB_SITEMAP_BOUNDARY_MARGIN)
        high = end + BBB_SITEMAP_BOUNDARY_MARGIN
        values.update(range(low, high + 1))
    return values


def _core_sitemap_numbers(state: str) -> set[int]:
    values: set[int] = set()
    for start, end in BBB_STATE_SITEMAP_RANGES.get(state.upper(), ()):
        values.update(range(start, end + 1))
    return values


class BbbSitemapComplaintsAdapter(BbbComplaintsAdapter):
    """Discover BBB profiles from BBB's published profile sitemaps, never /search.

    The profile and complaint parsers remain the conservative implementation from
    BbbComplaintsAdapter. This class changes only acquisition/discovery: it starts
    from BBB's robots-advertised business-profile sitemap index, narrows to the
    POC states represented in the imported bidder CSV, filters profile URLs by
    plausible contractor-name slug plus state, then verifies identity and location
    on the actual BBB profile before following its /complaints page.
    """

    canonical_start_url = BBB_SITEMAP_INDEX
    query_mode = True
    always_parse = True
    fail_fast_access_errors = True
    ignore_robots = False

    def __init__(self):
        super().__init__()
        self.state_contexts: dict[str, list[str]] = defaultdict(list)
        self.sitemap_contexts: dict[str, list[str]] = defaultdict(list)
        self.selected_sitemaps: set[str] = set()
        self.unsupported_states: set[str] = set()

    def seed_urls(self, master_rows: list[dict]) -> list[str]:
        # Reuse the mature bidder/alias normalization setup, then discard the
        # legacy /search query plan. No /search URL is returned or requested.
        super().seed_urls(master_rows)
        self.search_contexts.clear()
        self.query_urls.clear()
        self.state_contexts.clear()
        self.sitemap_contexts.clear()
        self.selected_sitemaps.clear()
        self.unsupported_states.clear()

        for key, contractor in self.contractors.items():
            state = _string(contractor.get("state")).upper()
            if state in BBB_STATE_SITEMAP_RANGES:
                if key not in self.state_contexts[state]:
                    self.state_contexts[state].append(key)
            else:
                # Unsupported/unmapped states remain unknown rather than clean.
                self.incomplete_queries.add(key)
                if state:
                    self.unsupported_states.add(state)

        return [BBB_SITEMAP_INDEX] if self.contractors else []

    def allowed_url(self, url: str) -> bool:
        parts = urlsplit(url)
        if (parts.hostname or "").lower() != "www.bbb.org" or parts.query:
            return False
        if parts.path == urlsplit(BBB_SITEMAP_INDEX).path:
            return True
        if _SITEMAP_CHILD_PATH.match(parts.path):
            return url in self.selected_sitemaps
        return _is_profile_url(url) or _is_complaints_url(url)

    def _index_links(self, text: str) -> list[str]:
        index_by_number: dict[int, str] = {}
        for value in _xml_locs(text):
            parts = urlsplit(value)
            if (parts.hostname or "").lower() != "www.bbb.org" or parts.query:
                continue
            match = _SITEMAP_CHILD_PATH.match(parts.path)
            if not match:
                continue
            index_by_number[int(match.group("number"))] = value

        links: list[str] = []
        for state, keys in self.state_contexts.items():
            core = _core_sitemap_numbers(state)
            if not core or not core.issubset(index_by_number):
                self.incomplete_queries.update(keys)

            selected_for_state: list[str] = []
            for number in sorted(_expanded_sitemap_numbers(state)):
                child = index_by_number.get(number)
                if not child:
                    continue
                selected_for_state.append(child)
                self.selected_sitemaps.add(child)
                for key in keys:
                    if key not in self.sitemap_contexts[child]:
                        self.sitemap_contexts[child].append(key)

            for key in keys:
                self.query_urls[key] = list(dict.fromkeys(selected_for_state))
            links.extend(selected_for_state)

        return list(dict.fromkeys(links))

    def _sitemap_profile_links(self, text: str, sitemap_url: str) -> list[str]:
        contexts = self.sitemap_contexts.get(sitemap_url, [])
        if not contexts:
            return []

        by_state: dict[str, list[str]] = defaultdict(list)
        for key in contexts:
            state = _string(self.contractors[key].get("state")).upper()
            if state:
                by_state[state].append(key)

        links: list[str] = []
        for value in _xml_locs(text):
            parts = urlsplit(value)
            match = _PROFILE_LOCATION_PATH.match(parts.path)
            if not match:
                continue
            state = match.group("state").upper()
            relevant = by_state.get(state, [])
            if not relevant:
                continue

            profile = _profile_base(value)
            candidate = {
                "profile_url": profile,
                "name": _candidate_name_from_slug(profile),
                "address": "",
                "city": match.group("city").replace("-", " "),
                "state": state,
                "zip": "",
                "card_text": "BBB published business-profile sitemap",
            }
            if self._remember_candidate(candidate, relevant):
                links.append(profile)

        return list(dict.fromkeys(links))

    def links(self, html: str, url: str) -> list[str]:
        parts = urlsplit(url)
        if parts.path == urlsplit(BBB_SITEMAP_INDEX).path:
            return self._index_links(html)
        if _SITEMAP_CHILD_PATH.match(parts.path):
            return self._sitemap_profile_links(html, url)
        return super().links(html, url)

    def finalize_records(self, complete: bool) -> list[dict]:
        records = super().finalize_records(complete=complete)
        for record in records:
            if record.get("source_url") == BBB_SEARCH_ENDPOINT:
                record["source_url"] = BBB_SITEMAP_INDEX
            extra = record.setdefault("extra", {})
            extra["discovery_method"] = "BBB-published business-profile sitemap index"
            extra["sitemap_blocks_considered"] = extra.pop("query_count", 0)
            extra["supported_sitemap_states"] = sorted(BBB_STATE_SITEMAP_RANGES)
            extra["unsupported_selected_states"] = sorted(self.unsupported_states)
            narrative = _string(extra.get("narrative"))
            narrative = narrative.replace(
                "after the targeted company/location search",
                "after BBB's published business-profile sitemap discovery",
            )
            extra["narrative"] = narrative
        return records
