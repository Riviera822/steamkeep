# SteamVault — Phase 0 PoC (Work Package 0.1)

Throwaway feasibility code for `docs/PROJECT_PLAN.md` section 7 ("Phase 0 —
Feasibility PoC"). **Nothing under `poc/` is production code.** It exists to
answer one question before any real implementation starts:

> Can nginx's `proxy_store` cache Steam CDN depot chunks **path-faithfully**
> on disk (so a game = a folder, and deleting a game = `rm -rf` that folder),
> and serve repeat requests straight from disk without hitting the upstream
> again?

This package (0.1) answers that question for plain (non-Range) GET requests,
running nginx natively on Windows — no Docker (deliberate: containerization
happens at the end of Phase 1, per the project's working agreement).

Range-request behavior is explicitly **out of scope here** — see WP 0.2.
Testing against the real Steam client is explicitly **out of scope here** —
see WP 0.3. SteamPrefill is explicitly **out of scope here** — see WP 0.4.

## What's in this folder

| Path | Purpose |
|---|---|
| `setup.ps1` | Downloads + extracts official nginx-for-Windows into `nginx/`. Idempotent. |
| `start.ps1` / `stop.ps1` | Start/stop nginx with `conf/nginx.conf`. |
| `conf/nginx.conf` | The purpose-built PoC config (see below). |
| `test-smoke.ps1` | Automated proof: MISS then HIT against a real Steam CDN chunk. |
| `nginx/` | Extracted nginx binary (gitignored, recreated by `setup.ps1`). |
| `cache/` | The cache store, `cache/depot/<id>/...` (gitignored). |
| `logs/` | nginx access/error log + pid (gitignored). |

## Setup

```powershell
cd poc
.\setup.ps1
```

Downloads nginx 1.30.4 (stable) from nginx.org (~1.7 MB) into `poc/nginx/`
and creates `poc/cache/` and `poc/logs/` (plus the temp-path subdirectories
nginx needs at startup). Safe to re-run — skips the download if
`poc/nginx/nginx.exe` already exists.

## Start / stop

```powershell
.\start.ps1   # listens on http://127.0.0.1:80
.\stop.ps1    # graceful `nginx -s stop`
```

`start.ps1` launches `nginx.exe -p <poc> -c conf/nginx.conf`; all relative
paths inside `nginx.conf` (root, logs, temp dirs) resolve against that `-p`
prefix, so the config doesn't hardcode this machine's checkout path.

Manual check:

```powershell
curl.exe -i http://127.0.0.1/health
```

## Test

```powershell
.\test-smoke.ps1
```

Starts nginx if needed, clears any previously cached copy of the test
object, fetches a real Steam CDN depot chunk twice through the proxy, and
asserts:

1. First request → HTTP 200, and the file appears under
   `poc/cache/depot/<depotid>/chunk/<hash>`.
2. Second request → served without contacting upstream, verified via the
   access log's `cache=HIT` / `upstream_status=-` marker (vs. `cache=MISS`
   / `upstream_status=200` on the first request).
3. Both response bodies are byte-identical (SHA256 compared).

Exit code `0` = PASS, `1` = FAIL, with a itemized `[ OK ]` / `[FAIL]` list
either way.

The default test target is depot `70403`, chunk
`773d10050d99b2544665873ec2125b3bf273e8b2` (a 999,232-byte chunk, confirmed
reachable and served correctly by the real Steam CDN on 2026-08-04 while
building this PoC). If that chunk ever gets garbage-collected upstream,
override it:

```powershell
.\test-smoke.ps1 -DepotId <id> -ChunkHash <sha1>
```

## Result: the central assumption holds (for non-Range GET)

- `proxy_store on;` combined with `root cache;` stores the response at
  exactly the same path a static file at that URI would have (nginx derives
  the store path from `root`/`alias` + URI) — **no manual path-rewriting
  needed**, and nginx **auto-creates the full nested directory tree**
  (`cache/depot/70403/chunk/`) on first write. This was verified empirically,
  not just from documentation (see "Windows path notes" below).
- A `try_files $uri @miss;` + named-location fallback makes hit vs. miss
  trivially observable and cheap: a HIT is a plain static-file response (no
  proxy involved at all), a MISS goes through `proxy_pass` + `proxy_store`.
- Confirmed via manual curl runs and `test-smoke.ps1`: first request ~0.29s
  (real network fetch, 999,232 bytes), second request ~0.003s (disk read),
  identical bytes.

## Upstream choice and the loop-risk it avoids

**The pitfall:** WP 0.3 (real Steam client test) will add a Windows hosts
entry `lancache.steamcontent.com -> 127.0.0.1` so the Steam client discovers
this machine as its cache. But nginx, running on the *same* machine, would
also be subject to that hosts entry if it ever tried to resolve
`lancache.steamcontent.com` (or reused that name anywhere) — creating a
resolution loop where the cache tries to fetch from itself.

**The fix, in `conf/nginx.conf`:**

1. The upstream in `proxy_pass` is **`dist-fra1.discovery.steamserver.net`**,
   not `lancache.steamcontent.com` or anything in the `*.steamcontent.com`
   family. This hostname is a real Valve CDN edge — confirmed during this
   PoC's build via `nslookup lancache.steamcontent.com`, which currently
   resolves via CNAME chain through
   `origin-tier2.steampipe.steamcontent.com` →
   `steampipe-origin-tier2.steamcontent.com` →
   `cache-origin.steampipe.steamcontent.akadns.net` →
   **`dist-fra1.discovery.steamserver.net`** (A/AAAA:
   `162.254.197.9`, `162.254.197.25`, ...). Fetching directly from that final
   hostname returned identical content (verified byte-for-byte against the
   `lancache.steamcontent.com` name resolved via `--resolve` to the same IP).
   It is outside the `steamcontent.com` zone, so nothing WP 0.3's hosts-file
   change touches will ever shadow it.
2. An explicit **`resolver 1.1.1.1 ipv6=off valid=300s;`** directive makes
   nginx resolve that upstream itself, over the network, instead of
   depending on the OS resolver (which is what consults the hosts file) at
   all. This is defense in depth: even if a future work package added a
   hosts entry for this exact upstream name, nginx's own resolver would
   still bypass it, because `proxy_pass` here uses a variable
   (`http://dist-fra1.discovery.steamserver.net$request_uri`) — variables in
   `proxy_pass` force runtime resolution via the configured `resolver`
   instead of the resolution nginx does once at config-load time for
   literal hostnames.

If `dist-fra1.discovery.steamserver.net` ever stops resolving/serving
(Valve's CDN topology can change), re-run
`nslookup lancache.steamcontent.com` to find the current
`*.discovery.steamserver.net` target and swap it in — just keep it outside
the `steamcontent.com` zone.

## Windows path notes (verified, not assumed)

- Forward slashes work fine in `nginx.conf` on Windows for `root`,
  `access_log`, `proxy_temp_path`, etc. — confirmed by running `nginx -t`
  and the actual server.
- Relative paths (e.g. `root cache;`, `access_log logs/access.log;`) resolve
  against the `-p` prefix passed on the command line, **not** against the
  directory containing `nginx.conf`. That's why `start.ps1`/`stop.ps1` both
  pass `-p <poc>` explicitly.
- nginx initializes several temp-path directories at startup regardless of
  whether the corresponding module is used in the config
  (`client_body_temp_path`, `proxy_temp_path`, `fastcgi_temp_path`,
  `uwsgi_temp_path`, `scgi_temp_path`) — if any of their target directories
  don't exist, `nginx -t` fails with `CreateDirectory() ... failed (3: The
  system cannot find the path specified)`. All five are pointed at
  subdirectories under `cache/tmp/` and pre-created by `setup.ps1`. This was
  discovered empirically while bringing this PoC up (not documented clearly
  by nginx itself) — worth remembering for the Phase 1 production config.
- `proxy_store`'s directory auto-creation (see above) meant no equivalent
  pre-creation was needed for `cache/depot/...` itself — nginx creates that
  tree on demand, per depot ID, as new chunks are stored.

## Access log format

```
$time_local uri="$request_uri" status=$status range="$http_range"
upstream_status=$vault_upstream_status bytes_sent=$bytes_sent
request_time=$request_time cache=$vault_cache_status
```

`$vault_cache_status` is `HIT` or `MISS` (set directly in the relevant
`location` blocks — not inferred after the fact). `$vault_upstream_status`
is `$upstream_status` mapped to `-` when empty (i.e., on local hits, where
no upstream was ever contacted). Logged to `poc/logs/access.log`.

## What this PoC does NOT cover

- **Range requests** (`Range:` header handling, partial-content behavior,
  whether `proxy_store` can even represent a partial response correctly) —
  this is WP 0.2. The known risk from `docs/PROJECT_PLAN.md` §9
  (`proxy_store` incompatible with range requests) is **not yet resolved**;
  this package only proves the happy path (full-body GET).
- **The real Steam client** — this PoC was tested with `curl.exe`/
  `Invoke-WebRequest` directly against depot chunk URLs, not by running
  Steam with a hosts-file redirect and downloading a real game. That's
  WP 0.3 (also where the loop-risk mitigation above gets exercised for
  real).
- **SteamPrefill** — not installed or run against this cache. That's WP 0.4.
- **Non-200 upstream responses** (404/403/etc.) — `proxy_store` behavior on
  error responses was not exercised or asserted here.
- **Docker/production hardening** — no log rotation, no healthcheck
  container, no multi-worker tuning. Deliberately out of scope per the
  project's Phase 0 charter ("no production code before Phase 0").
