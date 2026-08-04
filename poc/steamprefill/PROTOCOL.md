# SteamVault Phase 0 PoC — WP 0.4: SteamPrefill test protocol

This is the step-by-step protocol for testing whether **SteamPrefill**
(`tpill90/steam-lancache-prefill` on GitHub) fills the WP 0.1/0.2 PoC cache
(`poc/conf/nginx.conf`) correctly and path-faithfully — `docs/PROJECT_PLAN.md`
§7: "Run SteamPrefill against the PoC cache — does it fill correctly?"

Read the whole document before running anything. **Section 0 explains a
cache-detection contract discovered while building this kit and confirms
it is now satisfied — read it first for context.**

---

## 0. SteamPrefill's cache-detection contract (RESOLVED — heartbeat endpoint now implemented)

While building this kit, the SteamPrefill binary's own source
(`LancacheIpResolver.cs` in `tpill90/lancache-prefill-common`, the
`LancachePrefill.Common` submodule of the main repo) was read to confirm
*how* it discovers a cache, because it turns out to be a **different
mechanism than the Windows Steam client's** (the one WP 0.3 exercised).
This section explains the contract and **confirms it is now satisfied**
by `poc/conf/nginx.conf` and `poc/conf/nginx-passthrough.conf`.

### How SteamPrefill actually detects a cache

`LancacheIpResolver.ResolveLancacheIpAsync` is called with
`AppConfig.SteamTriggerDomain`, which is hardcoded to the same hostname WP
0.3 used — `"lancache.steamcontent.com"` — confirmed in
`SteamPrefill/Settings/AppConfig.cs`. So far this matches the Windows
client's mechanism and the existing hosts entry.

**But the detection check itself is different and stricter.** After
resolving candidate hostnames (`lancache.steamcontent.com`, `localhost`,
the Docker gateway `172.17.0.1`, then the local machine's own hostname —
in that order) to an RFC1918-or-loopback IPv4 address, it does **not**
stop there. For each candidate private IP, it sends:

```
GET http://<ip>/lancache-heartbeat
```

and only accepts the candidate as a real cache if the response carries an
`X-LanCache-Processed-By` header (see `DetectLancacheServerAsync` in
`LancacheIpResolver.cs`). This is the real LanCache project's own
heartbeat contract — SteamPrefill assumes it's talking to an actual
LanCache instance, not a bare nginx `proxy_store` config.

### This PoC's nginx configs now implement that contract

This was initially a gap (`poc/conf/nginx.conf` was frozen after WP 0.1
and had no such endpoint), documented here as a blocking finding. The
freeze has since been lifted and both configs now carry the minimal
heartbeat location:

```nginx
location = /lancache-heartbeat {
    add_header X-LanCache-Processed-By "steamvault-poc";
    return 200;
}
```

Present in both `poc/conf/nginx.conf` and `poc/conf/nginx-passthrough.conf`
(same snippet, same comment marking it as a **required** contract for the
eventual production vault-core config, not just this PoC). **Confirmed
empirically** against the running PoC instance after a config reload:

```powershell
curl.exe -i http://127.0.0.1/lancache-heartbeat
```

now returns `HTTP/1.1 200 OK` with `X-LanCache-Processed-By:
steamvault-poc` present in the response headers. `poc/test-smoke.ps1` also
asserts this automatically as part of its regular regression run.

**Run the curl command above before doing anything else in this
protocol anyway** — it needs no Steam login, takes a few seconds, and
confirms your local instance is in the expected state before you spend
time on an interactive login.

### What this means in practice

- **`select-apps` was never affected** — it only lists owned apps via the
  Steam network and does not touch the cache-detection path at all.
- **`prefill` should now proceed past cache detection** and attempt real
  depot-chunk downloads through `poc/conf/nginx.conf`, instead of
  aborting with `LancacheNotFoundException` before making any request.
  This is the expected behavior now — if you still see that exception,
  re-run the pre-flight curl check above first; if it fails, something
  changed (nginx not running/reloaded with the current config, wrong
  port, etc.) rather than the original gap re-appearing silently.
- **This was still useful Phase 0 evidence.** The exercise surfaced a real
  compatibility requirement `docs/PROJECT_PLAN.md` didn't previously call
  out explicitly: **vault-core must implement the LanCache heartbeat
  contract (`/lancache-heartbeat` + `X-LanCache-Processed-By`) for
  SteamPrefill (or any other LanCache-aware client/tool) to auto-detect it
  as a cache** — not just the Steam-client-facing `proxy_store` behavior
  WP 0.1-0.3 already proved. Both PoC configs now carry a one-line comment
  flagging this as required for the production config, so it isn't lost
  when `poc/conf/nginx.conf` is superseded in Phase 1.

Either way, **do the pre-flight curl check first** so you know which
outcome to expect before you spend time on an interactive login.

---

## 1. Setup (already done for this checkout)

```powershell
cd poc/steamprefill
.\setup.ps1
```

Downloads the latest `tpill90/steam-lancache-prefill` Windows x64 release
(queried via the GitHub API) into `poc/steamprefill/bin/` (gitignored).
Idempotent — skips the download if `bin/SteamPrefill.exe` already exists
(`-Force` to re-download). This was already run once while building this
kit: **v3.7.1**, binary confirmed present and `--version` runs cleanly.

> Note: the actual repository is `tpill90/steam-lancache-prefill` — there
> is no repository literally named `steam-lan-prefill`. The CLI binary
> and the project are both commonly referred to as "SteamPrefill".

---

## 2. Preconditions

1. **nginx PoC must be running** (store config, `poc/conf/nginx.conf`):
   ```powershell
   cd poc
   .\start.ps1
   curl.exe -i http://127.0.0.1/health
   ```
   Expect `HTTP/1.1 200 OK` / body `ok`. If this fails, fix nginx first —
   nothing below produces useful evidence otherwise.
2. **Run the pre-flight heartbeat check from section 0**:
   ```powershell
   curl.exe -i http://127.0.0.1/lancache-heartbeat
   ```
   Expect `HTTP/1.1 200 OK` with an `X-LanCache-Processed-By` header. If
   you don't see that header, nginx is either not running the current
   `poc/conf/nginx.conf`/`nginx-passthrough.conf` (reload it) or something
   else changed — see section 0.
3. **Hosts entry**: `127.0.0.1 lancache.steamcontent.com` must be active
   (it already is, per WP 0.3 — do not remove it, other test kits and any
   later re-run of WP 0.3 depend on it too). Confirm:
   ```powershell
   Resolve-DnsName lancache.steamcontent.com
   ```
   should resolve to `127.0.0.1`.

---

## 3. Interactive part (the user does this — never share credentials with scripts/files)

All of this happens in your own terminal, directly with SteamPrefill's own
prompts. **Nothing in this repo ever asks for, stores, transmits, or logs
your Steam password.**

### 3.1 First-run login

```powershell
cd poc/steamprefill/bin
.\SteamPrefill.exe select-apps
```

The first time any SteamPrefill command needs your Steam account, it
prompts interactively (username, password, and — if you have Steam Guard
enabled — a mobile/email confirmation code) directly in the console.
Type your credentials there, not anywhere else.

**Where SteamPrefill stores its own session data**: under
`poc/steamprefill/bin/Config/` (created automatically on first run, next
to the executable — this directory is already present, empty, from the
`setup.ps1` extraction). This holds SteamPrefill's own account/session
state (e.g. a persisted login session so you aren't prompted every run,
and your selected-apps list) — **not raw credentials**, but still
account-linked local state you may not want committed. It's covered by
this kit's `.gitignore` entry (`poc/steamprefill/bin/` is gitignored
wholesale, see the repo-root `.gitignore`), so it will never be committed
by accident.

### 3.2 Select ONE small app

`select-apps` opens an interactive, searchable list of everything your
account owns (see `LancachePrefill.Common.SelectAppsTui` — there is no
non-interactive "select this one appid" CLI flag; selection is only done
through this TUI). Search for and select **exactly one small app**:

- **Spacewar (AppID 480)** if it's selectable for your account — Valve's
  own hidden Steamworks test app, free, only tens of MB. It doesn't
  appear in a normal Store search, but does show up in this owned-apps
  list if your account has it (most accounts with any Steamworks history
  do; if you don't see it, that's fine, use the fallback below).
- Otherwise: the smallest app you own — sort/search in the TUI, space to
  toggle selection, enter to confirm.

Confirm the selection took:

```powershell
.\SteamPrefill.exe select-apps status
```

This lists your currently-selected app(s) and their download size(s) —
confirm it shows exactly the one app you picked, and that its size is
small (a few hundred MB at most, ideally much less).

### 3.3 Run the prefill

```powershell
.\SteamPrefill.exe prefill
```

This is the actual cache-filling attempt this work package exists to
evaluate. Per section 0, cache detection should now succeed (the
heartbeat endpoint is implemented) — expect it to proceed to actual depot
chunk downloads through `poc/conf/nginx.conf` rather than aborting with
`LancacheNotFoundException`. If you do see that exception, re-run the
section 0 pre-flight curl check first before assuming this run is broken.

### 3.4 Second run

SteamPrefill's default behavior is to **skip apps that are already up to
date** — so a
second plain `prefill` would make zero requests and tell you nothing new
about cache hits. Force it to re-fetch the same chunks so `verify.ps1` can
observe a warm-cache HIT run:

```powershell
.\SteamPrefill.exe prefill --force
```

`-f`/`--force` "forces the prefill to always run, overrides the default
behavior of only prefilling if a newer version is available" (from
`prefill --help`). This re-requests the same depot chunks — which
`poc/conf/nginx.conf`'s `try_files` should now serve as HITs straight from
disk, exactly like the second (post-uninstall) run in WP 0.3.

### What to capture

Nothing manual beyond noting roughly when you ran 3.3 (and 3.4, if
applicable) — `verify.ps1` (section 4) mines `poc/logs/access.log` for
everything else (URI conformance, hit/miss split, Range usage, bytes) the
same way WP 0.3's `analyze.ps1` did, plus a filesystem check of what
actually landed under `poc/cache/depot/`.

---

## 4. Analyze

From `poc/steamprefill/`:

```powershell
.\verify.ps1
```

Auto-detects the newest contiguous burst of `/depot/` traffic in
`poc/logs/access.log` (or pass `-From`/`-To` explicitly, same convention
as WP 0.3's `analyze.ps1`) and reports:

**Caution:** the auto-detected window is simply the *newest* burst in the
log — if you (or anyone else) ran `test-smoke.ps1`, `test-range.ps1`, or
`test-misshandling.ps1` against this same nginx instance *after* your
`prefill` run, that later synthetic traffic becomes the newest burst and
gets analyzed instead of SteamPrefill's. Pass `-From`/`-To` explicitly
(bracketing your noted `prefill` start/end times) whenever other test
scripts might have run afterward, to avoid misattributing their traffic.

1. URI conformance and Range usage **specifically for the traffic in that
   window** (new evidence vs. WP 0.3: does SteamPrefill use Range
   requests where the real Windows client used none at all?).
2. Hit/miss split and bytes fetched.
3. A filesystem check of `poc/cache/depot/` — new depot directories, chunk
   file counts/sizes per depot.
4. A cross-check of on-disk filenames against the path-faithful layout
   `docs/PROJECT_PLAN.md` §4 describes (`depot/<id>/chunk/<40-hex-sha1>`)
   — any file that doesn't match verbatim is listed explicitly.
5. If zero SteamPrefill-attributable traffic is found at all (e.g. if
   cache detection somehow still failed — see section 0's pre-flight
   check), a clear statement of that fact instead of a misleading
   "0 requests, 0 issues" report.

Writes `RESULTS-STEAMPREFILL-<timestamp>.md` next to itself, same
convention as WP 0.3.

---

## 5. Rollback

Nothing in this protocol changes system state (no hosts-file edits, no
service installs) — the only persistent artifacts are inside
`poc/steamprefill/bin/Config/` (SteamPrefill's own session/selected-apps
state) and whatever landed under `poc/cache/depot/`. Neither needs manual
cleanup for other work packages to keep working; delete
`poc/steamprefill/bin/` entirely (it's gitignored and fully recreated by
`setup.ps1`) if you want a completely clean slate for a re-run.

Leave the `127.0.0.1 lancache.steamcontent.com` hosts entry and nginx
running/stopped as you see fit for other work packages — this protocol
doesn't require removing either.

---

## 6. Troubleshooting

**`LancacheNotFoundException: Unable to detect Lancache server!` during
`prefill`.** Should NOT happen now that the heartbeat endpoint is
implemented (section 0) — if you see it, run the section 0 pre-flight
curl check first: if `/lancache-heartbeat` doesn't return 200 with
`X-LanCache-Processed-By`, nginx isn't running the current config (reload
it: `nginx -s reload`, or restart via `poc\stop.ps1` / `poc\start.ps1`)
or the hosts entry isn't active. If the pre-flight check passes but
`prefill` still throws this, that's worth investigating as a genuine
regression, not an expected outcome.

**`select-apps` shows no apps / login fails repeatedly.** Unrelated to
this PoC's cache — that's SteamPrefill talking to the real Steam network
for login/library data over your normal internet connection, not through
`poc/conf/nginx.conf` at all (only depot/manifest content is
cache-routed). Check your Steam account/2FA directly; this kit's cache
plumbing isn't in that path.

**`verify.ps1` reports "no SteamPrefill traffic found in the log."**
Either `prefill` aborted before any depot request (see the
`LancacheNotFoundException` entry above), or you ran `verify.ps1` against
a log window that doesn't include your run — try without `-From`/`-To`
first to see the whole log, or pass explicit timestamps bracketing your
`prefill` run.

**`poc/steamprefill/bin/SteamPrefill.exe --version` doesn't run /
"not recognized".** Re-run `setup.ps1 -Force`; if it still fails, check
`poc/steamprefill/bin/` was actually populated (a single-file self-contained
executable plus a `Config/` folder and `update.ps1`).
