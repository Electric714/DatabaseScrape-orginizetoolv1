from __future__ import annotations

import json
import re
from collections import defaultdict
from urllib.parse import urlsplit

from bs4 import BeautifulSoup
from lxml import etree

from .bbb_adapter import (
    BBB_BASE,
    BBB_SEARCH_ENDPOINT,
    BbbComplaintsAdapter,
    _candidate_name_from_slug,
    _complaint_summary,
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
_EXTRA_THREE_YEAR_PATTERNS = (
    re.compile(r"complaints?\s+closed\s+in\s+(?:the\s+)?last\s+3\s+years?\s*:?\s*([\d,]+)", re.I),
    re.compile(r"([\d,]+)\s+complaints?\s+closed\s+in\s+(?:the\s+)?last\s+3\s+years?", re.I),
    re.compile(r"complaints?\s+in\s+(?:the\s+)?last\s+3\s+years?\s*:?\s*([\d,]+)", re.I),
)
_EXTRA_TWELVE_MONTH_PATTERNS = (
    re.compile(r"complaints?\s+closed\s+in\s+(?:the\s+)?last\s+12\s+months?\s*:?\s*([\d,]+)", re.I),
    re.compile(r"closed\s+in\s+(?:the\s+)?last\s+12\s+months?\s*:?\s*([\d,]+)", re.I),
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


def _explicit_integer(patterns, text: str) -> int | None:
    for pattern in patterns:
        match = pattern.search(text or "")
        if not match:
            continue
        try:
            return int(match.group(1).replace(",", ""))
        except ValueError:
            continue
    return None


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _json_summary_value(soup: BeautifulSoup, markers: tuple[str, ...]) -> int | None:
    for script in soup.find_all("script"):
        raw = (script.string or script.get_text("", strip=True) or "").strip()
        if raw[:1] not in "[{":
            continue
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        for item in _walk_json(payload):
            for key, value in item.items():
                normalized = re.sub(r"[^a-z0-9]", "", str(key).casefold())
                if not all(marker in normalized for marker in markers):
                    continue
                if isinstance(value, bool):
                    continue
                try:
                    number = int(str(value).replace(",", "").strip())
                except (TypeError, ValueError):
                    continue
                if number >= 0:
                    return number
    return None


def _resilient_complaint_summary(html: str, url: str) -> dict:
    """Parse only explicit rolling-period complaint totals; malformed stays UNKNOWN."""
    summary = _complaint_summary(html, url)
    summary["parser_version"] = "bbb-complaints-v2"
    if summary.get("summary_parsed"):
        summary["summary_parse_reason"] = "explicit three-year total"
        return summary

    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    total = _explicit_integer(_EXTRA_THREE_YEAR_PATTERNS, text)
    if total is None:
        total = _json_summary_value(soup, ("complaint", "3", "year"))
    closed_12 = summary.get("closed_complaints_12m")
    if closed_12 is None:
        closed_12 = _explicit_integer(_EXTRA_TWELVE_MONTH_PATTERNS, text)
    if closed_12 is None:
        closed_12 = _json_summary_value(soup, ("complaint", "12", "month"))

    summary["total_complaints_3y"] = total
    summary["closed_complaints_12m"] = closed_12
    summary["summary_parsed"] = total is not None
    summary["summary_parse_reason"] = (
        "explicit three-year total" if total is not None else
        "no explicit three-year complaint total recognized"
    )
    return summary


class BbbSitemapComplaintsAdapter(BbbComplaintsAdapter):
    """Discover BBB profiles from BBB's published profile sitemaps, never /search.

    Sitemap XML is fetched as ordinary HTTP. Only a profile/complaints document
    discovered from those sitemaps may use Chromium's normal document navigation
    on a real local run. This is not a challenge bypass: robots policy and the
    same strict adapter URL boundary still apply. A blocked or malformed page is
    isolated to the contractors whose evidence path depended on that page.
    """

    canonical_start_url = BBB_SITEMAP_INDEX
    query_mode = True
    always_parse = True
    fail_fast_access_errors = False
    ignore_robots = False
    browser_respect_robots = True

    def __init__(self):
        super().__init__()
        self.state_contexts: dict[str, list[str]] = defaultdict(list)
        self.sitemap_contexts: dict[str, list[str]] = defaultdict(list)
        self.selected_sitemaps: set[str] = set()
        self.unsupported_states: set[str] = set()
        self.index_processed = False
        self.processed_sitemaps: set[str] = set()
        self.processed_profiles: set[str] = set()
        self.processed_complaints: set[str] = set()
        self.page_failures: dict[str, list[dict]] = defaultdict(list)

    def seed_urls(self, master_rows: list[dict]) -> list[str]:
        # Reuse bidder/alias normalization, then discard the legacy /search plan.
        super().seed_urls(master_rows)
        self.search_contexts.clear()
        self.query_urls.clear()
        self.state_contexts.clear()
        self.sitemap_contexts.clear()
        self.selected_sitemaps.clear()
        self.unsupported_states.clear()
        self.index_processed = False
        self.processed_sitemaps.clear()
        self.processed_profiles.clear()
        self.processed_complaints.clear()
        self.page_failures.clear()

        for key, contractor in self.contractors.items():
            state = _string(contractor.get("state")).upper()
            if state in BBB_STATE_SITEMAP_RANGES:
                if key not in self.state_contexts[state]:
                    self.state_contexts[state].append(key)
            else:
                self.incomplete_queries.add(key)
                if state:
                    self.unsupported_states.add(state)

        return [BBB_SITEMAP_INDEX] if self.contractors else []

    def browser_fetch_url(self, url: str) -> bool:
        return _is_profile_url(url) or _is_complaints_url(url)

    def browser_allowed_url(self, url: str) -> bool:
        parts = urlsplit(url)
        return (
            (parts.hostname or "").lower() == "www.bbb.org"
            and not parts.query
            and self.browser_fetch_url(url)
        )

    def allowed_url(self, url: str) -> bool:
        parts = urlsplit(url)
        if (parts.hostname or "").lower() != "www.bbb.org" or parts.query:
            return False
        if parts.path == urlsplit(BBB_SITEMAP_INDEX).path:
            return True
        if _SITEMAP_CHILD_PATH.match(parts.path):
            return url in self.selected_sitemaps
        return self.browser_fetch_url(url)

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

        if not index_by_number:
            raise ValueError("BBB sitemap index contained no recognized business-profile sitemap URLs")

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

            if not selected_for_state:
                self.incomplete_queries.update(keys)
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
            if (parts.hostname or "").lower() != "www.bbb.org" or parts.query:
                continue
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

    def _contexts_for_page(self, url: str) -> set[str]:
        parts = urlsplit(url)
        if parts.path == urlsplit(BBB_SITEMAP_INDEX).path:
            return set(self.contractors)
        if _SITEMAP_CHILD_PATH.match(parts.path):
            return set(self.sitemap_contexts.get(url, []))
        if _is_profile_url(url):
            profile = _profile_base(url)
            contexts = set(self.profile_contexts.get(profile, []))
            contexts.update(key for key, profiles in self.matched_profiles.items() if profile in profiles)
            return contexts
        return set()

    def record_page_error(self, url: str, error: str):
        """Optional crawler hook: retain exact fail-closed acquisition provenance."""
        message = _string(error)
        lowered = message.casefold()
        category = "blocked" if (
            "http 401" in lowered or "http 403" in lowered or "http 429" in lowered
            or "access challenge" in lowered or "captcha" in lowered
        ) else "fetch_error"
        if urlsplit(url).path == urlsplit(BBB_SITEMAP_INDEX).path:
            stage = "sitemap_index"
        elif _SITEMAP_CHILD_PATH.match(urlsplit(url).path):
            stage = "sitemap"
        elif _is_complaints_url(url):
            stage = "complaints"
        else:
            stage = "profile"
        contexts = self._contexts_for_page(url)
        if not contexts:
            contexts = set(self.contractors)
        for key in contexts:
            self.incomplete_queries.add(key)
            self.page_failures[key].append({
                "url": url,
                "stage": stage,
                "category": category,
                "reason": message[:500],
            })

    def links(self, html: str, url: str) -> list[str]:
        parts = urlsplit(url)
        if parts.path == urlsplit(BBB_SITEMAP_INDEX).path:
            links = self._index_links(html)
            self.index_processed = True
            return links
        if _SITEMAP_CHILD_PATH.match(parts.path):
            links = self._sitemap_profile_links(html, url)
            self.processed_sitemaps.add(url)
            return links
        if _is_complaints_url(url):
            summary = _resilient_complaint_summary(html, url)
            self.complaint_summaries[summary["profile_url"]] = summary
            self.processed_complaints.add(summary["profile_url"])
            return []
        if _is_profile_url(url):
            links = super().links(html, url)
            self.processed_profiles.add(_profile_base(url))
            return links
        return super().links(html, url)

    def _derive_incomplete_contractors(self) -> set[str]:
        incomplete: set[str] = set(self.incomplete_queries)
        incomplete.update(key for key, failures in self.page_failures.items() if failures)
        if not self.index_processed:
            incomplete.update(self.contractors)
            return incomplete

        for key in self.contractors:
            expected = set(self.query_urls.get(key) or [])
            if not expected or not expected.issubset(self.processed_sitemaps):
                incomplete.add(key)

        # A plausible candidate that could not be opened is UNKNOWN, not evidence
        # that the contractor has no BBB profile or complaints.
        for profile, contexts in self.profile_contexts.items():
            if profile not in self.processed_profiles:
                incomplete.update(contexts)

        # A verified exact profile must have an explicitly parsed rolling
        # three-year complaint total before a zero is allowed.
        for key, profiles in self.matched_profiles.items():
            for profile in profiles:
                profile_url = _profile_base(profile)
                summary = self.complaint_summaries.get(profile_url, {})
                if (
                    profile_url not in self.processed_complaints
                    or not summary
                    or not summary.get("summary_parsed")
                ):
                    incomplete.add(key)
        return incomplete

    def _identity_evidence(self, key: str, profile: dict) -> dict:
        name = _string(profile.get("name"))
        return {
            "name_exact": self._name_exact(name, key),
            "location_corroborated": self._location_accepts(profile, key),
            "location_evidence": self._location_evidence(profile, key),
            "rule": "exact normalized company/alias name plus corroborating master location",
        }

    def finalize_records(self, complete: bool) -> list[dict]:
        self.incomplete_queries.update(self._derive_incomplete_contractors())
        # Global crawler errors are not sprayed across every contractor. Per-page
        # tracking above determines which contractor is complete.
        records = super().finalize_records(complete=self.index_processed)
        for record in records:
            key = _string(record.get("extra", {}).get("master_id") or record.get("bidder_id"))
            if key not in self.contractors:
                # seed_urls keys may be a normalized fallback when no id exists.
                key = next((candidate for candidate, row in self.contractors.items()
                            if _string(row.get("id")) == _string(record.get("bidder_id"))), key)
            if record.get("source_url") == BBB_SEARCH_ENDPOINT:
                record["source_url"] = BBB_SITEMAP_INDEX

            extra = record.setdefault("extra", {})
            matched_raw = list(self.matched_profiles.get(key, {}).values())
            for evidence, raw in zip(extra.get("matched_profiles") or [], matched_raw):
                evidence["identity_decision"] = "verified_exact"
                evidence["identity_evidence"] = self._identity_evidence(key, raw)
                profile_url = _profile_base(raw.get("profile_url") or "")
                summary = self.complaint_summaries.get(profile_url, {})
                evidence["summary_parse_reason"] = summary.get("summary_parse_reason", "")
                evidence["parser_version"] = summary.get("parser_version", "")

            ambiguous = extra.get("ambiguous_candidates") or []
            for candidate in ambiguous:
                candidate["identity_decision"] = "manual_review"
                candidate["identity_evidence"] = self._identity_evidence(key, candidate)

            failures = self.page_failures.get(key, [])
            blocked = any(item.get("category") == "blocked" for item in failures)
            value = record.get("better_business_bureau_complaints", "")
            if blocked:
                status = "blocked"
                unknown_reason = "BBB blocked or challenged a required evidence page"
            elif not extra.get("complete_aggregate"):
                status = "partial"
                unknown_reason = "one or more required BBB sitemap/profile/complaint pages were incomplete or unparseable"
            elif value == "Y":
                status = "verified_positive"
                unknown_reason = ""
            elif value == "N":
                status = "verified_zero"
                unknown_reason = ""
            elif ambiguous:
                status = "unresolved_identity"
                unknown_reason = "similar BBB profile candidate(s) did not meet exact identity/location rules"
            else:
                status = "no_verified_profile"
                unknown_reason = "no exact verified BBB profile was established; absence is UNKNOWN, not clean"

            extra["discovery_method"] = "BBB-published business-profile sitemap index"
            extra["profile_fetch_method"] = "local Chromium document navigation"
            extra["sitemap_blocks_considered"] = extra.pop("query_count", 0)
            extra["supported_sitemap_states"] = sorted(BBB_STATE_SITEMAP_RANGES)
            extra["unsupported_selected_states"] = sorted(self.unsupported_states)
            extra["global_scan_complete"] = bool(complete)
            extra["contractor_collection_status"] = status
            extra["unknown_reason"] = unknown_reason
            extra["page_failures"] = failures
            extra["zero_rule"] = "N is allowed only after an exact verified profile is reached and explicitly reports a three-year complaint total of 0"
            extra["negative_inference_from_absence"] = False
            expected = set(self.query_urls.get(key) or [])
            extra["sitemap_acquisition"] = {
                "expected": len(expected),
                "processed": len(expected & self.processed_sitemaps),
                "complete": bool(expected and expected.issubset(self.processed_sitemaps)),
            }

            if status == "verified_positive":
                extra["narrative"] = "BBB verified an exact company/location profile and explicitly reported complaint activity."
            elif status == "verified_zero":
                extra["narrative"] = "BBB verified an exact company/location profile and explicitly reported zero complaints in the rolling three-year period."
            elif status == "unresolved_identity":
                extra["narrative"] = "BBB returned similar profile evidence, but identity/location remained unresolved; the master value stays UNKNOWN."
            elif status == "no_verified_profile":
                extra["narrative"] = "BBB sitemap acquisition completed without establishing an exact verified profile; no negative complaint conclusion is inferred."
            else:
                extra["narrative"] = "BBB acquisition was blocked, failed, partial, or unparseable for this contractor; no negative complaint conclusion is inferred."
        return records
