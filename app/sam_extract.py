"""Strict, streaming reader for the SAM Exclusions Public V2 extract."""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Iterator


class SamExtractError(ValueError):
    """The artifact cannot support a complete SAM scan."""


# Captured from the Public V2 data dictionary. Spelling aliases accommodate
# published label revisions without guessing absent data.
REQUIRED_FIELDS = ("exclusion_identifier", "classification", "entity_name", "record_status")
FIELD_ALIASES = {
    "exclusion_identifier": ("Exclusion ID", "Exclusion Identifier", "SAM Number"),
    "classification": ("Classification", "Classification Type"),
    "entity_name": ("Name", "Entity Name"),
    "record_status": ("Record Status", "Active Status", "Status"),
    "aliases": ("Aliases", "Alias"),
    "uei": ("UEI", "UEI SAM", "Unique Entity ID"),
    "cage_code": ("CAGE Code", "CAGE"),
    "address_1": ("Address 1", "Address Line 1"),
    "address_2": ("Address 2", "Address Line 2"),
    "city": ("City",),
    "state": ("State", "State / Province", "State Or Province Code"),
    "zip": ("ZIP", "ZIP Code", "Postal Code"),
    "country": ("Country", "Country Code"),
    "exclusion_type": ("Exclusion Type",),
    "exclusion_program": ("Exclusion Program",),
    "agency": ("Excluding Agency", "Excluding Agency Name", "Agency"),
    "activation_date": ("Active Date", "Activation Date"),
    "termination_date": ("Termination Date",),
}
ACTIVE_VALUES = {"active", "a", "yes", "y", "true", "1"}
INACTIVE_VALUES = {"inactive", "terminated", "expired", "no", "n", "false", "0"}


def _header_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.strip().lower()).strip()


def _csv_member(archive: zipfile.ZipFile, max_uncompressed: int) -> zipfile.ZipInfo:
    members = archive.infolist()
    if not members:
        raise SamExtractError("SAM ZIP is empty")
    seen: set[str] = set()
    csv_members = []
    total = 0
    for member in members:
        name = member.filename.replace("\\", "/")
        path = PurePosixPath(name)
        if path.is_absolute() or ".." in path.parts or not name or "\x00" in name:
            raise SamExtractError("unsafe path in SAM ZIP")
        folded = name.casefold()
        if folded in seen:
            raise SamExtractError("duplicate ZIP member ambiguity")
        seen.add(folded)
        total += member.file_size
        if total > max_uncompressed or member.file_size > max_uncompressed:
            raise SamExtractError("SAM ZIP exceeds the uncompressed-size limit")
        if not member.is_dir() and path.suffix.lower() in {".zip", ".gz", ".tar", ".7z"}:
            raise SamExtractError("unexpected nested archive in SAM ZIP")
        if not member.is_dir() and path.suffix.lower() == ".csv":
            csv_members.append(member)
    if len(csv_members) != 1:
        raise SamExtractError("SAM ZIP must contain exactly one unambiguous CSV")
    return csv_members[0]


def _iter_csv_text(text, artifact: dict | None) -> Iterator[dict]:
    try:
        reader = csv.reader(text, strict=True)
        headers = next(reader)
        normalized_headers = [_header_key(value) for value in headers]
        if not headers or any(not value for value in normalized_headers):
            raise SamExtractError("SAM CSV has an empty header")
        if len(set(normalized_headers)) != len(normalized_headers):
            raise SamExtractError("SAM CSV contains duplicate headers")
        lookup = {value: index for index, value in enumerate(normalized_headers)}
        columns: dict[str, int] = {}
        for field, aliases in FIELD_ALIASES.items():
            matches = [lookup[_header_key(alias)] for alias in aliases if _header_key(alias) in lookup]
            if len(matches) > 1:
                raise SamExtractError(f"ambiguous columns for {field}")
            if matches:
                columns[field] = matches[0]
        missing = [field for field in REQUIRED_FIELDS if field not in columns]
        if missing:
            raise SamExtractError(
                "SAM CSV schema drift; missing required columns: " + ", ".join(missing)
            )
        count = 0
        for line, values in enumerate(reader, 2):
            if len(values) != len(headers):
                raise SamExtractError(f"malformed or truncated SAM CSV row {line}")
            row = {field: values[index].strip() for field, index in columns.items()}
            status = row.get("record_status", "").casefold()
            if status in ACTIVE_VALUES:
                row["active"] = True
            elif status in INACTIVE_VALUES:
                row["active"] = False
            else:
                raise SamExtractError(
                    f"unrecognized SAM active-status representation at row {line}"
                )
            row["source_artifact"] = dict(artifact or {})
            count += 1
            yield row
        if count == 0:
            raise SamExtractError("SAM CSV contains no data rows")
    except StopIteration as exc:
        raise SamExtractError("SAM CSV is empty") from exc
    except (UnicodeDecodeError, csv.Error) as exc:
        raise SamExtractError("unsupported encoding or malformed SAM CSV") from exc


def iter_public_v2_rows(
    path: str | Path, *, max_uncompressed: int, artifact: dict | None = None
) -> Iterator[dict]:
    """Yield normalized Public V2 rows from an official ZIP or direct CSV artifact."""
    path = Path(path)
    extension = str((artifact or {}).get("extension") or path.suffix).lower()

    if extension == ".csv":
        try:
            if path.stat().st_size > max_uncompressed:
                raise SamExtractError("SAM CSV exceeds the size limit")
            with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as text:
                yield from _iter_csv_text(text, artifact)
        except SamExtractError:
            raise
        except (OSError, UnicodeDecodeError, csv.Error) as exc:
            raise SamExtractError("unsupported encoding or malformed SAM CSV") from exc
        return

    if extension != ".zip":
        raise SamExtractError("SAM artifact must be a ZIP or CSV")

    try:
        archive = zipfile.ZipFile(path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SamExtractError("corrupt SAM ZIP") from exc
    with archive:
        member = _csv_member(archive, max_uncompressed)
        try:
            raw = archive.open(member)
            text = io.TextIOWrapper(
                raw, encoding="utf-8-sig", errors="strict", newline=""
            )
            yield from _iter_csv_text(text, artifact)
        except SamExtractError:
            raise
        except (UnicodeDecodeError, csv.Error) as exc:
            raise SamExtractError("unsupported encoding or malformed SAM CSV") from exc
