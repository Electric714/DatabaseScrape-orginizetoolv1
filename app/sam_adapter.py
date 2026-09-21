"""SAM.gov daily Public V2 exclusions extract adapter (no credential required)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .bidder_schema import normalize_match_text
from .config import DATA_DIR
from .osha_adapter import company_core, contractor_aliases
from .sam_extract import SamExtractError, iter_public_v2_rows

SAM_DATA_SERVICES_ENDPOINT = "https://sam.gov/data-services/Exclusions/Public%20V2"
SAM_EXCLUSIONS_ENDPOINT = SAM_DATA_SERVICES_ENDPOINT  # compatibility/migration alias
SAM_EXCLUSIONS_PATH = "/data-services/Exclusions/Public%20V2"
SAM_PUBLIC_V2_PACKAGE = "Exclusions/Public V2/"
SAM_PUBLIC_SEARCH = "https://sam.gov/search/?index=ex"
SAM_MASTER_FIELDS = ("state_federal_debarment",)
SAM_MAX_COMPRESSED_BYTES = int(os.getenv("SAM_MAX_COMPRESSED_BYTES", str(512 * 1024 * 1024)))
SAM_MAX_UNCOMPRESSED_BYTES = int(os.getenv("SAM_MAX_UNCOMPRESSED_BYTES", str(2 * 1024 * 1024 * 1024)))
SAM_CACHE_MAX_AGE_SECONDS = int(os.getenv("SAM_CACHE_MAX_AGE_SECONDS", str(48 * 3600)))
SAM_CACHE_DIR = DATA_DIR / "sam-public-v2"
SAM_FUZZY_NAME_THRESHOLD = float(os.getenv("SAM_FUZZY_NAME_THRESHOLD", "0.94"))
SAM_MAX_AMBIGUOUS_PER_CONTRACTOR = int(os.getenv("SAM_MAX_AMBIGUOUS_PER_CONTRACTOR", "20"))

_STREET_SUFFIXES = {
    "street": "st", "st": "st", "road": "rd", "rd": "rd", "avenue": "ave", "ave": "ave",
    "boulevard": "blvd", "blvd": "blvd", "drive": "dr", "dr": "dr", "lane": "ln", "ln": "ln",
    "court": "ct", "ct": "ct", "parkway": "pkwy", "pkwy": "pkwy", "highway": "hwy", "hwy": "hwy",
    "place": "pl", "pl": "pl", "terrace": "ter", "ter": "ter", "circle": "cir", "cir": "cir",
}
_DIRECTIONALS = {
    "north": "n", "south": "s", "east": "e", "west": "w",
    "northeast": "ne", "northwest": "nw", "southeast": "se", "southwest": "sw",
}
_UNIT_TOKENS = {"suite", "ste", "unit", "apt", "apartment", "floor", "fl"}


def _string(value) -> str:
    return "" if value is None else str(value).strip()


def _parse_time(value: str) -> datetime:
    value = _string(value)
    if not value:
        raise ValueError("artifact publication timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        parsed = parsedate_to_datetime(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _zip5(value) -> str:
    digits = re.sub(r"[^0-9]", "", _string(value))
    return digits[:5] if len(digits) >= 5 else ""


def _street_tokens(value) -> list[str]:
    tokens = normalize_match_text(value).split()
    result = []
    for token in tokens:
        if token in _UNIT_TOKENS:
            break
        result.append(_STREET_SUFFIXES.get(token, _DIRECTIONALS.get(token, token)))
    return result


def _street_number(value) -> str:
    tokens = _street_tokens(value)
    if tokens and any(ch.isdigit() for ch in tokens[0]):
        return tokens[0]
    return ""


def _street_core(value) -> str:
    tokens = _street_tokens(value)
    if tokens and any(ch.isdigit() for ch in tokens[0]):
        tokens = tokens[1:]
    return " ".join(tokens)


def _street_similarity(left, right) -> float:
    left_core, right_core = _street_core(left), _street_core(right)
    if not left_core or not right_core:
        return 0.0
    if left_core == right_core:
        return 1.0
    return SequenceMatcher(None, left_core, right_core).ratio()


def _master_locations(master: dict) -> list[dict]:
    return [
        {
            "address": master.get("address_1"),
            "city": master.get("city"),
            "state": master.get("state"),
            "zip": master.get("zip"),
        },
        {
            "address": master.get("additional_address"),
            "city": master.get("additional_address_city"),
            "state": master.get("additional_address_state"),
            "zip": master.get("additional_address_zip"),
        },
    ]


def _location_evidence(candidate: dict, master: dict) -> dict:
    """Return conservative SAM identity corroboration, never a fuzzy percentage."""
    best = {"corroborates": False, "strength": 0, "basis": "no reliable location corroboration"}
    c_address = _string(candidate.get("address_1") or candidate.get("address"))
    c_city = normalize_match_text(candidate.get("city") or "")
    c_state = normalize_match_text(candidate.get("state") or "")
    c_zip = _zip5(candidate.get("zip") or "")
    c_number = _street_number(c_address)

    for target in _master_locations(master):
        t_address = _string(target.get("address"))
        t_city = normalize_match_text(target.get("city") or "")
        t_state = normalize_match_text(target.get("state") or "")
        t_zip = _zip5(target.get("zip") or "")
        t_number = _street_number(t_address)

        if c_state and t_state and c_state != t_state:
            continue
        state_same = bool(c_state and t_state and c_state == t_state)
        city_same = bool(c_city and t_city and c_city == t_city)
        zip_same = bool(c_zip and t_zip and c_zip == t_zip)
        number_same = bool(c_number and t_number and c_number == t_number)
        street_score = _street_similarity(c_address, t_address)

        # Exact/near-exact street plus state is strongest. A shared city alone is
        # intentionally never enough; this is what produced the prior Miami noise.
        if state_same and number_same and street_score >= 0.90:
            return {"corroborates": True, "strength": 4, "basis": "same street number/name and state"}
        if state_same and zip_same and city_same:
            evidence = {"corroborates": True, "strength": 3, "basis": "same city/state/ZIP"}
        elif state_same and zip_same and (not c_city or not t_city):
            evidence = {"corroborates": True, "strength": 2, "basis": "same state/ZIP"}
        elif state_same and city_same and number_same and street_score >= 0.75:
            evidence = {"corroborates": True, "strength": 2, "basis": "same city/state and compatible street"}
        else:
            evidence = {"corroborates": False, "strength": 0, "basis": "no reliable location corroboration"}
        if evidence["strength"] > best["strength"]:
            best = evidence
    return best


def _name_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _plausible_fuzzy_name(left: str, right: str) -> tuple[bool, float]:
    """Allow typo-level review candidates only; never broad industry-word matches."""
    score = _name_similarity(left, right)
    if score < SAM_FUZZY_NAME_THRESHOLD:
        return False, score
    left_tokens, right_tokens = left.split(), right.split()
    if len(left) < 8 or len(right) < 8 or not left_tokens or not right_tokens:
        return False, score
    shared = set(left_tokens) & set(right_tokens)
    # Multi-token names need real lexical overlap. For two-token names one shared
    # distinctive token plus a very high character similarity can still catch a typo.
    minimum_shared = 2 if min(len(left_tokens), len(right_tokens)) >= 3 else 1
    return len(shared) >= minimum_shared, score


def select_manifest_artifact(payload: bytes | str | dict | list) -> dict:
    """Validate a listing and deterministically select its newest Public V2 artifact."""
    if isinstance(payload, (bytes, str)):
        try:
            payload = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("SAM file listing is not valid JSON") from exc
    if isinstance(payload, dict):
        items = next((payload[k] for k in ("files", "items", "data", "fileDetails") if isinstance(payload.get(k), list)), None)
    else:
        items = payload
    if not isinstance(items, list) or not items or any(not isinstance(item, dict) for item in items):
        raise ValueError("SAM file listing has an unexpected shape")
    valid = []
    for item in items:
        package = _string(item.get("package") or item.get("folder") or item.get("path") or item.get("directory"))
        name = _string(item.get("fileName") or item.get("name") or item.get("filename"))
        identity = f"{package}/{name}".replace("//", "/")
        lowered = identity.casefold()
        if "histor" in lowered or "fascsa" in lowered or "public v2" not in lowered:
            continue
        extension = Path(name or urlsplit(_string(item.get("url") or item.get("downloadUrl"))).path).suffix.lower()
        if extension not in {".zip", ".csv"}:
            continue
        content_type = _string(item.get("contentType") or item.get("content_type") or item.get("mimeType")).lower()
        expected = ("zip", "octet-stream") if extension == ".zip" else ("csv", "text/plain", "octet-stream")
        if content_type and not any(token in content_type for token in expected):
            continue
        published_raw = item.get("publicationTimestamp") or item.get("published") or item.get("lastModified") or item.get("date")
        try:
            published = _parse_time(published_raw)
            size = int(item.get("size") or item.get("contentLength") or item.get("compressedSize") or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        if size < 0 or size > SAM_MAX_COMPRESSED_BYTES:
            continue
        url = _string(item.get("downloadUrl") or item.get("url") or item.get("href"))
        if not url:
            continue
        valid.append((published, name, {**item, "file_name": name, "download_url": url,
                                      "publication_timestamp": published.isoformat(), "compressed_size": size,
                                      "content_type": content_type, "extension": extension}))
    if not valid:
        raise ValueError("listing contains no valid Exclusions/Public V2 ZIP or CSV artifact")
    valid.sort(key=lambda entry: (entry[0], entry[1].casefold()), reverse=True)
    return valid[0][2]


class SamExclusionsAdapter:
    query_mode = True
    artifact_source = True
    always_parse = True
    canonical_start_url = SAM_DATA_SERVICES_ENDPOINT
    master_fields = SAM_MASTER_FIELDS

    def __init__(self):
        self.contractors = {}
        self.matches = defaultdict(dict)
        self.ambiguous = defaultdict(dict)
        self.artifact_metadata = {}
        self.extract_complete = False
        self.incomplete_reason = "extract not retrieved"

    def seed_urls(self, master_rows: list[dict]) -> list[str]:
        self.contractors.clear(); self.matches.clear(); self.ambiguous.clear()
        self.extract_complete = False; self.incomplete_reason = "extract not retrieved"
        for row in master_rows:
            name = _string(row.get("contractor_name"))
            if not name:
                continue
            key = _string(row.get("_master_id") or row.get("id") or normalize_match_text(name))
            context = dict(row)
            aliases = contractor_aliases(row)
            context["_sam_names"] = {company_core(alias) for alias in aliases if company_core(alias)}
            context["_sam_uei"] = normalize_match_text(_string(row.get("uei") or row.get("uei_sam")))
            context["_sam_cage"] = normalize_match_text(_string(row.get("cage_code") or row.get("cage")))
            self.contractors[key] = context
        return [SAM_DATA_SERVICES_ENDPOINT] if self.contractors else []

    def allowed_url(self, url: str) -> bool:
        parts = urlsplit(url)
        return parts.scheme == "https" and (parts.hostname or "").lower() in {"sam.gov", "www.sam.gov"}

    @staticmethod
    def _validated_download_url(value: str) -> str:
        url = urljoin(SAM_DATA_SERVICES_ENDPOINT, value)
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or parts.username or parts.password or not (host == "sam.gov" or host.endswith(".sam.gov")):
            raise ValueError("SAM manifest returned an unapproved download host")
        return url

    @staticmethod
    def _cache_path(extension: str) -> Path:
        extension = extension if extension in {".zip", ".csv"} else ".zip"
        return SAM_CACHE_DIR / f"public-v2{extension}"

    async def acquire(self, client) -> None:
        """Fetch listing and artifact, then atomically replace the validated cache."""
        SAM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        metadata_path = SAM_CACHE_DIR / "metadata.json"
        old = {}
        try:
            old = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        listing = await client.get(SAM_DATA_SERVICES_ENDPOINT, headers={"Accept": "application/json"})
        if listing.status_code >= 400:
            raise ValueError(f"SAM file listing HTTP {listing.status_code}")
        artifact = select_manifest_artifact(listing.content)
        download_url = self._validated_download_url(artifact["download_url"])
        headers = {}
        if old.get("download_url") == download_url:
            if old.get("etag"):
                headers["If-None-Match"] = old["etag"]
            if old.get("last_modified"):
                headers["If-Modified-Since"] = old["last_modified"]
        response = await client.get(download_url, headers=headers, follow_redirects=False)
        for _ in range(5):
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            download_url = self._validated_download_url(response.headers.get("location", ""))
            response = await client.get(download_url, headers=headers, follow_redirects=False)
        if response.status_code == 304:
            artifact_path = self._cache_path(_string(old.get("extension")))
            if not artifact_path.is_file() or not old.get("sha256"):
                raise ValueError("SAM returned 304 without a validated cached artifact")
            actual = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
            if actual != old["sha256"]:
                raise ValueError("cached SAM artifact failed integrity validation")
            age = (datetime.now(timezone.utc) - _parse_time(old["retrieved_at"])).total_seconds()
            self.artifact_metadata = {**old, "retained_artifact_age_seconds": max(0, int(age)), "cache_reused": True}
            if age > SAM_CACHE_MAX_AGE_SECONDS:
                self.incomplete_reason = "cached SAM artifact is stale and was not used"
                raise ValueError(self.incomplete_reason)
            self.process_artifact(artifact_path, self.artifact_metadata)
            return
        if response.status_code != 200:
            age = None
            if old.get("retrieved_at"):
                age = max(0, int((datetime.now(timezone.utc) - _parse_time(old["retrieved_at"])).total_seconds()))
            self.artifact_metadata = {**old, "retained_artifact_age_seconds": age}
            self.incomplete_reason = f"artifact refresh failed with HTTP {response.status_code}; retained artifact was not used"
            raise ValueError(self.incomplete_reason)
        body = response.content
        declared = response.headers.get("content-length")
        if (declared and int(declared) > SAM_MAX_COMPRESSED_BYTES) or len(body) > SAM_MAX_COMPRESSED_BYTES:
            raise ValueError("SAM artifact exceeds the compressed-size limit")
        kind = response.headers.get("content-type", "").lower()
        if artifact["extension"] == ".zip" and kind and not any(x in kind for x in ("zip", "octet-stream")):
            raise ValueError("SAM artifact has an unexpected content type")
        if artifact["extension"] == ".csv" and kind and not any(x in kind for x in ("csv", "text/plain", "octet-stream")):
            raise ValueError("SAM CSV artifact has an unexpected content type")
        fd, temporary = tempfile.mkstemp(prefix=".sam-download-", suffix=artifact["extension"], dir=SAM_CACHE_DIR)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(body); handle.flush(); os.fsync(handle.fileno())
            temp_path = Path(temporary)
            metadata = {**artifact, "download_url": download_url, "etag": response.headers.get("etag", ""),
                "last_modified": response.headers.get("last-modified", ""), "compressed_size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(), "schema_version": "Public V2",
                "retrieved_at": datetime.now(timezone.utc).isoformat(), "cache_reused": False}
            # Parsing is validation: bad input never replaces the last known-good file.
            self.process_artifact(temp_path, metadata)
            artifact_path = self._cache_path(artifact["extension"])
            os.replace(temp_path, artifact_path)
            for obsolete in (self._cache_path(".zip"), self._cache_path(".csv")):
                if obsolete != artifact_path:
                    try:
                        obsolete.unlink()
                    except FileNotFoundError:
                        pass
            meta_temp = metadata_path.with_suffix(".json.tmp")
            meta_temp.write_text(json.dumps(metadata, sort_keys=True, indent=2), encoding="utf-8")
            os.replace(meta_temp, metadata_path)
        finally:
            try:
                Path(temporary).unlink()
            except FileNotFoundError:
                pass

    @staticmethod
    def _location(row):
        return {"address_1": row.get("address_1"), "city": row.get("city"), "state": row.get("state"), "zip": row.get("zip")}

    def _add_ambiguous(self, key: str, identifier: str, row: dict, *, reason: str,
                       name_similarity: float, location: dict) -> None:
        if len(self.ambiguous[key]) >= SAM_MAX_AMBIGUOUS_PER_CONTRACTOR:
            return
        self.ambiguous[key][identifier] = {
            **row,
            "reason": reason,
            "name_similarity": round(name_similarity, 4),
            "location_basis": location.get("basis", ""),
        }

    def consume_rows(self, rows):
        uei_index, cage_index, name_index = defaultdict(list), defaultdict(list), defaultdict(list)
        for key, master in self.contractors.items():
            if master["_sam_uei"]:
                uei_index[master["_sam_uei"]].append(key)
            if master["_sam_cage"]:
                cage_index[master["_sam_cage"]].append(key)
            for name in master["_sam_names"]:
                name_index[name].append(key)

        for row in rows:
            if not row.get("active"):
                continue
            identifier = _string(row.get("exclusion_identifier"))
            if not identifier:
                raise SamExtractError("SAM row has no official exclusion identifier")
            uei = normalize_match_text(row.get("uei", ""))
            cage = normalize_match_text(row.get("cage_code", ""))
            name = company_core(row.get("entity_name", ""))
            is_firm = normalize_match_text(row.get("classification", "")) in {"firm", "entity", "organization"}

            identifier_candidates = []
            identifier_basis = ""
            if uei and uei in uei_index:
                identifier_candidates, identifier_basis = uei_index[uei], "exact UEI"
            elif cage and cage in cage_index:
                identifier_candidates, identifier_basis = cage_index[cage], "exact CAGE"

            if len(identifier_candidates) == 1:
                key = identifier_candidates[0]
                self.matches[key][identifier] = {**row, "matching_basis": identifier_basis}
                continue
            if len(identifier_candidates) > 1:
                for key in identifier_candidates:
                    location = _location_evidence(self._location(row), self.contractors[key])
                    self._add_ambiguous(
                        key, identifier, row,
                        reason=f"{identifier_basis} maps to multiple approved contractors",
                        name_similarity=max((_name_similarity(name, target) for target in self.contractors[key]["_sam_names"]), default=0.0),
                        location=location,
                    )
                continue

            if not is_firm or not name:
                continue

            # Exact company name/approved alias still needs meaningful location
            # corroboration unless a unique authoritative identifier matched above.
            exact_keys = name_index.get(name, [])
            for key in exact_keys:
                location = _location_evidence(self._location(row), self.contractors[key])
                if len(exact_keys) == 1 and location["corroborates"]:
                    self.matches[key][identifier] = {
                        **row,
                        "matching_basis": f"exact approved name/alias plus {location['basis']}",
                        "location_basis": location["basis"],
                    }
                else:
                    self._add_ambiguous(
                        key, identifier, row,
                        reason="exact approved name/alias but location is missing, conflicting, or not unique",
                        name_similarity=1.0,
                        location=location,
                    )
            if exact_keys:
                continue

            # Fuzzy names are review-only and require strong independent location
            # corroboration. A shared city/state by itself is never enough.
            for target, keys in name_index.items():
                plausible, score = _plausible_fuzzy_name(name, target)
                if not plausible:
                    continue
                for key in keys:
                    location = _location_evidence(self._location(row), self.contractors[key])
                    if not location["corroborates"] or location["strength"] < 2:
                        continue
                    self._add_ambiguous(
                        key, identifier, row,
                        reason="near-exact company name with independently corroborating location; manual review required",
                        name_similarity=score,
                        location=location,
                    )

    def process_artifact(self, path: Path, metadata: dict):
        self.artifact_metadata = metadata
        self.consume_rows(iter_public_v2_rows(path, max_uncompressed=SAM_MAX_UNCOMPRESSED_BYTES, artifact=metadata))
        self.extract_complete = True
        self.incomplete_reason = ""

    def extract(self, text, url):
        return []

    def links(self, text, url):
        return []

    def finalize_records(self, complete: bool) -> list[dict]:
        complete = bool(complete and self.extract_complete)
        records = []
        for key, contractor in self.contractors.items():
            matches = list(self.matches[key].values())
            ambiguous = list(self.ambiguous[key].values())
            name = _string(contractor.get("contractor_name"))
            bidder_id = _string(contractor.get("id"))
            if matches:
                narrative = f"Confirmed {len(matches)} active federal exclusion(s)."
            elif ambiguous:
                narrative = f"No confirmed exclusion; {len(ambiguous)} candidate(s) require manual identity review."
            elif complete:
                narrative = "Completed federal extract scan; no sufficiently corroborated active exclusion was found. The combined state/federal field remains unchanged."
            else:
                narrative = "UNKNOWN / INCOMPLETE: the federal extract could not be fully validated."
            records.append({
                "external_id": f"sam:bidder:{bidder_id or normalize_match_text(name)}",
                "company": name,
                "bidder_id": bidder_id,
                "date": max([_string(m.get("activation_date")) for m in matches] or [""]),
                "source_url": SAM_PUBLIC_SEARCH,
                "state_federal_debarment": "Y" if matches else "",
                "extra": {
                    "master_id": contractor.get("_master_id"),
                    "source_system": "SAM.gov Exclusions Public V2 daily extract",
                    "federal_component_only": True,
                    "negative_result_writes_combined_field": False,
                    "complete_aggregate": complete,
                    "incomplete_reason": "" if complete else self.incomplete_reason or "crawl incomplete",
                    "artifact": self.artifact_metadata,
                    "active_federal_exclusions": matches,
                    "confirmed_exclusions": matches,
                    "ambiguous_candidates": ambiguous,
                    "public_review_url": SAM_PUBLIC_SEARCH,
                    "narrative": narrative,
                },
            })
        return records
