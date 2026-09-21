"""Safe, structured failures produced while acquiring a public source."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal
from urllib.parse import urlsplit, urlunsplit


Stage = Literal[
    "manifest", "robots", "sitemap_index", "sitemap_child", "artifact_download",
    "archive_validation", "parse", "match", "browser_probe", "profile",
    "complaints", "search", "results", "pagination", "detail", "finalize",
]

CATEGORIES = frozenset({
    "access_block", "rate_limit", "upstream_5xx", "timeout", "network",
    "redirect_policy", "content_limit", "archive_invalid", "schema_drift",
    "parse_error", "identity_ambiguous", "internal_error",
})


def safe_logical_url(value: str | None) -> str | None:
    """Retain useful public provenance but never userinfo, fragments, or queries."""
    if not value:
        return None
    try:
        parts = urlsplit(str(value))
        host = parts.hostname or ""
        if not host:
            return None
        if ":" in host:
            host = f"[{host}]"
        if parts.port:
            host += f":{parts.port}"
        return urlunsplit((parts.scheme, host, parts.path or "/", "", ""))
    except (TypeError, ValueError):
        return None


@dataclass
class SourceAcquisitionError(ValueError):
    source_id: int | None
    source_name: str
    stage: Stage
    logical_url: str | None
    upstream_host: str | None
    http_method: str
    upstream_status: int | None
    category: str
    acquisition_mode: str
    retryable: bool
    attempt_no: int
    safe_message: str
    exception_type: str = "SourceAcquisitionError"

    def __post_init__(self):
        from .activity import redact
        self.source_name = redact(self.source_name)[:200]
        self.logical_url = safe_logical_url(self.logical_url)
        self.upstream_host = (urlsplit(self.logical_url).hostname if self.logical_url else self.upstream_host)
        self.http_method = self.http_method.upper()[:12]
        self.category = self.category if self.category in CATEGORIES else "internal_error"
        self.safe_message = redact(self.safe_message)[:1000]
        super().__init__(self.safe_message)

    def public_dict(self) -> dict:
        value = asdict(self)
        value["message"] = value.pop("safe_message")
        return value

    @classmethod
    def from_exception(cls, exc: Exception, **context) -> "SourceAcquisitionError":
        category = "internal_error"
        if isinstance(exc, TimeoutError):
            category = "timeout"
        return cls(category=category, retryable=category == "timeout",
                   safe_message=str(exc) or type(exc).__name__,
                   exception_type=type(exc).__name__, **context)
