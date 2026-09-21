"""SAM.gov daily Public V2 exclusions extract adapter (no credential required)."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import defaultdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .bidder_schema import normalize_match_text
from .config import DATA_DIR
from .identity import location_corroborates
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

    async def acquire(self, client) -> None:
        """Fetch listing and artifact, then atomically replace the validated cache."""
        SAM_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        metadata_path, artifact_path = SAM_CACHE_DIR / "metadata.json", SAM_CACHE_DIR / "public-v2.zip"
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
            if old.get("etag"): headers["If-None-Match"] = old["etag"]
            if old.get("last_modified"): headers["If-Modified-Since"] = old["last_modified"]
        response = await client.get(download_url, headers=headers, follow_redirects=False)
        for _ in range(5):
            if response.status_code not in {301, 302, 303, 307, 308}:
                break
            download_url = self._validated_download_url(response.headers.get("location", ""))
            response = await client.get(download_url, headers=headers, follow_redirects=False)
        if response.status_code == 304:
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
        if declared and int(declared) > SAM_MAX_COMPRESSED_BYTES or len(body) > SAM_MAX_COMPRESSED_BYTES:
            raise ValueError("SAM artifact exceeds the compressed-size limit")
        kind = response.headers.get("content-type", "").lower()
        if artifact["extension"] == ".zip" and kind and not any(x in kind for x in ("zip", "octet-stream")):
            raise ValueError("SAM artifact has an unexpected content type")
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
            os.replace(temp_path, artifact_path)
            meta_temp = metadata_path.with_suffix(".json.tmp")
            meta_temp.write_text(json.dumps(metadata, sort_keys=True, indent=2), encoding="utf-8")
            os.replace(meta_temp, metadata_path)
        finally:
            try: Path(temporary).unlink()
            except FileNotFoundError: pass

    @staticmethod
    def _location(row):
        return {"address": row.get("address_1"), "city": row.get("city"), "state": row.get("state"), "zip": row.get("zip")}

    def consume_rows(self, rows):
        uei_index, cage_index, name_index = defaultdict(list), defaultdict(list), defaultdict(list)
        for key, master in self.contractors.items():
            if master["_sam_uei"]: uei_index[master["_sam_uei"]].append(key)
            if master["_sam_cage"]: cage_index[master["_sam_cage"]].append(key)
            for name in master["_sam_names"]: name_index[name].append(key)
        for row in rows:
            if not row.get("active"):
                continue
            identifier = _string(row.get("exclusion_identifier"))
            if not identifier:
                raise SamExtractError("SAM row has no official exclusion identifier")
            uei, cage = normalize_match_text(row.get("uei", "")), normalize_match_text(row.get("cage_code", ""))
            name = company_core(row.get("entity_name", ""))
            is_firm = normalize_match_text(row.get("classification", "")) in {"firm", "entity", "organization"}
            candidates, basis = [], ""
            if uei and uei in uei_index: candidates, basis = uei_index[uei], "exact UEI"
            elif cage and cage in cage_index: candidates, basis = cage_index[cage], "exact CAGE"
            elif is_firm and name in name_index:
                candidates = [key for key in name_index[name] if location_corroborates(self._location(row), self.contractors[key])]
                basis = "exact approved name/alias plus location"
            if len(candidates) == 1:
                self.matches[candidates[0]][identifier] = {**row, "matching_basis": basis}
                continue
            plausible = set(candidates)
            if not plausible and name and is_firm:
                for target, keys in name_index.items():
                    if name == target or (len(name) >= 6 and SequenceMatcher(None, name, target).ratio() >= .86):
                        plausible.update(keys)
            for key in plausible:
                self.ambiguous[key][identifier] = {**row, "reason": "shared identifier, multiple master match, or name/location conflict"}

    def process_artifact(self, path: Path, metadata: dict):
        if metadata.get("extension") == ".csv":
            raise SamExtractError("direct CSV artifacts are not enabled until their transport encoding is fixture-validated")
        self.artifact_metadata = metadata
        self.consume_rows(iter_public_v2_rows(path, max_uncompressed=SAM_MAX_UNCOMPRESSED_BYTES, artifact=metadata))
        self.extract_complete = True
        self.incomplete_reason = ""

    def extract(self, text, url): return []
    def links(self, text, url): return []

    def finalize_records(self, complete: bool) -> list[dict]:
        complete = bool(complete and self.extract_complete)
        records = []
        for key, contractor in self.contractors.items():
            matches = list(self.matches[key].values())
            ambiguous = list(self.ambiguous[key].values())
            name = _string(contractor.get("contractor_name")); bidder_id = _string(contractor.get("id"))
            records.append({
                "external_id": f"sam:bidder:{bidder_id or normalize_match_text(name)}", "company": name,
                "bidder_id": bidder_id, "date": max([_string(m.get("activation_date")) for m in matches] or [""]),
                "source_url": SAM_PUBLIC_SEARCH, "state_federal_debarment": "Y" if matches else "",
                "extra": {"master_id": contractor.get("_master_id"), "source_system": "SAM.gov Exclusions Public V2 daily extract",
                    "federal_component_only": True, "negative_result_writes_combined_field": False,
                    "complete_aggregate": complete, "incomplete_reason": "" if complete else self.incomplete_reason or "crawl incomplete",
                    "artifact": self.artifact_metadata, "active_federal_exclusions": matches,
                    "confirmed_exclusions": matches, "ambiguous_candidates": ambiguous, "public_review_url": SAM_PUBLIC_SEARCH,
                    "narrative": (f"Confirmed {len(matches)} active federal exclusion(s)." if matches else
                        "Completed federal extract scan; the combined state/federal field remains unchanged." if complete else
                        "UNKNOWN / INCOMPLETE: the federal extract could not be fully validated.")}}
            )
        return records
