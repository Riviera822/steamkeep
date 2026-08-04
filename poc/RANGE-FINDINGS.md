# WP 0.2 — Range-request findings

Status: PRELIMINARY (see "What's still open" at the end — WP 0.3 and WP 0.5
still need to run before this becomes final).

This is the evidence for the single most important open question in
`docs/PROJECT_PLAN.md` §7/§9: **does `proxy_store` fail on Range requests
against the Steam CDN, forcing a fallback to Plan A?**

Produced by running `poc/test-range.ps1` against the WP 0.1 PoC config
(`poc/conf/nginx.conf`, unmodified) on 2026-08-04, from a clean cache state,
against the known-good live test object: depot `70403`, chunk
`773d10050d99b2544665873ec2125b3bf273e8b2` (999,232 bytes, confirmed
reachable via `dist-fra1.discovery.steamserver.net`). The suite was run
several times in a row to check for flakiness (especially the concurrency
scenario) — results were identical across all runs.

## Headline finding

**The Steam CDN upstream does not honor `Range` requests on a cache miss —
it always returns the full object with `200 OK`, ignoring the `Range`
header entirely.** Because of that, `proxy_store` never sees a partial
response to store on a miss: the dangerous case in §9 ("proxy_store stores a
corrupt/truncated file as if it were the complete object") **did not occur**
in any of the runs, not because `proxy_store` handles it gracefully, but
because the upstream never produces the partial response that would trigger
it. Once an object is warm (already stored, fully, on disk), nginx's own
static-file serving handles `Range` (including suffix ranges, mid-file
ranges, and multi-range requests) correctly and natively — no `slice`
module needed for that path.

## Results table

| # | Scenario | Request sent | Response status | What got stored | Integrity verdict |
|---|---|---|---|---|---|
| A | Baseline: upstream Range support, cold object, through proxy | `GET /depot/70403/chunk/773d…` `Range: bytes=0-1023` | **200** (no `Content-Range`, `Content-Length: 999232`) | — (see B) | Upstream **ignores** `Range` on miss |
| B | Cold cache + Range request — storage side effect | same request as A | 200 (full body) | Full object stored, 999,232 bytes, SHA256 `C78FB9F8…` — matches ground truth | **Correct** — no corruption, because upstream sent the full body |
| B (follow-up) | Plain GET right after B, against the resulting cache | `GET /depot/70403/chunk/773d…` (no Range) | 200 | served from disk (`cache=HIT`) | **Correct**, byte-identical |
| C | Warm cache + Range request | `Range: bytes=0-1023` on a fully-cached object | **206**, `Content-Range: bytes 0-1023/999232` | n/a (read-only static serving) | **Correct**, byte-exact vs. ground truth |
| D1 | Warm cache, suffix range | `Range: bytes=-500` | **206**, `Content-Range: bytes 998732-999231/999232` | n/a | **Correct**, byte-exact |
| D2 | Warm cache, mid-file range | `Range: bytes=499616-500615` | **206**, `Content-Range: bytes 499616-500615/999232` | n/a | **Correct**, byte-exact |
| D3 | Warm cache, multi-range | `Range: bytes=0-99,200-299` | **206**, `Content-Type: multipart/byteranges; boundary=…`, 408 bytes | n/a | Recorded as-is (multipart response — acceptable per spec) |
| E | Cold cache + 2 concurrent full downloads | 2× parallel `GET` (no Range), same object, cache cleared first | both **200**, both byte-identical to ground truth | Final stored file: 999,232 bytes, SHA256 matches ground truth | **Correct** across 3 repeated runs, no torn/corrupt writes observed |
| F | Final cache integrity | — | — | Stored file at end of full suite | **Byte-identical** to upstream original (SHA256 `C78FB9F8A88318DD61F318BB95F0B59911C9BBBF8678F6EF2D2724CDBC56A66C`) |

## Raw log excerpts (evidence)

From `poc/logs/access.log`, one full run (log format documented in
`poc/README.md`):

```
# Ground truth full fetch (cold, no Range)
04/Aug/2026:16:41:05 +0200 uri="/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2" status=200 range="-" upstream_status=200 bytes_sent=999703 request_time=0.423 cache=MISS

# Scenario A/B: cold cache + Range: bytes=0-1023 -> upstream still answers 200, full body
04/Aug/2026:16:41:05 +0200 uri="/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2" status=200 range="bytes=0-1023" upstream_status=200 bytes_sent=999703 request_time=0.234 cache=MISS

# Scenario B follow-up: plain GET now HITs the (correctly, fully) stored object
04/Aug/2026:16:41:05 +0200 uri="/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2" status=200 range="-" upstream_status=- bytes_sent=999490 request_time=0.002 cache=HIT

# Scenario C: warm cache + Range -> served as a real 206 straight from disk, no upstream contact
04/Aug/2026:16:41:06 +0200 uri="/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2" status=206 range="bytes=0-1023" upstream_status=- bytes_sent=1307 request_time=0.000 cache=HIT

# Scenario D1: suffix range
04/Aug/2026:16:41:06 +0200 uri="/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2" status=206 range="bytes=-500" upstream_status=- bytes_sent=789 request_time=0.000 cache=HIT

# Scenario D2: mid-file range
04/Aug/2026:16:41:06 +0200 uri="/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2" status=206 range="bytes=499616-500615" upstream_status=- bytes_sent=1290 request_time=0.000 cache=HIT

# Scenario D3: multi-range -> multipart/byteranges
04/Aug/2026:16:41:06 +0200 uri="/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2" status=206 range="bytes=0-99,200-299" upstream_status=- bytes_sent=672 request_time=0.000 cache=HIT

# Scenario E: 2 concurrent full downloads, both MISS, both fetched fresh from upstream
04/Aug/2026:16:41:06 +0200 uri="/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2" status=200 range="-" upstream_status=200 bytes_sent=999703 request_time=0.272 cache=MISS
04/Aug/2026:16:41:06 +0200 uri="/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2" status=200 range="-" upstream_status=200 bytes_sent=999703 request_time=0.297 cache=MISS
```

Console output for scenario D3 (multi-range detail):

```
[INFO] status: 206, Content-Type: multipart/byteranges; boundary=00000000011, Content-Length: 408, body size on disk: 408
[FINDING] multi-range request answered as HTTP 206 multipart/byteranges; boundary=00000000011, body 408 bytes
```

## Conclusions

**1. Is `proxy_store`'s completeness constraint a real problem for the Steam use case?**

Not for the object we tested — but the reason is subtle and worth stating
precisely, because it changes what "the fix" would even mean if this ever
breaks: it is not that `proxy_store` gracefully handles partial upstream
responses, it's that **the Steam CDN edge we're hitting
(`dist-fra1.discovery.steamserver.net`) doesn't return partial responses at
all** — it answers every request with the full object regardless of the
`Range` header sent. `proxy_pass` forwards the client's `Range` header
upstream by default (nothing in `poc/conf/nginx.conf` suppresses it), so if
a different Steam CDN edge, a different depot/chunk, or Valve's CDN
configuration ever *does* start honoring `Range` on these URLs, the
dangerous case from §9 (a truncated file silently stored and served as if
complete) becomes live again — this PoC did not exercise that path because
it could not: the upstream would not cooperate. This should be treated as
"not proven safe in general", not as "proven safe".

**2. What did we actually observe the Windows Steam-CDN objects/chunk sizes to be?**

Only one object was available for testing in this work package (the known
good chunk carried over from WP 0.1): depot `70403`, chunk
`773d10050d99b2544665873ec2125b3bf273e8b2`, exactly 999,232 bytes. That's a
single data point, not a distribution — WP 0.3 (real Steam client, real
game) is where we'll see the actual range of chunk sizes Steam uses in
practice. If Valve's typical chunk size is small enough that clients rarely
issue mid-transfer range requests at all (chunks are usually fetched whole),
that would reduce real-world exposure to this risk regardless of what the
CDN's Range support looks like.

**3. Preliminary recommendation**

**Plan B viable as-is for the miss path** (cold cache + Range → this specific
upstream returns full 200 responses, so `proxy_store` always gets a
complete body to store; no corruption observed across repeated runs).
**Plan B fully viable for the hit path** (warm cache + Range, including
suffix/mid/multi-range, is handled correctly and natively by nginx's static
file serving — this required no `slice` module and no config changes).
Concurrent cold-cache downloads of the same object also produced a correct,
non-corrupt stored file across 3 repeated runs — no torn-write corruption
observed, though this was only tested with 2 concurrent requests on a single
~1 MB object, not under load.

This verdict is marked **PRELIMINARY** because:
- It rests on one upstream edge server's behavior for one object. Steam's
  CDN is a large, heterogeneous, third-party system; other edges (different
  CDN partners, different `steampipe` backends) could behave differently.
- The real Steam client was not used here (WP 0.3) — we don't yet know
  whether the actual client ever sends `Range` requests against depot chunk
  URLs in practice, or under what conditions (resumed downloads, seeking
  during streaming install, parallel chunk workers).
- Cache-miss handling performance/scale (WP 0.5) is unmeasured — this
  package only proves correctness, not that synchronous store-on-miss is
  fast enough to be the primary miss-handling strategy.

## What this work package does NOT cover

- Non-200/206 upstream responses (404/403/416 Range Not Satisfiable, etc.)
  were not exercised.
- Only one depot/chunk object was tested (constrained by having only one
  confirmed-good live test target from WP 0.1).
- No load/scale testing of concurrent access beyond 2 simultaneous
  requests.
- The real Steam client, SteamPrefill, and DNS/hosts-file redirection are
  explicitly out of scope here (WP 0.3, WP 0.4).
