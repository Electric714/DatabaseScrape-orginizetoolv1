"""Typed, redacted acquisition diagnostics shared by all source adapters."""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

_URL = re.compile(r"https?://[^\s<>\"']+")
_SECRET_NAME = re.compile(r"(?i)(authorization|cookie|password|token|api[_-]?key|secret|credential|session)")
_SECRET_VALUE = re.compile(r"(?i)(authorization|cookie|password|token|api[_-]?key|secret)(\s*[:=]\s*)([^\s,;]+)")
_SENSITIVE_BODY = re.compile(r"(?i)(body|payload|response|html|content|raw|document|source_text)")


class AcquisitionStage(str, Enum):
    PREPARE = "prepare"
    POLICY = "policy"
    DISCOVERY = "discovery"
    DOWNLOAD = "download"
    PARSE = "parse"
    MATCH = "match"
    PERSIST = "persist"
    FINALIZE = "finalize"


class AcquisitionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    BLOCKED = "blocked"
    FAILED = "failed"


class RetryClass(str, Enum):
    NEVER = "never"
    TRANSIENT = "transient"
    RATE_LIMITED = "rate_limited"
    AFTER_CONFIGURATION = "after_configuration"
    AFTER_SOURCE_CHANGE = "after_source_change"


@dataclass(slots=True)
class AcquisitionFailure(Exception):
    code: str
    message: str
    stage: AcquisitionStage
    status: AcquisitionStatus = AcquisitionStatus.FAILED
    retry: RetryClass = RetryClass.NEVER
    http_status: int | None = None

    def __str__(self) -> str:
        return self.message

    @property
    def retryable(self) -> bool:
        return self.retry in {RetryClass.TRANSIENT, RetryClass.RATE_LIMITED}

    def details(self) -> dict[str, Any]:
        return {"failure_code": self.code, "stage": self.stage.value,
                "acquisition_status": self.status.value, "retry_class": self.retry.value,
                "http_status": self.http_status}


def classify_failure(exc: BaseException, stage: AcquisitionStage) -> AcquisitionFailure:
    if isinstance(exc, AcquisitionFailure):
        return exc
    text = str(exc)
    lowered = text.casefold()
    status_match = re.search(r"\bhttp\s+(\d{3})\b", lowered)
    http_status = int(status_match.group(1)) if status_match else None
    name = type(exc).__name__.casefold()
    if http_status == 429:
        return AcquisitionFailure("rate_limited", text, stage, AcquisitionStatus.INCOMPLETE, RetryClass.RATE_LIMITED, http_status)
    if http_status in {500, 502, 503, 504} or "timeout" in name or "network" in name:
        return AcquisitionFailure("temporary_network_failure", text or type(exc).__name__, stage, AcquisitionStatus.INCOMPLETE, RetryClass.TRANSIENT, http_status)
    if http_status in {401, 403} or "access challenge" in lowered or "access denied" in lowered:
        retry = RetryClass.AFTER_CONFIGURATION if http_status == 401 else RetryClass.AFTER_SOURCE_CHANGE
        return AcquisitionFailure("access_blocked", text, stage, AcquisitionStatus.BLOCKED, retry, http_status)
    if "api key" in lowered or "credential" in lowered:
        return AcquisitionFailure("credential_required", text, stage, AcquisitionStatus.BLOCKED, RetryClass.AFTER_CONFIGURATION, http_status)
    if any(word in lowered for word in ("malformed", "invalid json", "unrecognized", "decoder", "unexpected response")):
        return AcquisitionFailure("malformed_response", text, stage, AcquisitionStatus.INCOMPLETE, RetryClass.AFTER_SOURCE_CHANGE, http_status)
    return AcquisitionFailure("acquisition_failed", text or type(exc).__name__, stage, AcquisitionStatus.FAILED, RetryClass.NEVER, http_status)


def redact(value: Any) -> str:
    def safe_url(match):
        try:
            url = urlsplit(match[0])
            return urlunsplit((url.scheme, url.hostname or "", url.path, "", ""))
        except ValueError:
            return "[invalid URL]"
    text = _URL.sub(safe_url, str(value))
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)(?:Bearer|Basic)\s+\S+", r"\1[redacted]", text)
    text = _SECRET_VALUE.sub(r"\1\2[redacted]", text)
    text = re.sub(r"(?i)[A-Z]:\\Users\\[^\\\s]+", r"C:\\Users\\[user]", text)
    return text[:12000]


def sanitize(value: Any, *, key: str = "") -> Any:
    """Recursively remove secrets and acquired bodies before persistence/export."""
    if _SECRET_NAME.search(key) or _SENSITIVE_BODY.fullmatch(key):
        return "[redacted]"
    if isinstance(value, dict):
        return {str(k): sanitize(v, key=str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize(item, key=key) for item in value]
    return redact(value)
