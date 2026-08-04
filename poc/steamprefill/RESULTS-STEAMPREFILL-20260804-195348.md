# SteamVault WP 0.4 - SteamPrefill verification

Generated: 2026-08-04 19:53:48
Log file: C:\claude-dev\SteamVault\poc\logs\access.log
Cache depot dir: C:\claude-dev\SteamVault\poc\steamprefill\..\cache\depot
Window: auto-detected newest burst (gap threshold 30s) - 23 burst(s) found in the log, analyzing the last one (1277 entries)
Lines: 4846 total in file, 4725 parsed, 121 skipped (unparseable), 1277 in analyzed window
Window time span: 2026-08-04 19:52:35 .. 2026-08-04 19:53:37

## 1. URI-scheme conformance (chunk / manifest / patch / other)

| Category | Count | % of total |
|---|---|---|
| chunk (/depot/<id>/chunk/<sha1>) | 1272 | 99.6% |
| manifest (/depot/<id>/manifest/...) | 0 | 0.0% |
| patch (/depot/<id>/patch/<from>/<to>) | 0 | 0.0% |
| other / non-conforming | 5 | 0.4% |
| **Total** | 1277 | 100.0% |

Non-conforming URIs observed (verbatim, most frequent first):

| Count | URI |
|---|---|
| 2 | /depot/3419431/chunk/4b44ab91bf30922773796ec042681b6bfa10e6fe?nocache=1 |
| 2 | /lancache-heartbeat |
| 1 | /depot/242921/chunk/944c4968a2768f4b82aabf48fc769733491b832c?nocache=1 |

## 2. Range usage BY STEAMPREFILL (Phase-0: WP 0.3's real Windows client used zero Range requests - does SteamPrefill?)

| Range kind | Count | % of total |
|---|---|---|
| none (full-body request) | 1274 | 99.8% |
| suffix (bytes=-N) | 0 | 0.0% |
| explicit (bytes=N-M) | 3 | 0.2% |
| multi-range (comma-separated) | 0 | 0.0% |
| other / malformed | 0 | 0.0% |
| **Any Range header used** | 3 | 0.2% |

## 3. Hit/miss split and bytes fetched

| | Count | Bytes |
|---|---|---|
| HIT (served from disk) | 272 | 26.92 MiB |
| MISS (fetched from upstream) | 1003 | 179.95 MiB |
| other (e.g. /health, cache status -) | 2 | - |
| **Hit ratio (HIT / (HIT+MISS))** | **21.3%** | |

## 4. Per-depot request/byte counts (from the log)

| Depot ID | Requests | chunk | manifest | patch | Bytes |
|---|---|---|---|---|---|
| 242921 | 174 | 174 | 0 | 0 | 103.70 MiB |
| 3419431 | 1098 | 1098 | 0 | 0 | 103.12 MiB |

## 5. Filesystem check (poc/cache/depot/) for each depot this run touched

| Depot ID | On disk? | chunk files | chunk bytes | manifest files | manifest bytes | patch files | patch bytes |
|---|---|---|---|---|---|---|---|
| 242921 | yes | 174 | 103.62 MiB | 0 | 0 B | 0 | 0 B |
| 3419431 | yes | 829 | 75.97 MiB | 0 | 0 B | 0 | 0 B |

## 6. Path-faithful layout cross-check (docs/PROJECT_PLAN.md section 4)

Checks: every file under a touched depot's `chunk/` subfolder must be named as a bare 40-hex-character SHA1 (no extension, no extra path segments), and nothing should be stored directly under a depot's own directory outside the `chunk/`/`manifest/`/`patch/` subfolders. (`manifest/` and `patch/` contents use Steam's own numeric manifest/request-code IDs, not SHA1 hashes, so they are counted above but not pattern-checked here.)

**PASS** - no layout violations found. Every chunk file matched the expected 40-hex-SHA1 naming verbatim, and no stray files/folders were found directly under any touched depot directory.

## 7. Out of scope for this script

- The real Windows Steam client (WP 0.3, see poc/steam-client-test/) - not re-exercised here.
- Whether SteamPrefill's own lancache-heartbeat detection succeeded is inferred only indirectly (via 'was there any depot traffic at all') - see PROTOCOL.md section 0 for the direct pre-flight check.

