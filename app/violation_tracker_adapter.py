"""Fail-closed registration for the unavailable Violation Tracker source.

The public search contract has not been captured from this environment.  This
module deliberately contains no guessed form fields, selectors, pagination, or
offense mappings.  See ``docs/VIOLATION-TRACKER.md`` for the discovery gate.
"""

VT_URL = "https://violationtracker.goodjobsfirst.org/"
VT_FIELDS = (
    "environmental_violations",
    "prevailing_wage_violations",
    "misc_violations",
)


class ViolationTrackerAccessBlock(RuntimeError):
    """Raised before collection when the public interface is not verified."""


class ViolationTrackerAdapter:
    """Registered source boundary that cannot collect until discovery passes.

    Keeping a source-specific adapter registered prevents the generic HTML
    extractor from interpreting an access-denied page or an unverified layout.
    """

    query_mode = True
    always_parse = True
    fail_fast_access_errors = True
    canonical_start_url = VT_URL
    master_fields = VT_FIELDS
    availability = "access_block"

    def seed_urls(self, master_rows):
        raise ViolationTrackerAccessBlock(
            "access_block: Violation Tracker public search/result/detail flow "
            "has not been captured and manually verified; collection is disabled"
        )

    def allowed_url(self, url):
        return False

    def links(self, html, url):
        raise ViolationTrackerAccessBlock(
            "access_block: unverified Violation Tracker HTML must not be parsed"
        )

    def extract(self, html, url):
        return []

    def finalize_records(self, complete):
        # No seed can run, and therefore no source record or master proposal can
        # be produced. Absence and access failures are never converted to "N".
        return []
