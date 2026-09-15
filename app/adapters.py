from typing import Protocol
from urllib.parse import urlsplit

from .extractor import discover_links, extract_records
from .osha_adapter import OshaEstablishmentAdapter
from .sam_adapter import SamExclusionsAdapter
from .bbb_adapter import BbbComplaintsAdapter
from .state_adapter import MinnesotaDebarmentAdapter
from .state_sources import IllinoisDebarmentAdapter, WisconsinDebarmentAdapter
from .violation_tracker_adapter import ViolationTrackerAdapter


class SourceAdapter(Protocol):
    def extract(self, html: str, url: str) -> list[dict]: ...
    def links(self, html: str, url: str) -> list[str]: ...
    def allowed_url(self, url: str) -> bool: ...


class GenericAdapter:
    """Fallback adapter. Real target sites can subclass/replace this deterministically."""

    def extract(self, html: str, url: str) -> list[dict]:
        return extract_records(html, url)

    def links(self, html: str, url: str) -> list[str]:
        return discover_links(html, url)

    def allowed_url(self, url: str) -> bool:
        return True


class BrowserBbbComplaintsAdapter(BbbComplaintsAdapter):
    """BBB parser fetched through Chromium's normal document network stack.

    Real diagnostics showed BBB challenging the raw HTTP client before the first
    search page could be parsed. This changes only the acquisition mechanism for
    the same narrowly allowed search/profile URLs. The crawler still detects an
    access challenge after navigation and fails closed rather than bypassing it.
    """

    direct_browser = True


# Register exact hostnames here. Adapters return a stable, namespaced external_id
# and can narrow query/pagination boundaries, but cannot widen the network policy.
ADAPTERS: dict[str, type[GenericAdapter]] = {
    # Existing OSHA HTML sources are kept as aliases so old local databases migrate
    # transparently to the canonical DOL API endpoint at scan time.
    "www.osha.gov": OshaEstablishmentAdapter,
    "apiprod.dol.gov": OshaEstablishmentAdapter,
    "api.dol.gov": OshaEstablishmentAdapter,
    "api-alpha.sam.gov": SamExclusionsAdapter,
    "api.sam.gov": SamExclusionsAdapter,
    "violationtracker.goodjobsfirst.org": ViolationTrackerAdapter,
    "mn.gov": MinnesotaDebarmentAdapter,
    "labor.illinois.gov": IllinoisDebarmentAdapter,
    "wisconsindot.gov": WisconsinDebarmentAdapter,
    "www.wisconsindot.gov": WisconsinDebarmentAdapter,
    "www.bbb.org": BrowserBbbComplaintsAdapter,
    "bbb.org": BrowserBbbComplaintsAdapter,
}


def adapter_for_url(url: str) -> SourceAdapter:
    return ADAPTERS.get((urlsplit(url).hostname or "").lower(), GenericAdapter)()
