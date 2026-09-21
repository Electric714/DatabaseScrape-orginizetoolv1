"""Safety tests for the intentionally unavailable Violation Tracker source.

Fixture-backed parser tests belong here only after the discovery prerequisites
in docs/VIOLATION-TRACKER.md have been completed. Invented HTML would falsely
claim that the acceptance gate had passed.
"""

import pytest

from app.adapters import adapter_for_url
from app.violation_tracker_adapter import (
    VT_FIELDS,
    ViolationTrackerAccessBlock,
    ViolationTrackerAdapter,
)


def test_registered_adapter_fails_before_network_or_form_submission():
    adapter = adapter_for_url("https://violationtracker.goodjobsfirst.org/")
    assert isinstance(adapter, ViolationTrackerAdapter)
    with pytest.raises(ViolationTrackerAccessBlock, match="access_block"):
        adapter.seed_urls([{"id": "1", "contractor_name": "Approved Co"}])


def test_unverified_html_cannot_be_treated_as_results_or_zero_results():
    adapter = ViolationTrackerAdapter()
    for html in (
        "<h1>Access denied</h1>",
        "<p>No results</p>",
        "<table><tr><td>Invented result</td></tr></table>",
    ):
        with pytest.raises(ViolationTrackerAccessBlock, match="unverified"):
            adapter.links(html, "https://violationtracker.goodjobsfirst.org/")


def test_access_block_cannot_produce_negative_positive_or_new_contractor():
    adapter = ViolationTrackerAdapter()
    assert adapter.finalize_records(complete=False) == []
    assert adapter.finalize_records(complete=True) == []
    assert adapter.extract("<html></html>", adapter.canonical_start_url) == []
    assert set(adapter.master_fields) == set(VT_FIELDS)
    assert not adapter.allowed_url(adapter.canonical_start_url)
