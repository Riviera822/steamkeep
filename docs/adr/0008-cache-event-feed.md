# ADR-0008: Cache-event feed from vault-core via a dedicated structured log

Date: 2026-08-09
Status: Accepted (orchestrator decision under explicit user authorization to
decide the core→api feed question and document it; the user can overturn it —
this ADR names every option for that purpose)

## Context

Two remaining Phase-3 features need vault-api to know what requests actually
reached vault-core:

1. **Miss-triggered prefill completion** (hybrid decision, ADR-0001): a cache
   MISS on a depot of an unknown or partially-cached app should queue a
   prefill job so one player's download completes the cache for everyone.
2. **Per-client hit statistics + bypass detection** (plan §5/§6): which
   machines are being served, at what hit rate — and which machines report
   installed games via vault-agent but never appear at the cache at all
   (DNS bypass, the plan's own pain point).

Plan §1 positions this project against LanCache's "parse the access log to
attribute content" approach. That stance must be read precisely: what it
rejects is deriving *content ownership* (chunk→game attribution) from
URL-pattern parsing — fragile, and this project gets that from SteamPrefill
manifests instead (ADR-0006/0007). It does not forbid using nginx's own
logging for *coarse request facts* (who asked, was it a HIT, how many bytes),
which no other source can provide.

## Options considered

- **A — tail the standard access log.** Rejected: the combined-format log is
  built for humans, mixes non-cache traffic, needs brittle parsing, and
  rotating it fights every other consumer of that file.
- **B — per-request live feed (nginx `mirror`/`post_action` subrequest to a
  vault-api endpoint).** Rejected: a Steam download is tens of thousands of
  chunk requests; mirroring each one doubles request volume and turns the
  single-threaded API into a hot-path dependency of the serving path.
  vault-core must keep serving even when vault-api is down (deploy §
  independence), and a live feed breaks that.
- **C — dedicated structured event log + periodic sweep (CHOSEN).**
  vault-core writes a second, purpose-built `access_log` with a minimal,
  machine-readable line format (timestamp, client address, upstream cache
  status HIT/MISS/BYPASS, depot-bearing path prefix, bytes) — buffered, cheap,
  and entirely inside nginx. vault-api sweeps the file on the existing
  WP 3.5 scheduler cadence with a persisted byte-offset cursor, so each line
  is read once, a sweep failure re-reads instead of losing data, and rotation
  happens only after a successful sweep. The serving path never depends on
  the API being alive.

## Decision

Option C. Consequences and boundaries:

- The event log carries **request facts only**. Content attribution (which
  chunks belong to which app) stays with manifests (ADR-0006/0007) — the
  event log's depot id is used to *look up* the depot→app mapping vault-api
  already owns, never to build it.
- Miss-trigger rule: a MISS for a depot mapped to an app that is not
  currently cached-and-current MAY enqueue a non-forced prefill for that app,
  with strict dedupe (the existing per-app job dedupe) and a per-app cooldown
  so a busy download night cannot enqueue storms. Misses on unmapped depots
  are counted but trigger nothing (no mapping = no honest target; the
  readiness thinking of ADR-0007 applied to job creation).
- Client identity: event-log lines carry addresses; vault-agent reports
  already arrive FROM those addresses, so vault-api records the report source
  address and correlates. A client that reports installed games but has no
  cache-log presence within the report window is `bypass_suspected`.
- The feed is optional at runtime: vault-core without the extra log (or
  vault-api without read access to it) degrades to today's behavior —
  serving, prefill, GC all unaffected.
