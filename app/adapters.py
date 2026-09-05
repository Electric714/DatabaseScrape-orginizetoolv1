from typing import Protocol

from .extractor import discover_links, extract_records


class SourceAdapter(Protocol):
    def extract(self, html: str, url: str) -> list[dict]: ...
    def links(self, html: str, url: str) -> list[str]: ...


class GenericAdapter:
    """Fallback adapter. Real target sites can subclass/replace this deterministically."""

    def extract(self, html: str, url: str) -> list[dict]:
        return extract_records(html, url)

    def links(self, html: str, url: str) -> list[str]:
        return discover_links(html, url)


def adapter_for_url(_url: str) -> SourceAdapter:
    # Site-specific adapters will be selected here once the six target sites are known.
    return GenericAdapter()
