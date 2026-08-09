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

## Addendum (2026-08-06): last-remnant shared depots are deletable

### Problem

Mapping rows surviving deletion (decision above) combined with WP 1.6's
row-based shared-depot protection creates a permanent leak: if apps A and B
share depot D and both are deleted (each deletion keeps D as "shared with
the other"), D's bytes stay on disk forever. Neither app reports as cached,
no operator action reclaims the space, and `/v1/cache/summary` attributes
D's size to apps that say "not cached". Found during the Phase-4 mockup
review.

The Phase-3 GC (ADR-0007) does NOT cover this case: its keep set is the
union of the *current manifests of all mapped apps* — an uncached mapped
app still contributes its full current manifest, so a last-remnant depot
whose chunks are current would be kept entirely. Deferring to GC was
evaluated and rejected.

### Decision

`DELETE /v1/cache/{appid}` may delete a shared depot when **no co-owning
app currently has cache content** — i.e. the depot is the last cached
remnant. Mapping rows are still kept (unchanged from the main decision).

A co-owner "has cache content" is judged conservatively from tracked
state; the depot stays protected if ANY co-owner:

- has `apps.status != 'idle'` (done/stale/error/running — error and
  unknown states protect), or
- has `last_prefill_at` set, or
- has a queued or running job, or
- is unreadable (poisoned mapping row) or has no `apps` row at all.

Only when EVERY co-owner is verifiably idle, never-prefilled and job-free
is the depot deleted. The same rule is re-evaluated at execute time (the
WP 1.6 TOCTOU recheck), so a co-owner that becomes cached or gains a job
between plan and removal still protects the depot.

Consequences:

- Such deletions are reported distinctly in the response (deleted-depot
  entry carries the uncached co-owner ids), never silently merged with
  exclusive deletions.
- Every co-owner of a removed (or removal-attempted) last-remnant depot
  gets `needs_force = 1` — its on-disk state changed or became uncertain
  (ADR-0006 decision 2 semantics).
- Retroactive repair needs no new endpoint: mapping rows survive, so
  re-issuing `DELETE /v1/cache/{appid}` on any co-owning app removes an
  already-orphaned remnant depot.
- Worst case of the conservative judgment being wrong (store-on-miss
  content a client was actively using): a re-download, never corruption —
  the same honest limit ADR-0007 accepts.
- Phase-3 GC implementation note: for consistency, an uncached mapped app
  should not pin chunks in a shared depot's keep set; decide when GC is
  built.
