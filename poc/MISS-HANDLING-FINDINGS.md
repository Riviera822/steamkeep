# WP 0.5 — Miss-handling findings: synchronous store vs. transparent passthrough

Status: PRELIMINARY (this is Phase 0 evidence for one design decision, not the
final Phase-0 gate call — see "Preliminary recommendation" at the end. WP 0.7
makes the final Phase-0 call using this write-up plus WP 0.3's real-client
evidence).

This is the evidence for `docs/PROJECT_PLAN.md` §5's "Slow cache-miss
downloads" pain-point row and §7's "Miss-handling decision" checkbox: **on a
cache miss, is it better to (A) synchronously fetch-and-store (the WP 0.1
baseline, `poc/conf/nginx.conf`), or (B) pass through transparently and let an
async prefill fill the cache later (`poc/conf/nginx-passthrough.conf`, new in
this work package)?**

Produced by running `poc/test-misshandling.ps1` on 2026-08-04 against both
configs, from clean cache states, against the same known-good live test
object used throughout Phase 0: depot `70403`, chunk
`773d10050d99b2544665873ec2125b3bf273e8b2` (999,232 bytes). The suite was run
twice in a row (once to validate the script, once for the numbers recorded
here) — the correctness verdict was identical both times; the timing numbers
below are from the second run.

## Correctness results (hard PASS/FAIL) — all PASS

| # | Config | Scenario | Result |
|---|---|---|---|
| A1 | passthrough | Cold miss, full GET | Client received the correct full body (SHA256 matches ground truth). **Nothing stored on disk.** |
| A2 | passthrough | Pre-seeded cache entry (simulated prefill fill) | Served as a `HIT`, byte-identical, `upstream_status=-` (no upstream contact) — confirms a prefill-filled cache works correctly under the passthrough config, not just the store config |
| A3 | passthrough | Cold miss + `Range: bytes=0-1023` | Forwarded to upstream unmodified; client received whatever upstream returned (HTTP 200, full 999,232-byte body — this upstream edge ignores `Range` on a miss, consistent with `RANGE-FINDINGS.md`). **Nothing stored on disk**, regardless of the Range header or upstream's response |
| B1 | store (regression) | Cold miss, full GET | Stored on disk, SHA256 matches ground truth — WP 0.1 behavior unchanged |
| B2 | store (regression) | Warm cache, full GET | Served from disk (`HIT`, `upstream_status=-`), byte-identical — WP 0.1 behavior unchanged |

The full WP 0.2 Range-request regression matrix (suffix/mid/multi-range,
concurrency) is not re-run here — that is `test-range.ps1`'s job and is
already evidenced in `RANGE-FINDINGS.md`. B1/B2 only re-confirm the base
MISS→store→HIT behavior still holds after this suite starts/stops nginx
several times switching between configs.

**Headline correctness finding:** the passthrough config never writes to
disk on a miss — not on a plain GET, not on a Range request — while still
serving pre-populated (prefill-filled) objects as fast, correct `HIT`s. This
is exactly the property the prefill-first design in `docs/PROJECT_PLAN.md`
§5 depends on: the cache can be filled entirely out-of-band, and client
traffic on a miss never touches the disk.

## Measurement results (INFO) — N=5 iterations per scenario

Client-perceived latency and throughput, `curl.exe`'s own timers (not
PowerShell's), localhost client → PoC nginx → real Steam CDN edge
(`dist-fra1.discovery.steamserver.net`). Cache cleared before every cold-miss
iteration; nginx restarted when switching between configs.

| Scenario | Latency min | Latency median | Latency max | Throughput min | Throughput median | Throughput max |
|---|---|---|---|---|---|---|
| passthrough, cold miss | 253.48 ms | 298.51 ms | 473.50 ms | 2.01 MB/s | 3.19 MB/s | 3.76 MB/s |
| passthrough, warm HIT | 2.37 ms | 2.55 ms | 7.55 ms | 126.15 MB/s | 373.12 MB/s | 401.92 MB/s |
| store, cold miss | 269.69 ms | 316.02 ms | 334.76 ms | 2.85 MB/s | 3.02 MB/s | 3.53 MB/s |
| store, warm HIT | 2.40 ms | 2.52 ms | 2.66 ms | 357.85 MB/s | 378.90 MB/s | 396.73 MB/s |

### Derived numbers

- **Store-mode overhead on the miss path vs. passthrough (median latency):
  +17.50 ms, +5.9%.** For context, that's smaller than the run-to-run
  *variance* of the cold-miss scenario itself (both configs ranged over
  ~80–200 ms across 5 iterations, driven by the real network hop to Valve's
  CDN) — at this object size, the cost of `proxy_store` writing ~1 MB to a
  local SSD is noise compared to the WAN round-trip that dominates a cache
  miss either way.
- **HIT vs. MISS speedup factor:** passthrough ≈117x, store ≈126x (median
  latency ratio). Both configs show the same order-of-magnitude payoff for a
  cache hit — the *store-vs-passthrough* choice barely moves this number;
  what moves it is *whether the object is cached at all*.

## What these numbers say about the prefill-first hypothesis

For a single ~1 MB object measured this way, **synchronous store-on-miss is
not meaningfully slower than passthrough for the client on the critical
path** — the measured overhead (single-digit ms, ~6%) is dwarfed by normal
network variance. If this generalized to all Steam depot chunks, the
performance case for prefill-first (in the narrow sense of "storing is slow
for the client") would be weak on its own.

However, the plan's prefill-first rationale in §5 was never really about
per-object store overhead — it's about the *aggregate* behavior described
there: "nginx slice mechanics + CDN back-off behave poorly with the Steam
client" under many/large/parallel real-world downloads, and the idea that
"SteamPrefill downloads far faster than the Steam client" at bulk-game
scale. Nothing in this work package measures that claim directly. What this
work package *does* confirm is the mechanical prerequisite: passthrough mode
works correctly (A1–A3) and a prefill-filled cache is served identically
fast to a store-mode-filled one (A2 vs. B2, ~2.5 ms either way) — so
prefill-first is mechanically sound; whether it's *necessary* for
performance at Steam's real scale is not established by this data.

## What these numbers do NOT say

- **Single ~1 MB object, one data point.** Real Steam depot chunks vary in
  size; this PoC has only ever had one confirmed-good live test target
  (carried over from WP 0.1/0.2). A store-mode overhead that's noise at 1 MB
  could behave differently at very different chunk sizes — untested here.
- **Localhost client, single connection, no parallelism.** The real Steam
  client opens multiple parallel chunk-download workers per depot/game
  (`docs/PROJECT_PLAN.md` §9 flags this explicitly as untested). Concurrent
  cold misses writing to `proxy_store` under real client-like parallelism
  (beyond the 2-concurrent-download smoke test in WP 0.2's scenario E) is
  not measured here at all — this is likely where any real store-mode
  overhead (lock contention, disk I/O contention, temp-file rename
  serialization) would actually show up, not in a single sequential request.
- **One CDN edge, one network path.** All numbers are against
  `dist-fra1.discovery.steamserver.net` from one machine on one network.
  Different Steam CDN edges (geographically closer/farther, different
  backing infrastructure) could have very different baseline latency and
  variance, which would proportionally change how visible a fixed store
  overhead is.
- **No slice-mechanics or CDN back-off behavior exercised.** The plan's pain
  point explicitly calls out "nginx slice mechanics + CDN back-off behave
  poorly with the Steam client" as the underlying problem prefill-first is
  meant to solve — this PoC's nginx config uses neither the `slice` module
  nor any back-off/retry logic, so that specific failure mode is untested by
  either config here.
- **The real Steam client is still missing.** WP 0.3 (the real-client test
  kit, not yet executed by the user as of this writing) is where the missing
  evidence comes from: does the actual client's chunk-fetch pattern
  (parallel workers, retry/resume behavior, actual chunk size distribution)
  behave differently against store vs. passthrough than this curl-based
  measurement suggests? That evidence does not exist yet.

## The WP 0.2 follow-up, made explicit here

`RANGE-FINDINGS.md` already flagged this, and it bears repeating because the
two configs in this work package diverge on it by design:

- **Store mode (`nginx.conf`) needs a Range-strip + 200-only-store guard in
  production (Phase 1).** Today's tested upstream edge always ignores
  `Range` and returns a full `200`, so `proxy_store` has only ever seen
  complete bodies to store — but nothing in `nginx.conf` *enforces* that.
  If a different CDN edge/route ever does honor `Range` on a miss, store
  mode would silently write a truncated partial response under the object's
  full-object path and later serve it as if complete. The fix belongs in
  Phase 1: strip the client's `Range` header before proxying upstream on a
  miss, and only `proxy_store` when `$upstream_status = 200` (never store a
  `206`).
- **Passthrough mode (`nginx-passthrough.conf`) needs neither guard.**
  Nothing is ever written to disk on a miss, so there is no
  "truncated-file-masquerading-as-complete" failure mode to guard against.
  The client's `Range` header is forwarded to upstream unmodified and the
  client gets exactly what upstream would have given it directly — this is
  correct behavior, not a risk, precisely because passthrough mode never
  stores anything (see `nginx-passthrough.conf`'s own header comment for the
  full rationale).

This is a real, structural safety difference between the two designs, not
just a performance one: passthrough is unconditionally safe against the
Range/proxy_store risk from `docs/PROJECT_PLAN.md` §9; store mode is only
safe today because of upstream behavior this PoC does not control, and needs
an explicit guard to be safe in general.

## Preliminary recommendation for the Phase-0 gate

Based on this work package alone:

- **Performance does not force a choice.** At this object size, synchronous
  store-on-miss and transparent passthrough are latency-equivalent to the
  client (overhead within measurement noise). The plan's §5 rationale for
  prefill-first should be read as resting primarily on the *aggregate/scale*
  argument (many parallel chunk fetches, CDN back-off under Steam-client-like
  load) rather than per-object store cost — and that argument remains
  unmeasured.
- **Safety favors passthrough.** Passthrough mode has no Range/proxy_store
  corruption risk by construction; store mode's safety currently depends on
  an upstream behavior this project doesn't control and needs an explicit
  Phase-1 guard (Range-strip + 200-only-store) to be robust in general.
- **Mechanically, prefill-first is sound.** A5's requirement ("per-game
  download trigger... start → done status") and the whole vault-api
  prefill-orchestration design in §3/§4 depend on prefill being able to fill
  the cache out-of-band and have it served correctly afterward — A2 in this
  work package confirms that works, at full HIT speed, under the
  passthrough config.
- **This is not the final call.** WP 0.7 (per the plan's Phase 0 checklist)
  should make the final Plan A/B decision only after WP 0.3 (real Steam
  client, real chunk sizes, real parallel-fetch behavior) has run — the
  scale/parallelism argument that actually motivates prefill-first in §5 is
  exactly what this work package could not test.

## What this work package does NOT cover

- Concurrent/parallel miss traffic under either config (beyond WP 0.2's
  2-concurrent smoke test, not repeated here).
- Non-200/206 upstream responses (404/403/etc.) for either config.
- Any object other than the single ~999,232-byte known-good test chunk.
- The real Steam client, SteamPrefill, or DNS/hosts-file redirection
  (WP 0.3/0.4, out of scope here as in all prior Phase 0 packages).
- Docker/production hardening for either config — both remain PoC-only,
  Windows-native nginx configs per the project's Phase 0 charter.
