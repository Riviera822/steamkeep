# ADR-0003: Depot→app mappings are additive, corrections via explicit DELETE

Date: 2026-08-05
Status: Accepted (orchestrator decision during WP 1.3 review, plan-conformant per §4)

## Context

A depot can legitimately belong to multiple apps (shared depots,
redistributables — plan §4). `PUT /v1/mapping/{depotid}` must therefore not
replace existing mappings. But without any removal path, a single wrong
mapping row would be permanent and would poison the shared-depot protection:
the affected depot could never be deleted by WP 1.6's cache deletion.

## Decision

1. `PUT /v1/mapping/{depotid}` stays ADDITIVE — re-PUT with a different
   appid adds a second mapping; both apps then report the depot as shared.
2. Corrections go through the explicit repair path
   `DELETE /v1/mapping/{depotid}/{appid}` (removes exactly one pair, never
   the app row).
3. The prefill flow (WP 1.4+) applies replace-semantics per app: after a
   successful prefill of app X, the authoritative depot set observed during
   that job replaces X's previous mapping rows, so stale mappings from
   Steam-side depot reassignments do not accumulate.
