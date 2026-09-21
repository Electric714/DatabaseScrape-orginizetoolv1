# Violation Tracker discovery record and request-flow contract

**State:** `access_block` / unavailable / incomplete  
**Last attempted verification:** 2026-09-21 UTC  
**Owner:** Good Jobs First  
**Public origin attempted:** `https://violationtracker.goodjobsfirst.org/`

## Mandatory discovery gate

Implementation is not accepted until a permitted manual browser session can
capture and verify one complete public search → every results page →
representative detail-record flow. Subscriber downloads, authenticated exports,
challenge bypasses, and undocumented private endpoints are explicitly excluded.

On 2026-09-21, exactly one browser reconnaissance navigation was attempted from
this environment. The navigation failed before page content was exposed with
HTTP **401 Unauthorized** from the browser-access gateway. Consequently there
was no response HTML, redirect chain, or challenge body to inspect. An earlier
repository review records HTTP **403** for both the public homepage and search
from this environment. Per the stop rule, no additional site probes were made.

This is evidence of an access block, **not** evidence about the site's current
form, records, or availability to ordinary public browsers. No sanitized live
HTML fixture can honestly be committed from this session.

## Request-flow contract (not yet captured)

Every item below is deliberately `UNVERIFIED`; no value is inferred from the
retired provisional adapter.

| Contract item | Verified value |
| --- | --- |
| Current `robots.txt` | UNVERIFIED — access block occurred before inspection |
| Terms/user guide | UNVERIFIED — access block occurred before inspection |
| Search form action and method | UNVERIFIED |
| Field names and default filters | UNVERIFIED |
| Anti-CSRF fields/session requirements | UNVERIFIED |
| Redirect behavior | UNVERIFIED |
| JavaScript/XHR participation | UNVERIFIED |
| Known-company multi-result search | NOT PERFORMED after stop condition |
| No-result search and its stable landmark | NOT PERFORMED |
| Result identifier and detail URL shape | UNVERIFIED |
| Pagination parameters/tokens and termination | UNVERIFIED |
| Total-count semantics | UNVERIFIED |
| Stable structured data/table labels | UNVERIFIED |
| Result granularity (violation/company/parent/mixed) | UNVERIFIED |

## Required supported-access route

Before implementation resumes, obtain permission or a supported public-access
route from Good Jobs First (for example, confirmation that a normal manual
browser may use the public search and detail interface from the intended
environment). Ask Good Jobs First to clarify permitted automated use, rate
limits, and whether public search requires JavaScript/session state. Do not ask
for or use subscriber downloads or authenticated exports for this adapter.

Once access is supported, a human must record the items above, searches for one
known company with multiple results and one with no results, every pagination
step, and representative details. Save only small sanitized HTML fixtures with
record/company personal data replaced consistently, while retaining structural
landmarks, identifiers, pagination, counts, and offense/agency labels.

## Implementation and review gate

The application registers a fail-closed adapter solely to prevent fallback to
generic HTML parsing. It creates no request, parses no guessed markup, emits no
record, proposes neither `Y` nor `N`, and cannot add a contractor. Therefore the
catalog remains **Blocked / unverified**.

After live discovery, reviewers must require fixture tests for form submission,
all pagination and cycle bounds, validated zero results, canonical-ID
deduplication, detail schema drift, count mismatches, partial failures,
challenge/403 circuit breaking, and identity ambiguity. Classification must
retain primary offense, secondary offense, and agency verbatim; versioned rules
may map only explicit environmental and prevailing-wage/Davis-Bacon categories.
Generic wage-and-hour and unknown/new categories remain review evidence.
Miscellaneous mappings require an approved documented taxonomy. Any access,
parser, pagination, count, detail, or identity uncertainty makes the aggregate
incomplete, and absence can never propose a negative.
