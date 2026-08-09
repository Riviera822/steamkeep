# ADR-0007: Garbage collection by manifest diff; time-based attribution rejected

Date: 2026-08-06
Status: Accepted (orchestrator decision from evidence; see docs/research/phase3-manifests.md)

## Decision

`POST /v1/cache/{appid}/gc` runs as a queued job (serialized with prefills
on the single worker), **dry-run by default**:

1. Chunk "keep" sets come from parsed manifests — SteamPrefill's `.bin`
   temp-cache files and/or cache-stored client manifests (ZIP → sectioned
   payload → protobuf chunk SHAs; ~60 lines of stdlib parsing, no
   dependencies). Both sources yield identical chunk sets (proven against
   ~12,000 on-disk files: zero orphans, byte-exact `cb_compressed` sizes).
2. Shared depots: keep = UNION of the current manifests of ALL apps mapped
   to the depot. Readiness gate: if any mapped app has no resolvable
   manifest, the depot is skipped and reported — never GC on partial
   knowledge.
3. Deletion goes through the WP 1.6 guard path (settle-and-recheck,
   link-safe removal). Duplicate stored manifests per (depot, manifest)
   are deduped to one copy.

## Rejected: time-based attribution ("delete chunks untouched during the job")

Two independent, measured reasons:
- nginx `proxy_store` stamps stored files with the upstream
  `Last-Modified` — on-disk mtimes are content publish times spread over
  months, not fetch times.
- A current, already-cached chunk is served as a HIT and never rewritten —
  a time-based GC would delete exactly the chunks it must keep. Recovering
  HITs would require access-log parsing, the LanCache anti-pattern this
  project exists to eliminate (plan §1).

## Addendum (2026-08-09, WP 3.7): uncached mapped apps in the keep set

Resolves the open note at the end of ADR-0003's addendum. In `plan_gc`:

- An app that is **verifiably idle, never prefilled, job-free AND has no
  `depot_manifests` row** is excluded from the union requirement — it
  neither pins chunks nor blocks the readiness gate. A blocking uncached
  co-owner would reproduce the WP 3.6 last-remnant leak (mapping rows
  survive deletion by design, so the block would be permanent).
- Fail-closed retained: a missing `apps` row, non-idle status, a set
  `last_prefill_at`, an active job, or an unreadable row all make the app
  COUNT. An uncached app WITH a recorded manifest row also counts — a
  claim vault-api wrote down but cannot resolve is exactly the partial
  knowledge the gate exists for.
- A depot where EVERY mapped app is excluded is `skipped_no_counting_apps`,
  never "everything is an orphan". That end state belongs to
  `DELETE /v1/cache/{appid}`'s last-remnant rule (execute-time recheck,
  `needs_force` bookkeeping); GC does not grow a second, weaker deletion
  path for the same state.

## Honest limits

GC can delete chunks a client pinned to an unrecorded/beta manifest still
wants (consequence: re-download, never corruption); unknown-manifest and
unmapped depots are skipped and reported; GC reclaims space but does not
certify completeness; no atomicity against concurrent client writes
(benign: re-fetch).
