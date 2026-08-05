# ADR-0006: Staleness detection — non-forced prefill as the primary oracle

Date: 2026-08-06
Status: Accepted (orchestrator decision from evidence; see docs/research/phase3-manifests.md)

## Decision

1. The primary staleness check is a **SteamPrefill run WITHOUT `--force`**:
   SteamPrefill's own up-to-date bookkeeping makes it a ~3 s, zero-download
   no-op for current apps (measured). The summary counters (`Updated`,
   `Up To Date`) become the job outcome; `Updated==0 AND UpToDate==0`
   means the app was never considered (unowned/filtered) and the job must
   NOT end `done` (job-outcome honesty, WP 1.7 finding).
2. `--force` is reserved for first fills and post-deletion refills via a
   per-app `needs_force` flag set by the deletion path (preserves the
   WP 1.4 rationale: SteamPrefill's state does not know about our deletes).
3. Manifest state is recorded per depot from SteamPrefill's temp-cache
   filenames (`{appid}_{containingapp}_{depotid}_{manifestid}.bin`) into a
   `depot_manifests` table; the `.bin` files are archived durably (they
   survive `clear-temp`).
4. An optional, opt-in, fail-soft third-party oracle
   (`VAULT_MANIFEST_ORACLE=steamcmd_api`) may set the pre-emptive orange
   "stale" badge between cron ticks. Default off; unaffiliated with Valve;
   public branch only.

## Rejected

- `ISteamApps/UpToDateCheck`: only answers for apps with dedicated-server
  versions; returns server version, not buildid (live-tested against five
  apps — four failed).
- SteamCMD binary / ValvePython PICS in-process: ~250 MB runtime or a
  heavy dependency stack inside a deliberately small service (plan §9).

## Honest limits

Tier 1 gives "current as of <timestamp>", not a pre-emptive stale badge;
each per-app check costs a Steam login (~3 s) — sweeps are spaced across
the cron window, not batched (batching would destroy per-app attribution).
