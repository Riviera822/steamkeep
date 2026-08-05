# vault-core (Phase 1, WP 1.1)

Production nginx config for the SteamVault cache core. Derives from the
Phase-0 PoC (`poc/conf/nginx.conf`, frozen as evidence) plus the four
binding production requirements discovered there and recorded in
[`docs/adr/0001-proxy-store-feasibility.md`](../docs/adr/0001-proxy-store-feasibility.md).

No Docker yet -- that's WP 1.9. This work package is the config itself,
runnable natively on Windows for development exactly like the PoC was.

## Files

```
core/
├── nginx/
│   └── nginx.conf                        # the config itself
├── tests/
│   ├── test-core.ps1                     # automated suite, runs against the real Steam CDN
│   └── fixtures/
│       └── retry-regression.conf         # local throwaway rig for the B1 retry regression (test 7)
└── README.md                              # this file
```

`core/cache/`, `core/logs/`, `core/tmp/`, `core/_testcore_tmp/` are created
at runtime (by `test-core.ps1`, idempotently) and gitignored -- see the
repo root `.gitignore`.

## ADR-0001 requirement -> config mapping

| # | Requirement | Where in `core/nginx/nginx.conf` |
|---|---|---|
| 1 | LanCache heartbeat contract | `location = /lancache-heartbeat` (bottom of the `server` block) |
| 2 | Strip client `Range`/`Accept-Encoding`/`If-Range` upstream + store only 200 (incl. retry lists) | `proxy_set_header Range/Accept-Encoding/If-Range ""` in `@miss`; `map $upstream_status $vault_store_path` (`200` or `"~, 200$"` -> real path, else empty) feeding `proxy_store $vault_store_path` |
| 3 | `?nocache=1` bypass | `map $arg_nocache $vault_try_target` (forces `try_files` onto a guaranteed-missing path) used by `location /depot/` |
| 4 | Client-Host upstream, resolver, timeouts, retry, abuse guard | `resolver 1.1.1.1 ipv6=off valid=30s`; `map $host $vault_host_allowed` + `map $host $vault_upstream_host`; `proxy_connect_timeout 3s`; `proxy_next_upstream ...`; the `if ($vault_host_allowed = 0) { return 403; }` guard in `@miss` |

(`Accept-Encoding`/`If-Range` stripping and the `"~, 200$"` retry-list
match were added in a review-fix pass after the initial WP 1.1 submission
-- see "Store guard" and the Accept-Encoding note below for why.)

Every requirement also has an inline comment at its implementation site in
`nginx.conf` explaining the *why*, not just the *what* -- this file is the
map/index, not a duplicate of that reasoning.

## Running it natively (development)

Same prefix trick as the PoC, and the **same nginx.exe binary** -- no
separate download/setup script exists under `core/` on purpose, to avoid
shipping a second copy of a 2.7 MB zip for a config that reuses the exact
same nginx build:

```powershell
poc\nginx\nginx.exe -p <repo>\core -c nginx\nginx.conf
```

All relative paths in `nginx.conf` (`root cache`, `logs/access.log`, the
temp paths) resolve against the `-p` prefix, so this creates/uses
`core/cache/`, `core/logs/` -- entirely separate from `poc/cache/`,
`poc/logs/`. `core/tests/test-core.ps1` does exactly this (see below).

**Port 80 contention:** only one nginx can listen on port 80 at a time.
The PoC's own nginx may already be running (a live Steam client can be
using it as its cache right now). `test-core.ps1` handles this automatically:
stops whatever is running, runs vault-core's config for the test window,
then stops it again and restarts the PoC's nginx via `poc/start.ps1` --
the machine is left exactly as it was found. `poc/` itself is never
modified.

## Running the test suite

```powershell
core\tests\test-core.ps1
```

Runs against the real Steam CDN (known-good test object: depot `70403`,
chunk `773d10050d99b2544665873ec2125b3bf273e8b2`, the same one used
throughout `poc/`). Exit code 0 = pass, 1 = fail. As of the review-fix pass,
all 9 test groups pass: health, heartbeat, smoke MISS/HIT, Range-strip
guard, nocache bypass, Host allowlist reject + pass-through, plus three
regressions added for the review findings -- B1 retry-list storage (own
local fixture, `core/tests/fixtures/retry-regression.conf`), S2
Accept-Encoding/gzip MISS+HIT byte-identity, and S3 `/tmp/proxy/...`
returning 404.

**Every `/depot/` request in the suite sends an explicit `Host` header**
naming a real Steam CDN hostname. This isn't incidental: it mirrors how
this server is actually used in production (a DNS- or hosts-file-redirected
Steam client always connects with a genuine `*.steamcontent.com` /
`*.steamserver.net` Host). `curl`/`Invoke-WebRequest`'s own default Host
(`127.0.0.1`) is exactly the kind of Host the allowlist guard (requirement
4) is designed to reject -- so it cannot be used for the suite's
positive-path tests, by design, not by oversight.

## Store guard: what nginx does on its own, and what the map actually adds

**Corrected in review (S1):** an earlier revision of this section presented
a 200/404/206/502 table as proof that the `$vault_store_path` map was
"guarding" storage. It wasn't testing the map's own contribution --
`proxy_store on;` with **no map at all** already skips storing 404/206/502
responses on this nginx build (confirmed by re-running that exact
comparison, unguarded, during the review-fix pass: with plain
`proxy_store on;` and a local throwaway upstream cycling through
200/301/302/404/206/502, only the 200 response landed on disk; the other
five were all correctly returned to the client but never written). That
table was validating **nginx's own built-in behavior**, not this config's
guard.

So what does the map in `nginx.conf` genuinely add, that bare
`proxy_store on;` does not give you for free?

```nginx
map $upstream_status $vault_store_path {
    200          "$document_root$uri";
    "~, 200$"    "$document_root$uri";
    default      "";
}
...
proxy_store $vault_store_path;
```

**1. The retry-list case (the real BLOCKER this map exists to fix).**
`$upstream_status` is not always a bare status code: when
`proxy_next_upstream` retries a failed attempt against a second backend,
nginx sets it to a **comma-separated list of every attempt**, e.g.
`"502, 200"` for a failed-then-succeeded retry. This is not a lab
curiosity -- real Phase-0 traffic produced exactly this
(`poc/logs/access.log` lines 3391-3392: `upstream_status=502, 200`,
21-36 second requests during a transient upstream outage). A map keyed on
the bare literal `200` does **not** match `"502, 200"` -- it falls to
`default ""`, silently discarding a completely valid, fully-received body
that the client already got a correct 200 for. This was reproduced in an
isolated two-backend rig (one always-502, one always-200, `upstream {}` +
`proxy_next_upstream`) during the review-fix pass: the buggy map (bare
`200` only) left the object uncached after a successful retry; the fixed
map (bare `200` plus `"~, 200$"`, matching "last attempt in the list was
200") stored it correctly. `core/tests/test-core.ps1` test 7 encodes this
exact regression against a checked-in fixture
(`core/tests/fixtures/retry-regression.conf`), not just the real CDN
(which cannot be made to fail on demand).

**2. Redirect bodies (301/302).** Own follow-up finding, not yet
independently reproduced against a real Steam CDN redirect (none was
observed in Phase 0 or this WP): a bare `proxy_store on;`, per the same
local test, does **not** store 301/302 bodies either on this nginx build --
so on *this specific build*, the map's 301/302 handling is currently
redundant with nginx's own behavior, same as the 404/206/502 case. This is
kept anyway (see point 3) rather than removed, since relying on an
unstated, unversioned nginx internal behavior for a security/correctness
property is fragile across nginx versions/platforms in a way an explicit
map in our own config is not.

**3. Explicitness and auditability regardless of platform behavior.**
Even where the map's `default ""` branch is redundant with nginx's own
unguarded behavior on this specific build, keeping it explicit means the
"only 200 (or a retry that ended in 200) gets stored" invariant is stated
in *our* config, not left to depend on nginx-internal behavior that could
plausibly differ across versions/platforms and was never in nginx's public
documentation as a status-code contract in the first place. The map is the
one part of this guarantee that's actually ours to keep correct -- which is
exactly what the B1 bug (point 1) was: an *our-own-code* bug, not an nginx
behavior surprise.

`core/tests/test-core.ps1`'s Range-strip guard test (cold object + a real
`Range: bytes=0-1023` header against the real Steam CDN) still asserts the
stored file is the complete object, but as noted before, the real CDN
(like nginx's own unguarded behavior) never actually answers with a 206
once Range is stripped -- so that test alone proves the *Range-strip*, not
the *store-path map*, is doing the work for that particular scenario. The
retry-rig regression (point 1) is what actually exercises the map's own
behavior end-to-end.

**What the client receives for a Range request on a cold object:** with
Range stripped upstream, the upstream always answers 200 (full body) --
and since nginx forwards a MISS response to the client exactly as received
from upstream (no local range-serving logic on the miss path), the client
*also* gets a 200 with the full body, not a 206, even though it asked for
`bytes=0-1023`. This is identical to what the PoC already observed (the
tested CDN edge ignores Range on a miss regardless), so no client-visible
regression -- but production no longer *relies on* that upstream behavior
being true forever; it's now enforced. Once an object is warm, nginx's
static-file serving handles Range (including partial/suffix/multi-range)
correctly and natively, unchanged from the PoC (`poc/RANGE-FINDINGS.md`
scenarios C/D1-D3).

## `?nocache=1` bypass: chosen semantics

`try_files` resolves against `$uri`, which has the query string already
stripped -- so left alone, a `?nocache=1` request for an already-cached
object would still HIT. `$vault_try_target` forces `try_files` onto a
path that can never exist on disk whenever `?nocache=1` is present, so it
always falls through to `@miss` (a real upstream fetch), regardless of
what's currently cached.

The fresh response is then written back to the object's normal canonical
path via the same 200-only guard above -- i.e. **a `nocache=1` request
refreshes the stored copy rather than deleting it**. This is what
`proxy_store` naturally does when writing to the same path again, and is
explicitly allowed ("acceptable") by ADR req 3. It also means a `nocache=1`
request against a URI whose upstream now returns a non-200 leaves the
previously-stored copy untouched (the store-path guard still applies) --
a bypass never destroys a known-good cached copy with a failed fetch.

## Accept-Encoding / If-Range stripped upstream too (S2 fix)

**Found in review:** without stripping `Accept-Encoding`, upstream can
answer a MISS with a gzip-compressed body (`Content-Encoding: gzip`).
`proxy_store` writes exactly the bytes it received -- compressed -- to
disk under the object's canonical path. A later HIT serves that same file
straight from disk via `try_files`, which has no idea the bytes were ever
compressed and does **not** re-add a `Content-Encoding` header. The client
then receives a raw gzip body with nothing in the HTTP response signaling
that it's compressed: silent corruption indistinguishable from a healthy
response until something tries to actually use the depot chunk.

**Fix:** `proxy_set_header Accept-Encoding "";` in `@miss` forces upstream
to always answer identity (uncompressed), so the stored bytes and the
served bytes always agree. `If-Range` is stripped alongside it for the
same reason `Range` already was: a conditional-range header only makes
sense paired with a `Range` request, and `Range` is already gone.
`core/tests/test-core.ps1` test 8 sends `Accept-Encoding: gzip` on both a
cold and a warm request against the real Steam CDN and asserts both
bodies are byte-identical to ground truth.

## Temp files must never be web-reachable (S3 fix)

**Found in review:** an earlier revision placed nginx's temp paths
(`client_body_temp_path`, `proxy_temp_path`, etc.) under `cache/tmp/...` --
inside `root cache`. Since nothing excluded `/tmp/...` as a request path,
`GET /tmp/proxy/<tempname>` resolved onto the same filesystem tree nginx
serves `/depot/...` from, and could serve an in-flight (or leftover)
`proxy_store` temp file's raw bytes to any client that guessed or observed
a temp filename. Reviewer reproduced this directly.

**Fix:** temp paths now live under `tmp/`, a *sibling* of `cache/` (both
directly under the `-p` prefix), so no request URI this server accepts
maps onto them at all -- moving the problem out of existence rather than
trying to block it after the fact. A second, independent layer
(`location ^~ /tmp/ { return 404; }`) is kept anyway as defense-in-depth,
in case a future edit ever reintroduces a temp dir under `cache/` again.
`core/tests/test-core.ps1` test 9 asserts `GET /tmp/proxy/whatever` returns
404.

**Same-filesystem requirement (binding for WP 1.9):** `tmp/` and `cache/`
must stay on the *same* filesystem/volume. `proxy_store` finishes a
download by `rename()`-ing the completed temp file into its final path
under `cache/depot/...` -- atomic and effectively instant only when both
paths share one filesystem. Across filesystems, `rename()` fails and nginx
falls back to a full copy (slower, and briefly doubles disk usage for
large depot chunks). In dev, both are plain relative paths under the same
`-p` prefix, so this holds automatically. **In the Docker image, `tmp/`
and `cache/` must be part of the same mounted volume** (the same bind
mount or the same named volume) -- never split across two separate volume
mounts, and never one of them left on the container's own (possibly
different-filesystem, e.g. overlay/tmpfs) root.

## Known gap: `analyze.ps1` cannot parse comma-list `upstream_status` lines (S4)

`poc/steam-client-test/analyze.ps1`'s log-line regex captures
`upstream_status` with `\S+` (`upstream_status=(?<ustatus>\S+)`) --
a single non-whitespace token. A retried request's log line reads
`upstream_status=502, 200 bytes_sent=...` (note the space after the
comma): `\S+` only matches `502,`, leaving a stray `` 200`` immediately
before `bytes_sent=`, which the anchored (`^...$`) pattern for the *entire*
line requires to appear right where `\S+` stopped. The whole line fails to
match and is **silently skipped** by the analyzer -- not misparsed with a
wrong value, just dropped as if it never happened.

This is a known, currently-unfixed gap, not something this work package
resolves: `analyze.ps1` belongs to `poc/` (frozen as Phase-0 evidence) and
is out of scope here. It matters going forward because **this gap gets
worse, not better, the more `proxy_next_upstream` actually helps** --
every request that needed a retry to succeed is exactly the kind of
request this analyzer will now silently drop from any future log-based
analysis (hit/miss ratios, bypass detection in Phase 3, etc.). Flagging
this explicitly so a future work package (whichever one next touches log
analysis -- likely bypass detection, requirement A12, Phase 3) fixes the
regex to tolerate a comma-separated list rather than rediscovering the gap
the hard way.

## Host-header allowlist: design and a real-world DNS surprise

The allowlist (`$vault_host_allowed`, requirement 4's abuse guard) is only
enforced inside `@miss` -- **not** on the whole `/depot/` location. An
already-cached object is served as a HIT regardless of the request's Host
header. This is deliberate: serving bytes that are already legitimately on
disk carries no open-proxy risk (the attacker learns nothing they couldn't
get by requesting the exact same path with a valid Host), whereas
`@miss` is the only place this server can be made to dial an
attacker-chosen destination -- that's where the guard has to live.

**Discrepancy found while writing this work package's tests:** ADR-0001
req 4 describes `lancache.steamcontent.com` as "unusable, e.g. ... which
has no public A record", requiring the fallback-edge mapping for it. Direct
`nslookup` against both the local resolver and `1.1.1.1` during this WP
(2026-08-05) shows it now resolves via a real CNAME chain
(`lancache.steamcontent.com` -> ... -> `dist-fra1.discovery.steamserver.net`,
the same edge this config falls back to). This appears to be another
"community-documented assumption superseded by fresh evidence" case, the
same pattern WP 0.6 already found for the Linux-client quirk. Practically,
this means the explicit fallback mapping in `nginx.conf` is currently
*redundant* for reachability (nginx's resolver would follow the CNAME to
the same result even without it) -- but it is kept as designed, because:
it is harmless, it matches the ADR's documented intent, and there is no
guarantee Valve keeps that CNAME indefinitely (historically it was
documented as absent). If it disappears again, this config already handles
it without a change.

## `proxy_next_upstream` caveat (honesty note)

`nginx.conf` sets `proxy_connect_timeout 3s` and
`proxy_next_upstream error timeout http_502 http_503 http_504` with a
bounded `proxy_next_upstream_tries 3` / `proxy_next_upstream_timeout 6s`.
This directly addresses the ~42s stall Phase 0 observed from a dead
upstream IP (`poc/linux-client-test` findings) for the *connect* phase.

What this work package did **not** independently re-verify: whether
`proxy_next_upstream` actually retries against a *second* IP address when
`resolver` returns multiple A records for the same hostname and
`proxy_pass` targets it via a variable (as here), rather than through a
real `upstream {}` server-group block. This is documented, longstanding
nginx behavior for resolver-backed dynamic `proxy_pass`, but doing a live
test would require a hostname with a genuinely-dead first IP and a working
second IP, which wasn't available to construct safely against the real
Steam CDN in this WP's timebox. The `proxy_connect_timeout 3s` bound alone
already prevents the specific 42s-stall failure mode observed in Phase 0
regardless of whether the multi-IP-retry path fires -- worst case without
it, a request still fails fast (bounded by `proxy_next_upstream_timeout`)
instead of hanging.

## Log rotation

`access_log logs/access.log vault;` and `error_log logs/error.log warn;`
are both plain files nginx keeps open for the lifetime of the worker
process -- nothing here rotates them automatically, same as any nginx
install.

**In the container (WP 1.9):** standard logrotate pattern, using nginx's
own graceful log-reopen via `SIGUSR1` (or `nginx -s reopen`) so no requests
are dropped mid-rotation:

```
# /etc/logrotate.d/steamvault-core (Docker image)
/cache/../logs/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    postrotate
        nginx -s reopen
    endscript
}
```

(Exact path depends on the volume layout finalized in WP 1.9 --
placeholder path shown; the mechanism, not the path, is the point here.)

**Windows-native (dev):** no logrotate equivalent is set up for local
development -- logs simply accumulate under `core/logs/`. Since this mode
is for development/testing only (never the deployed target), delete
`core/logs/access.log`/`error.log` manually if they grow inconveniently
large; they are gitignored and carry no state that needs to survive a
restart.

## What moves into the Docker image (WP 1.9)

- The nginx binary itself (Linux build, not the Windows one reused here)
- `core/nginx/nginx.conf` unchanged in content -- only the `-p` prefix
  argument changes (container WORKDIR instead of `core/`), so `root cache`
  resolves to the container's `/cache` volume mount and the path-faithful
  `/cache/depot/...` layout falls out with no config edits
- `access_log`/`error_log` paths likely redirected to stdout/stderr or a
  mounted log volume, per whatever the Compose/logging convention lands on
  in WP 1.9 -- not decided here
- The logrotate example above, wired into the image or the host, whichever
  the Compose design in WP 1.9 prefers
- **Binding requirement, not optional:** `tmp/` and `cache/` must be part
  of the *same* mounted volume -- see "Temp files must never be
  web-reachable" above for why (rename() atomicity for `proxy_store`)

## What this work package does NOT cover

- Docker/Dockerfile/Compose (WP 1.9)
- vault-api or any API code (WP 1.2+)
- Miss-triggered prefill completion (Phase 3, hybrid decision in ADR-0001)
- Manifest-based garbage collection (Phase 3)
- Per-client bypass detection (Phase 3, requirement A12)
- Changes to `poc/` (frozen as Phase-0 evidence; its nginx is only
  stopped/started by `test-core.ps1`, never its config or code)
