# Phase 3 research: staleness detection & garbage collection

Date: 2026-08-06 · Method: evidence-first (live binaries, live APIs, on-disk
diffs on the dev machine) · Decisions recorded in ADR-0006 / ADR-0007.
This document preserves the evidence behind them.

## Q1 — Staleness detection

### What SteamPrefill stores

- `Config/successfullyDownloadedDepots.json`: `{depotid: [manifestids...]}` —
  no app id, only usable as SteamPrefill's own up-to-date predicate
  (`DepotHandler.AppIsUpToDate`).
- **Manifest temp cache** (the valuable store): `$HOME/.cache/SteamPrefill/v1/`
  (in our container: `/opt/steamprefill/home/.cache/SteamPrefill/v1/`,
  volume-backed). Filename contract from `SteamPrefill/Models/DepotInfo.cs`:
  `{originalAppId}_{containingAppId}_{depotId}_{manifestId}.bin` — yields the
  full app→depot→manifest mapping INCLUDING shared depots (observed live:
  `107100_228980_229002_...bin` = Bastion pulling a Steamworks-redist depot).
- The `.bin` payload is plain uncompressed protobuf
  (`SteamPrefill/Models/Manifest.cs`): field 2 = manifest id, 4 = depot id,
  repeated FileData→ChunkData{1: 40-hex chunk id, 2: compressed length}.
  Decoded with a ~25-line stdlib varint reader — no dependency needed.

### Cheap check mode

No `--dry-run` exists (verified against v3.7.1 `prefill --help`). But a
prefill **without `--force`** IS the check: measured ~3 s wall clock, zero
bytes, for an up-to-date app (real app.log evidence), vs. 17 s / 76 MiB when
stale. Outcome is machine-readable from the summary counters
(`Updated` / `Up To Date`; source: lancache-prefill-common
`PrefillSummaryResult.cs`). Unowned apps produce `Updated 0 / Up To Date 0`
with zero bytes — the precise rule for job-outcome honesty.
`clear-temp` can wipe the `.bin` cache at any time → archive ingested files.

### Alternative current-manifest sources (live-tested)

| Source | Verdict |
|---|---|
| `ISteamApps/UpToDateCheck/v1` | Unusable: works only for apps with dedicated-server versions (4 of 5 test apps failed); returns server version, not buildid |
| Official Web API for depot gids | Does not exist; PICS is the only official route |
| `api.steamcmd.net/v1/info/<appid>` (third-party) | Works; cross-validated EXACTLY against our local `.bin` manifests for four depots; exposes `depots.<id>.manifests.public.gid`, buildid, `depotfromapp`. Single-maintainer, no SLA → opt-in only, fail-soft |
| SteamCMD binary | ~250 MB runtime + brittle VDF-on-stdout — rejected (§9) |
| ValvePython PICS | Heavy dependency stack in a 4k-line service — rejected (§9) |

## Q2 — Garbage collection

### Cache-stored manifest format

`/depot/<id>/manifest/<manifestid>/5/<requestcode>` is a ZIP (single deflate
entry `z`) containing the sectioned Steam manifest
(`PAYLOAD 0x71F617D0`, `METADATA 0x1F4812BE`, `SIGNATURE`, `EOF`).
PAYLOAD = ContentManifestPayload protobuf; FileMapping.6 = chunks with
sha(20 bytes), offsets, cb_original, cb_compressed. Filenames are encrypted
(needs depot key) — chunk SHAs are NOT, and GC only needs SHAs.
Total parsing cost: stdlib zipfile + struct + varint reader ≈ 60 lines.

### Correctness proof (the decisive result)

Diffed every available manifest against on-disk chunk dirs (~12,000 files):

- Client-filled depots (cache-stored manifests): 1070561 (3594), 1391111
  (8174), 229006 (84), 4594150 (2), 481 (8) — **orphans 0, size mismatches 0**.
- Prefill-filled depots (`.bin` manifests): 229002, 242921 full match;
  107101 and 990081 partially cached (missing chunks ≠ orphans) —
  **orphans 0, size mismatches 0**.

Three facts established: chunk id IS the cache filename; `cb_compressed` IS
the on-disk byte size (exact reclaim reporting + free corruption check);
metadata `unique_chunks` self-checks the parser.

### Source asymmetry

SteamPrefill fetches manifests over HTTPS into its own cache; the real Steam
client fetches over HTTP into `/cache/depot/<id>/manifest/`. Neither source
covers all depots — GC accepts both (identical chunk sets, proven). Also
observed: the same manifest stored 3× under different request codes →
dedupe opportunity.

### Why time-based GC is wrong (measured)

1. `proxy_store` stamps stored files with upstream `Last-Modified` — mtimes
   are content publish times spread over months (live CDN header + on-disk
   evidence), fetch time does not exist on disk.
2. Current-and-cached chunks are HITs and never rewritten — a time-window GC
   deletes exactly what it must keep; fixing that needs access-log parsing,
   the anti-pattern this project exists to remove.

## Open risks (carry into the WPs)

1. Removing `--force` changes WP 1.4 behavior — preserve via per-app
   `needs_force` set by deletion, else deleted games never refill.
2. Summary-table parsing is brittle (Spectre box glyphs, SGR bleed);
   unparseable table ⇒ "unknown", never zero. Decode output as UTF-8.
3. Third-party oracle ships disabled; unaffiliated-with-Valve note required.
4. `/data/manifests` archive needs its own retention (current + N).
5. Reading SteamPrefill's temp dir couples to its internals — version is
   digest-pinned (3.7.1); add a startup assertion on the filename pattern.

## WP cut (adopted into the plan)

3.1 manifest parsers (pure, fixtures) · 3.2 schema v3 depot_manifests +
archive + ingestion · 3.3 summary parser + job-outcome honesty ·
3.4 needs_force + update job type · 3.5 cron window · 3.6 GC core
(plan_gc, no deletion) · 3.7 GC endpoint as job, dry-run default ·
3.8 optional stale oracle (gated).
