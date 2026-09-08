from __future__ import annotations

import json
import re
from collections import defaultdict
from difflib import SequenceMatcher
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from .bidder_schema import normalize_match_text
from .osha_adapter import company_core, contractor_aliases, clean_search_term

BBB_BASE = "https://www.bbb.org"
BBB_SEARCH_ENDPOINT = f"{BBB_BASE}/search"
BBB_MASTER_FIELDS = ("better_business_bureau_complaints",)
BBB_MAX_SEARCH_PAGES = 3

_PROFILE_PATH = re.compile(r"^/us/[a-z]{2}/[^/]+/profile/[^/]+/[^/?#]+", re.I)
_COMPLAINT_PATH = re.compile(r"/profile/[^/]+/[^/?#]+/complaints/?$", re.I)
_ADDRESS_RE = re.compile(
    r"(?P<street>\d{1,8}\s+[^,]{2,120}?)\s*,?\s+"
    r"(?P<city>[A-Za-z .'-]{2,80}),\s*(?P<state>[A-Z]{2})\s+"
    r"(?P<zip>\d{5}(?:-\d{4})?)\b"
)
_CITY_STATE_ZIP_RE = re.compile(
    r"(?P<city>[A-Za-z .'-]{2,80}),\s*(?P<state>[A-Z]{2})\s+"
    r"(?P<zip>\d{5}(?:-\d{4})?)\b"
)
_THREE_YEAR_PATTERNS = (
    re.compile(r"\b([\d,]+)\s+total\s+complaints?\s+in\s+the\s+last\s+3\s+years?\b", re.I),
    re.compile(r"\b([\d,]+)\s+complaints?\s+in\s+the\s+last\s+3\s+years?\b", re.I),
)
_TWELVE_MONTH_PATTERNS = (
    re.compile(r"\b([\d,]+)\s+complaints?\s+closed\s+in\s+the\s+last\s+12\s+months?\b", re.I),
    re.compile(r"\b([\d,]+)\s+closed\s+complaints?\s+in\s+the\s+last\s+12\s+months?\b", re.I),
)
_COMPLAINT_DATE = re.compile(r"\bDate\s*:\s*([^|•]+?)(?=\s+(?:Type|Status|Resolved|Unresolved|Answered|Unanswered|Unpursuable)\s*:|$)", re.I)
_COMPLAINT_TYPE = re.compile(r"\bType\s*:\s*([^|•]+?)(?=\s+(?:Date|Status|Resolved|Unresolved|Answered|Unanswered|Unpursuable)\s*:|$)", re.I)
_COMPLAINT_STATUS = re.compile(r"\bStatus\s*:\s*(Resolved|Unresolved|Answered|Unanswered|Unpursuable)\b", re.I)
_NO_RESULTS = re.compile(
    r"\b(?:no\s+(?:matching\s+)?business(?:es)?|no\s+results?|0\s+results?|"
    r"could(?:n['’]t|\s+not)\s+find\s+(?:any\s+)?(?:business(?:es)?|results?))\b",
    re.I,
)


def _string(value) -> str:
    return "" if value is None else str(value).strip()


def _location_query(row: dict) -> str:
    city = _string(row.get("city"))
    state = _string(row.get("state")).upper()
    zip_code = _string(row.get("zip"))
    if city and state:
        return f"{city}, {state}" + (f" {zip_code}" if zip_code else "")
    if zip_code:
        return zip_code
    if state:
        return state
    return ""


def build_bbb_search_query(name: str, row: dict, *, page: int = 1) -> str:
    params = {
        "find_country": "USA",
        "find_text": clean_search_term(name),
        "page": str(max(1, page)),
    }
    location = _location_query(row)
    if location:
        params["find_loc"] = location
    return f"{BBB_SEARCH_ENDPOINT}?{urlencode(params)}"


def _search_term(url: str) -> str:
    return (parse_qs(urlsplit(url).query).get("find_text") or [""])[0].strip()


def _search_location(url: str) -> str:
    return (parse_qs(urlsplit(url).query).get("find_loc") or [""])[0].strip()


def _search_page(url: str) -> int:
    raw = (parse_qs(urlsplit(url).query).get("page") or ["1"])[0]
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


def _with_page(url: str, page: int) -> str:
    parts = urlsplit(url)
    query = parse_qs(parts.query, keep_blank_values=True)
    query["page"] = [str(max(1, page))]
    pairs = [(key, value) for key, values in query.items() for value in values]
    return parts._replace(query=urlencode(pairs)).geturl()


def _profile_base(url: str) -> str:
    parts = urlsplit(urljoin(BBB_BASE, url))
    path = re.sub(r"/addressId/\d+/?$", "", parts.path, flags=re.I)
    path = re.sub(r"/(?:complaints|customer-reviews)/?$", "", path, flags=re.I)
    return urlunsplit(("https", "www.bbb.org", path.rstrip("/"), "", ""))


def _complaints_url(url: str) -> str:
    return _profile_base(url) + "/complaints"


def _is_profile_url(url: str) -> bool:
    parts = urlsplit(urljoin(BBB_BASE, url))
    return (parts.hostname or "").lower() == "www.bbb.org" and bool(_PROFILE_PATH.match(parts.path))


def _is_complaints_url(url: str) -> bool:
    parts = urlsplit(urljoin(BBB_BASE, url))
    return (parts.hostname or "").lower() == "www.bbb.org" and bool(_COMPLAINT_PATH.search(parts.path))


def _script_json(soup: BeautifulSoup):
    for script in soup.find_all("script"):
        kind = (script.get("type") or "").lower()
        raw = (script.string or script.get_text("", strip=True) or "").strip()
        if not raw:
            continue
        if "ld+json" in kind:
            try:
                yield json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        # BBB has changed frontend frameworks over time. Parse plain JSON script
        # payloads only when the script itself is valid JSON; never execute code.
        elif raw[:1] in "[{":
            try:
                yield json.loads(raw)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue


def _walk_json(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _address_from_text(text: str) -> dict:
    match = _ADDRESS_RE.search(text or "")
    if match:
        return {
            "address": _string(match.group("street")),
            "city": _string(match.group("city")),
            "state": _string(match.group("state")).upper(),
            "zip": _string(match.group("zip")),
        }
    match = _CITY_STATE_ZIP_RE.search(text or "")
    if match:
        return {
            "address": "",
            "city": _string(match.group("city")),
            "state": _string(match.group("state")).upper(),
            "zip": _string(match.group("zip")),
        }
    return {"address": "", "city": "", "state": "", "zip": ""}


def _candidate_container_text(anchor) -> str:
    best = anchor.get_text(" ", strip=True)
    node = anchor
    for _ in range(6):
        node = getattr(node, "parent", None)
        if node is None:
            break
        text = node.get_text(" ", strip=True)
        if 20 <= len(text) <= 1400:
            best = text
            if _CITY_STATE_ZIP_RE.search(text):
                break
    return best


def _candidate_name_from_slug(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").split("/")[-1]
    slug = re.sub(r"-\d{4,}(?:-\d+)?$", "", slug)
    return " ".join(part for part in slug.split("-") if part)


def _search_candidates(html: str, page_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    candidates: dict[str, dict] = {}

    for anchor in soup.find_all("a", href=True):
        href = urljoin(page_url, anchor["href"])
        if not _is_profile_url(href):
            continue
        profile = _profile_base(href)
        text = _candidate_container_text(anchor)
        location = _address_from_text(text)
        name = anchor.get_text(" ", strip=True) or _candidate_name_from_slug(profile)
        current = candidates.setdefault(profile, {
            "profile_url": profile,
            "name": name,
            **location,
            "card_text": text[:1400],
        })
        if len(name) > len(current.get("name") or ""):
            current["name"] = name
        for key, value in location.items():
            if value and not current.get(key):
                current[key] = value

    # Fallback for BBB frontend revisions that keep search data in embedded JSON.
    for payload in _script_json(soup):
        for item in _walk_json(payload):
            raw_url = _string(
                item.get("profileUrl") or item.get("profileURL") or item.get("url")
            )
            if not raw_url or not _is_profile_url(raw_url):
                continue
            profile = _profile_base(raw_url)
            address_value = item.get("address")
            if isinstance(address_value, dict):
                street = _string(address_value.get("streetAddress") or address_value.get("address"))
                city = _string(address_value.get("addressLocality") or address_value.get("city"))
                state = _string(address_value.get("addressRegion") or address_value.get("state")).upper()
                zip_code = _string(address_value.get("postalCode") or address_value.get("postalcode"))
            else:
                street = _string(item.get("address"))
                city = _string(item.get("city"))
                state = _string(item.get("state")).upper()
                zip_code = _string(item.get("postalcode") or item.get("postalCode"))
            candidate = {
                "profile_url": profile,
                "name": _string(item.get("businessName") or item.get("name")) or _candidate_name_from_slug(profile),
                "address": street,
                "city": city,
                "state": state,
                "zip": zip_code,
                "card_text": "",
            }
            existing = candidates.get(profile)
            if not existing:
                candidates[profile] = candidate
            else:
                for key, value in candidate.items():
                    if value and not existing.get(key):
                        existing[key] = value
    return list(candidates.values())


def _jsonld_profile(soup: BeautifulSoup) -> dict:
    best: dict = {}
    for payload in _script_json(soup):
        for item in _walk_json(payload):
            raw_type = item.get("@type")
            types = raw_type if isinstance(raw_type, list) else [raw_type]
            if not any(str(value or "").lower() in {
                "organization", "localbusiness", "professionalservice", "homeandconstructionbusiness"
            } for value in types):
                continue
            name = _string(item.get("name"))
            address = item.get("address") if isinstance(item.get("address"), dict) else {}
            candidate = {
                "name": name,
                "address": _string(address.get("streetAddress")),
                "city": _string(address.get("addressLocality")),
                "state": _string(address.get("addressRegion")).upper(),
                "zip": _string(address.get("postalCode")),
                "telephone": _string(item.get("telephone")),
            }
            if candidate["name"] and sum(bool(candidate[k]) for k in ("address", "city", "state", "zip")) >= sum(
                bool(best.get(k)) for k in ("address", "city", "state", "zip")
            ):
                best = candidate
    return best


def _profile_details(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    details = _jsonld_profile(soup)
    text = soup.get_text(" ", strip=True)

    if not details.get("name"):
        for heading in soup.find_all(["h1", "h2", "h3"]):
            name = heading.get_text(" ", strip=True)
            if name and name.lower() not in {"business profile", "overview"} and "bbb rating" not in name.lower():
                details["name"] = name
                break

    if not all(details.get(key) for key in ("city", "state", "zip")):
        parsed = _address_from_text(text[:5000])
        for key, value in parsed.items():
            if value and not details.get(key):
                details[key] = value

    alternate_names: list[str] = []
    match = re.search(
        r"Alternate Names?\s*:\s*(.+?)(?=\s+(?:Business Management|Additional Contact Information|Principal Contacts|Customer Contacts|Business Categories|$))",
        text,
        re.I,
    )
    if match:
        alternate_names = [
            part.strip(" ,;") for part in re.split(r"[;|\n]+", match.group(1)) if part.strip(" ,;")
        ][:12]

    details.update({
        "profile_url": _profile_base(url),
        "alternate_names": alternate_names,
    })
    return details


def _integer_match(patterns, text: str) -> int | None:
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            try:
                return int(match.group(1).replace(",", ""))
            except ValueError:
                pass
    return None


def _complaint_metadata(soup: BeautifulSoup) -> list[dict]:
    rows: list[dict] = []
    seen = set()
    for heading in soup.find_all(["h2", "h3", "h4", "strong"]):
        if "initial complaint" not in heading.get_text(" ", strip=True).lower():
            continue
        node = heading
        block = ""
        for _ in range(5):
            node = getattr(node, "parent", None)
            if node is None:
                break
            text = node.get_text(" ", strip=True)
            if 30 <= len(text) <= 5000:
                block = text
                if re.search(r"\bDate\s*:", text, re.I) and re.search(r"\bStatus\s*:", text, re.I):
                    break
        if not block:
            continue
        date = (_COMPLAINT_DATE.search(block).group(1).strip() if _COMPLAINT_DATE.search(block) else "")
        type_value = (_COMPLAINT_TYPE.search(block).group(1).strip() if _COMPLAINT_TYPE.search(block) else "")
        status = (_COMPLAINT_STATUS.search(block).group(1).strip().title() if _COMPLAINT_STATUS.search(block) else "")
        key = (date, type_value, status)
        if key == ("", "", "") or key in seen:
            continue
        seen.add(key)
        rows.append({"date": date, "type": type_value, "status": status})
    return rows[:50]


def _complaint_summary(html: str, url: str) -> dict:
    soup = BeautifulSoup(html, "lxml")
    text = soup.get_text(" ", strip=True)
    total = _integer_match(_THREE_YEAR_PATTERNS, text)
    closed_12 = _integer_match(_TWELVE_MONTH_PATTERNS, text)
    return {
        "profile_url": _profile_base(url),
        "complaints_url": _complaints_url(url),
        "total_complaints_3y": total,
        "closed_complaints_12m": closed_12,
        "summary_parsed": total is not None,
        # Do not retain consumer complaint narratives. We only need metadata for
        # evidentiary context behind the bidder's Y/N complaint flag.
        "recent_complaint_metadata": _complaint_metadata(soup),
    }


class BbbComplaintsAdapter:
    """Targeted BBB business-profile/complaint parser for bidder companies only."""

    query_mode = True
    always_parse = True
    fail_fast_access_errors = True
    canonical_start_url = BBB_SEARCH_ENDPOINT
    master_fields = BBB_MASTER_FIELDS

    # BBB currently disallows generic query-string crawling in robots.txt while
    # business profile URLs are allowed. This adapter performs only explicit,
    # operator-directed company lookups from the imported master list, at low
    # concurrency, and never attempts to bypass access challenges.
    ignore_robots = True

    def __init__(self):
        self.contractors: dict[str, dict] = {}
        self.search_contexts: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.profile_contexts: dict[str, list[str]] = defaultdict(list)
        self.search_evidence: dict[str, dict[str, dict]] = defaultdict(dict)
        self.matched_profiles: dict[str, dict[str, dict]] = defaultdict(dict)
        self.ambiguous_profiles: dict[str, dict[str, dict]] = defaultdict(dict)
        self.complaint_summaries: dict[str, dict] = {}
        self.query_urls: dict[str, list[str]] = defaultdict(list)

    def seed_urls(self, master_rows: list[dict]) -> list[str]:
        self.contractors.clear()
        self.search_contexts.clear()
        self.profile_contexts.clear()
        self.search_evidence.clear()
        self.matched_profiles.clear()
        self.ambiguous_profiles.clear()
        self.complaint_summaries.clear()
        self.query_urls.clear()

        urls: list[str] = []
        for row in master_rows:
            contractor_name = _string(row.get("contractor_name"))
            if not contractor_name:
                continue
            key = _string(row.get("_master_id") or row.get("id") or normalize_match_text(contractor_name))
            context = dict(row)
            aliases = contractor_aliases(row)
            context["_bbb_aliases"] = aliases
            context["_bbb_match_cores"] = sorted({company_core(alias) for alias in aliases if company_core(alias)})
            self.contractors[key] = context

            seen = set()
            for alias in aliases:
                term = clean_search_term(alias)
                if not term:
                    continue
                query = build_bbb_search_query(term, row)
                sig = (normalize_match_text(_search_term(query)), normalize_match_text(_search_location(query)))
                if sig in seen:
                    continue
                seen.add(sig)
                if key not in self.search_contexts[sig]:
                    self.search_contexts[sig].append(key)
                self.query_urls[key].append(query)
                urls.append(query)
        return list(dict.fromkeys(urls))

    def allowed_url(self, url: str) -> bool:
        parts = urlsplit(url)
        if (parts.hostname or "").lower() != "www.bbb.org":
            return False
        if parts.path.rstrip("/") == "/search":
            return True
        return bool(_PROFILE_PATH.match(parts.path))

    def _contexts_for_search(self, url: str) -> list[str]:
        sig = (normalize_match_text(_search_term(url)), normalize_match_text(_search_location(url)))
        return self.search_contexts.get(sig, [])

    def _name_exact(self, name: str, key: str) -> bool:
        candidate = company_core(name)
        return bool(candidate and candidate in set(self.contractors[key].get("_bbb_match_cores") or []))

    def _name_plausible(self, name: str, key: str) -> bool:
        candidate = company_core(name)
        if len(candidate) < 5:
            return False
        for target in self.contractors[key].get("_bbb_match_cores") or []:
            if not target:
                continue
            if candidate in target or target in candidate:
                return True
            if SequenceMatcher(None, candidate, target).ratio() >= 0.84:
                return True
        return False

    def _location_evidence(self, candidate: dict, key: str) -> dict:
        target = self.contractors[key]
        candidate_state = normalize_match_text(candidate.get("state"))
        target_state = normalize_match_text(target.get("state"))
        candidate_city = normalize_match_text(candidate.get("city"))
        target_city = normalize_match_text(target.get("city"))
        candidate_zip = re.sub(r"\D", "", _string(candidate.get("zip")))[:5]
        target_zip = re.sub(r"\D", "", _string(target.get("zip")))[:5]
        candidate_address = normalize_match_text(candidate.get("address"))
        target_address = normalize_match_text(target.get("address_1"))
        street_number = re.match(r"\d+", _string(target.get("address_1")))
        number = street_number.group(0) if street_number else ""

        return {
            "state": bool(candidate_state and target_state and candidate_state == target_state),
            "city": bool(candidate_city and target_city and candidate_city == target_city),
            "zip": bool(candidate_zip and target_zip and candidate_zip == target_zip),
            "address": bool(
                candidate_address and target_address and (
                    candidate_address == target_address
                    or candidate_address in target_address
                    or target_address in candidate_address
                )
            ),
            "street_number": bool(number and re.match(r"\d+", _string(candidate.get("address"))) and re.match(r"\d+", _string(candidate.get("address"))).group(0) == number),
        }

    def _location_accepts(self, candidate: dict, key: str) -> bool:
        target = self.contractors[key]
        has_location = any(_string(target.get(field)) for field in ("address_1", "city", "state", "zip"))
        if not has_location:
            return True
        evidence = self._location_evidence(candidate, key)
        if evidence["zip"]:
            return True
        if evidence["city"] and evidence["state"]:
            return True
        if evidence["address"] and (evidence["state"] or evidence["city"]):
            return True
        if evidence["street_number"] and evidence["city"] and evidence["state"]:
            return True
        return False

    def _remember_candidate(self, candidate: dict, contexts: list[str]) -> bool:
        useful = False
        profile = _profile_base(candidate["profile_url"])
        for key in contexts:
            exact = self._name_exact(candidate.get("name") or "", key)
            plausible = exact or self._name_plausible(candidate.get("name") or "", key)
            if not plausible:
                continue
            useful = True
            self.search_evidence[profile][key] = {
                **candidate,
                "name_exact": exact,
                "location_evidence": self._location_evidence(candidate, key),
            }
            if key not in self.profile_contexts[profile]:
                self.profile_contexts[profile].append(key)
        return useful

    def _next_search_page(self, soup: BeautifulSoup, url: str) -> str | None:
        page = _search_page(url)
        if page >= BBB_MAX_SEARCH_PAGES:
            return None
        numeric_fallback = None
        for anchor in soup.find_all("a", href=True):
            next_url = urljoin(url, anchor["href"])
            if urlsplit(next_url).path.rstrip("/") != "/search":
                continue
            label = " ".join([
                anchor.get_text(" ", strip=True),
                _string(anchor.get("aria-label")),
                " ".join(anchor.get("rel") or []),
            ]).lower()
            if "next" in label:
                return next_url
            if _search_page(next_url) == page + 1:
                numeric_fallback = numeric_fallback or next_url
        return numeric_fallback

    def links(self, html: str, url: str) -> list[str]:
        parts = urlsplit(url)
        path = parts.path.rstrip("/")
        links: list[str] = []

        if path == "/search":
            contexts = self._contexts_for_search(url)
            soup = BeautifulSoup(html, "lxml")
            candidates = _search_candidates(html, url)
            page_text = soup.get_text(" ", strip=True)

            # Fail closed when BBB returns a 200 page whose result structure is no
            # longer recognizable. Otherwise a frontend redesign could look like
            # "zero matches" and incorrectly write N for every bidder.
            if not candidates and not _NO_RESULTS.search(page_text):
                raise ValueError(
                    "BBB search page layout was not recognized; refusing to create negative complaint results"
                )

            strong_location_candidate = False
            for candidate in candidates:
                candidate_useful = self._remember_candidate(candidate, contexts)
                if not candidate_useful:
                    continue
                links.append(_profile_base(candidate["profile_url"]))
                for key in contexts:
                    if (
                        self._name_exact(candidate.get("name") or "", key)
                        and self._location_accepts(candidate, key)
                    ):
                        strong_location_candidate = True

            # If page 1 only contains plausible same-name businesses in the wrong
            # city, keep following BBB's explicit pagination rather than settling
            # for an ambiguous match. Stop early once an exact name/location
            # candidate is visible.
            if not strong_location_candidate:
                next_url = self._next_search_page(soup, url)
                if next_url:
                    sig = (normalize_match_text(_search_term(url)), normalize_match_text(_search_location(url)))
                    next_sig = (normalize_match_text(_search_term(next_url)), normalize_match_text(_search_location(next_url)))
                    if next_sig == sig:
                        self.search_contexts[next_sig] = list(dict.fromkeys([*self.search_contexts.get(next_sig, []), *contexts]))
                    links.append(next_url)
            return list(dict.fromkeys(links))

        if _is_complaints_url(url):
            summary = _complaint_summary(html, url)
            self.complaint_summaries[summary["profile_url"]] = summary
            return []

        if _is_profile_url(url):
            profile = _profile_base(url)
            details = _profile_details(html, url)
            contexts = self.profile_contexts.get(profile, [])
            accepted = False
            for key in contexts:
                search = self.search_evidence.get(profile, {}).get(key, {})
                exact_name = self._name_exact(details.get("name") or search.get("name") or "", key)
                combined = {
                    **search,
                    **{field: value for field, value in details.items() if value},
                }
                if exact_name and self._location_accepts(combined, key):
                    self.matched_profiles[key][profile] = combined
                    accepted = True
                elif self._name_plausible(details.get("name") or search.get("name") or "", key):
                    self.ambiguous_profiles[key][profile] = {
                        **combined,
                        "reason": "BBB profile name/location requires manual identity review",
                    }
            if accepted:
                links.append(_complaints_url(profile))
            return links

        return []

    def extract(self, html: str, url: str) -> list[dict]:
        return []

    def finalize_records(self, complete: bool) -> list[dict]:
        records: list[dict] = []
        for key, contractor in self.contractors.items():
            profiles = list(self.matched_profiles.get(key, {}).values())
            ambiguous = list(self.ambiguous_profiles.get(key, {}).values())
            if not profiles and not ambiguous and not complete:
                continue

            summaries = [
                self.complaint_summaries.get(_profile_base(profile.get("profile_url") or ""), {})
                for profile in profiles
            ]
            parsed = [summary for summary in summaries if summary.get("summary_parsed")]
            positive = any((summary.get("total_complaints_3y") or 0) > 0 for summary in parsed)

            if positive:
                complaint_value = "Y"
            elif profiles and complete and len(parsed) == len(profiles):
                complaint_value = "N"
            elif not profiles and not ambiguous and complete:
                # A completed exact company/location search found no matching BBB
                # profile or plausible candidate, so BBB has no published complaint
                # profile we can attribute to this bidder.
                complaint_value = "N"
            else:
                complaint_value = ""

            if positive:
                narrative = "BBB reports complaint activity for at least one exact company/location profile match."
            elif profiles and parsed and len(parsed) == len(profiles):
                narrative = "BBB exact company/location profile match(es) report zero complaints in the last three years."
            elif ambiguous:
                narrative = "BBB returned similar business profile(s) that require manual identity review."
            elif complete:
                narrative = "No exact or plausible BBB business profile match was found after the targeted company/location search."
            else:
                narrative = "BBB collection was incomplete, so no negative complaint conclusion was written."

            bidder_id = _string(contractor.get("id"))
            contractor_name = _string(contractor.get("contractor_name"))
            source_url = ""
            if profiles:
                source_url = _complaints_url(profiles[0].get("profile_url") or "")
            if not source_url:
                source_url = (self.query_urls.get(key) or [BBB_SEARCH_ENDPOINT])[-1]

            evidence_profiles = []
            for profile in profiles:
                profile_url = _profile_base(profile.get("profile_url") or "")
                summary = self.complaint_summaries.get(profile_url, {})
                evidence_profiles.append({
                    "profile_url": profile_url,
                    "business_name": _string(profile.get("name")),
                    "address": _string(profile.get("address")),
                    "city": _string(profile.get("city")),
                    "state": _string(profile.get("state")),
                    "zip": _string(profile.get("zip")),
                    "alternate_names": profile.get("alternate_names") or [],
                    "total_complaints_3y": summary.get("total_complaints_3y"),
                    "closed_complaints_12m": summary.get("closed_complaints_12m"),
                    "summary_parsed": bool(summary.get("summary_parsed")),
                    "recent_complaint_metadata": summary.get("recent_complaint_metadata") or [],
                })

            records.append({
                "external_id": f"bbb:bidder:{bidder_id or normalize_match_text(contractor_name)}",
                "company": contractor_name,
                "source_url": source_url,
                "better_business_bureau_complaints": complaint_value,
                "extra": {
                    "source_system": "Better Business Bureau public business profiles",
                    "api_fields_written_to_master": list(BBB_MASTER_FIELDS),
                    "identity_fields_used_for_matching_only": [
                        "contractor_name", "related_companies", "address_1", "city", "state", "zip"
                    ],
                    "search_terms": contractor.get("_bbb_aliases") or [],
                    "query_count": len(self.query_urls.get(key) or []),
                    "complete_aggregate": bool(complete),
                    "matched_profiles": evidence_profiles,
                    "ambiguous_candidates": ambiguous,
                    "narrative": narrative,
                    "complaint_definition": "BBB-published complaints in the profile's rolling three-year reporting period",
                    "consumer_complaint_text_retained": False,
                },
            })
        return records
