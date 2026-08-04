# SteamVault Phase 0 PoC — WP 0.3: Real-Steam-client test protocol

This is the step-by-step protocol for testing the WP 0.1/0.2 nginx PoC
(`poc/conf/nginx.conf`) against a **real Steam client and a real download**,
using the Windows hosts-file cache-discovery mechanism described in
`docs/PROJECT_PLAN.md` (§10, deployment mode 3: "DNS-free hosts mode").

Read this whole document before touching the hosts file. It requires
administrator rights for one step and a real (small) game download.

Once you're done, run `analyze.ps1` (in this same folder) against
`poc/logs/access.log` — it mines the log automatically and answers the
Phase-0 checkboxes in `docs/PROJECT_PLAN.md` §7. You only need to *do* the
download and note when it started/ended; the script does the analysis.

---

## 0. Preconditions

1. **nginx PoC must be running before Steam ever tries to download
   anything.** From `poc/`:
   ```powershell
   cd poc
   .\start.ps1
   ```
   (If it's already running, `start.ps1` just tells you the existing PID and
   exits 0 — safe to re-run.)

2. **Confirm it's actually healthy:**
   ```powershell
   curl.exe -i http://127.0.0.1/health
   ```
   Expect `HTTP/1.1 200 OK` and body `ok`. If this fails, stop here — fix
   nginx first (see `poc/README.md` / `poc/logs/error.log`). Nothing below
   will produce useful evidence if nginx isn't listening on port 80.

3. **Cache state snapshot (informational only).** A fresh/empty
   `poc/cache/` is the cleanest starting point (makes "first download = all
   MISS, second download = all HIT" obvious at a glance), but **it is not
   required** — `analyze.ps1` works per-appid/per-depot regardless of what
   else is already in the cache, and the whole point of path-faithful
   storage is that pre-existing, unrelated depots don't interfere with the
   analysis of the depot(s) your test game uses. If you want a clean
   baseline anyway:
   ```powershell
   Get-ChildItem poc\cache\depot -ErrorAction SilentlyContinue | Measure-Object
   ```
   Note whatever it reports (or "empty") in your own notes — that's it, no
   action needed.

---

## 1. Hosts-file step (requires an elevated editor)

### Why this works

The **Windows** Steam client has a built-in cache-discovery behavior: before
falling back to Valve's CDN, it checks whether the hostname
`lancache.steamcontent.com` resolves to anything reachable, and if it does,
uses that host as a caching proxy for depot/manifest downloads. This is a
genuine Valve client feature (not a LanCache-project mechanism) — see
`docs/PROJECT_PLAN.md` §10, deployment mode 3, and the "Upstream choice and
the loop-risk it avoids" section of `poc/README.md` for why this PoC's own
`nginx.conf` deliberately proxies to a *different* upstream hostname so it
never resolves itself in a loop once you add the entry below.

**The Linux/Steam Deck client does not do this lookup at all** — this
protocol and this hosts-file trick only apply to the Windows client. That's
a known, separate Phase-0 open item (see `docs/PROJECT_PLAN.md` §7), not
something this work package attempts to fix.

### Steps

1. Open Notepad (or your editor of choice) **as Administrator**. The hosts
   file is not writable otherwise.
   ```powershell
   Start-Process notepad.exe -Verb RunAs C:\Windows\System32\drivers\etc\hosts
   ```
2. Add this exact line at the end of the file (one line, nothing else on
   it):
   ```
   127.0.0.1 lancache.steamcontent.com
   ```
   Save and close.
3. **Verify it took effect:**
   ```powershell
   Resolve-DnsName lancache.steamcontent.com
   ```
   Expect it to resolve to `127.0.0.1`. (`Resolve-DnsName` on Windows
   consults the hosts file before any real DNS server, so this confirms the
   edit is live without needing to restart anything system-wide.) A quick
   double-check:
   ```powershell
   ping -n 1 lancache.steamcontent.com
   ```
   should show replies from `127.0.0.1`.

4. **Restart Steam.** This is not optional — the client only runs its cache
   discovery check at startup (or on certain reconnects), so if Steam was
   already running before you edited the hosts file, it will not notice the
   change until you fully quit and relaunch it (quit via the tray icon, not
   just closing the window — Steam keeps running in the background
   otherwise).

> **Ordering matters, twice over:**
> - nginx must already be listening on port 80 **before** Steam starts any
>   download (steps in section 0 first).
> - Steam must be **restarted after** the hosts-file change so it re-runs
>   cache discovery against the new entry.
>
> Doing this in the wrong order is the single most common reason this test
> silently "does nothing" — see Troubleshooting below.

---

## 2. Test run

### Pick a small test game

Any small, free-to-download Steam title works — genuinely small, a few
hundred MB at most, so the whole protocol takes minutes, not hours.
**Avoid large "free-to-play" titles like Team Fortress 2, Dota 2, or
Counter-Strike 2** — these are tens of GB nowadays (TF2 alone is roughly
30 GB) and are a poor fit here; stopping a download partway defeats the
"download, uninstall, download again" comparison this protocol relies on.

Concrete small options:
- **Spacewar** — AppID `480`. This is Valve's own hidden Steamworks test
  application: free, only tens of MB, and it exists specifically for
  testing/tooling purposes like this one (it doesn't show up in a normal
  Store search or library view). Install it via `steam://install/480`
  (paste that into the Run dialog or your browser's address bar with Steam
  running) or `steamcmd`. If it's unavailable to your account for any
  reason, fall back to the option below.
- Any free tool/demo app in the ~100–300 MB range you already own or can
  add for free — the *exact* game does not matter for this test, only that
  it's small and reproducible. If in doubt, sort your existing Steam
  library by size and pick the smallest free title; `analyze.ps1`'s
  per-depot breakdown works for any depot ID, it doesn't need to be told
  the appid in advance. **Check the size on its store/library page before
  starting** — Valve-side content sizes change over time, so don't rely on
  a size you remember from elsewhere.

  If you'd rather not add a new game, an existing small installed game you
  already own also works fine for the "uninstall, wipe nothing, reinstall"
  cycle in step 2.2 below — it doesn't have to be free, it just has to be
  small so the round trip is fast.

### 2.1 First download (expect: all cache MISS)

1. **Note the wall-clock start time** (e.g. `Get-Date`) before you click
   Install/Download in Steam.
2. Let it download and install fully.
3. **Note the end time** once Steam reports the install complete.

That's all you need to capture manually — `analyze.ps1` mines
`poc/logs/access.log` for everything else (per-request timing, bytes,
hit/miss, URI scheme, Range usage).

### 2.2 Uninstall, then download again (expect: all cache HIT)

1. Uninstall the game from Steam (right-click → Manage → Uninstall).
   **Do not touch `poc/cache/` at all** — the entire point is proving the
   *second* run gets served from the untouched on-disk cache.
2. **Note the start time**, then reinstall/download the same game again.
3. **Note the end time.**

Expect this second run to be dramatically faster (LAN/disk-speed, not
internet-speed) if the caching hypothesis holds — `analyze.ps1`'s
throughput section quantifies this from the log directly, but a stopwatch
difference you notice yourself is a good sanity check too.

### What to capture

Just the four timestamps (first-run start/end, second-run start/end) —
write them down or keep the PowerShell console history. You do not need to
capture anything from the Steam UI itself; the access log has the
per-request ground truth.

---

## 3. Analyze

From `poc/steam-client-test/`:

```powershell
.\analyze.ps1
```

This analyzes the *entire* `poc/logs/access.log` (including any earlier
WP 0.1/0.2 traffic still in there — that's fine, it's designed to coexist).
It prints a full report to the console and also writes
`RESULTS-<timestamp>.md` in this folder.

For a clean, uncontaminated read of the *second* (cache-warm) run
specifically, narrow the window to the timestamps you noted above:

```powershell
.\analyze.ps1 -From "2026-08-04 18:05:00" -To "2026-08-04 18:12:00"
```

(Use your own noted start/end times, with a minute or two of margin on
each side.) Run it once for the whole log and once narrowed to the second
run — both are useful, and both get written as separate timestamped
`RESULTS-*.md` files so you keep a record of each.

The report covers, section by section:
1. **URI-scheme conformance** — does the real client consistently request
   `/depot/<id>/chunk/<hash>` (and `/depot/<id>/manifest/...`)? Every
   non-conforming URI is listed verbatim (e.g. `/serverlist/...`,
   `/client/...` paths) — that's exactly the new information this test
   exists to gather.
2. **Range usage** by the real client (how many requests carried a `Range`
   header, and what kind).
3. **Hit/miss split** and bytes served, with a hit ratio.
4. **Throughput estimate** for the analyzed window, plus MISS-vs-HIT
   latency/throughput — the data input for the miss-handling decision in
   the plan (synchronous store vs. transparent-passthrough + async
   prefill).
5. **Per-depot request/byte counts** — the foundation for the later
   depot→app mapping sanity check.

---

## 4. Rollback

1. Quit Steam.
2. Remove the hosts-file line you added:
   ```powershell
   Start-Process notepad.exe -Verb RunAs C:\Windows\System32\drivers\etc\hosts
   ```
   Delete the `127.0.0.1 lancache.steamcontent.com` line, save, close.
3. Confirm it's gone:
   ```powershell
   Resolve-DnsName lancache.steamcontent.com
   ```
   should now fail or resolve to Valve's real CDN, not `127.0.0.1`.
4. Restart Steam again so it stops using the (now-removed) cache override.
5. You can leave nginx running or stop it (`poc\stop.ps1`) — it's harmless
   either way once the hosts entry is gone, since nothing will route through
   it anymore.

---

## 5. Troubleshooting

**Symptom: no new lines appear in `poc/logs/access.log` at all while Steam
downloads.** This means Steam bypassed the cache entirely — it went
straight to Valve's real CDN. Known causes, roughly in order of likelihood:

1. **Steam wasn't restarted after the hosts-file edit.** Cache discovery
   only runs at client startup. Fully quit (tray icon → Exit) and relaunch.
2. **nginx wasn't running (or not on port 80) when Steam started
   downloading.** Re-check `curl.exe -i http://127.0.0.1/health` *before*
   you click Install. If nginx was started *after* Steam began the
   download, some in-flight connections may have already been negotiated
   against the real CDN — stop the download, confirm nginx is up, and
   restart the download.
3. **The hosts line is malformed.** Common mistakes: extra whitespace
   patterns hosts doesn't like, wrong IP, a typo in the hostname, or the
   line accidentally commented out with a leading `#`. Re-open the file and
   compare byte-for-byte with `127.0.0.1 lancache.steamcontent.com`.
   `Resolve-DnsName lancache.steamcontent.com` is the fast way to confirm —
   if it doesn't return `127.0.0.1`, the hosts file isn't the reason
   traffic is flowing; something else is (see next point).
4. **Antivirus/VPN/other software rewriting or ignoring the hosts file.**
   Some security suites protect the hosts file from edits or maintain their
   own DNS path that bypasses it. If `Resolve-DnsName` shows `127.0.0.1`
   but Steam still isn't hitting nginx, this is the next thing to suspect —
   check `poc/logs/error.log` for connection attempts from the Steam client
   process, or use `netstat` to see what Steam is actually connecting to
   during the download.
5. **You're on the Linux/Steam Deck client.** This mechanism is
   Windows-only by design (see section 1) — not a bug, a known scope limit
   from `docs/PROJECT_PLAN.md` §7.
6. **The client fetched depot content over HTTPS (port 443) instead of
   plain HTTP.** `docs/PROJECT_PLAN.md` §10 documents that Steam CDN
   traffic is plain HTTP, and this PoC's nginx only listens on port 80 —
   but if you're stuck with zero log lines and everything else above
   checks out, it's worth ruling out: use `netstat -ano | findstr :443` (or
   check `poc/logs/error.log` / a packet capture) while a download is
   running to see whether Steam is talking to port 443 on some CDN IP
   instead of routing through `127.0.0.1:80`. If so, that's a real,
   separate bypass path this PoC does not handle — worth a note for later
   phases, not something to fix by hand here.

**Symptom: some lines appear, but far fewer than expected / a mix of hit
and miss on what should be a fully-warm second run.** Two different things
can cause this, and they point in very different directions:

- **New or updated depots.** Steam's download manager can pull chunks in
  parallel from multiple depots (base game + DLC/language depots you
  didn't explicitly select), and some of those may not have been present
  in the first run at all if Steam changed what it requested between runs
  (e.g. an update landed between your two downloads). Check the per-depot
  section of the `analyze.ps1` report — it'll show you exactly which
  depot(s) had MISS entries on the "second" run so you can tell whether
  that's a new depot (expected) or a real cache miss on a
  previously-cached one (worth investigating).
- **Range requests hitting a cold (or not-yet-fully-stored) object — this
  is the more important case, and it is exactly the Phase-0 evidence this
  work package exists to gather, not a test failure.** The real Steam
  client uses `Range` requests heavily (unlike the synthetic curl-only
  evidence in WP 0.2), and `poc/conf/nginx.conf`'s `@miss` location does
  **not** strip the client's `Range` header before `proxy_pass` — it's
  forwarded upstream as-is. WP 0.2's findings (`poc/RANGE-FINDINGS.md`)
  show the *one* upstream edge tested there always ignores `Range` on a
  miss and returns the full `200` body, which is what keeps `proxy_store`
  safe in that evidence. If a real download instead shows unexpected
  MISSes on the warm run, or a download that stalls/restarts/re-verifies
  content, the likely mechanism is: a different CDN edge *did* honor the
  client's `Range` header on a miss, `proxy_store` persisted that partial
  body at the object's full-file path anyway, and a later plain (or
  differently-ranged) request against that same path got served corrupt
  or truncated data — forcing Steam to re-fetch (hence the extra MISSes)
  or reject the chunk (hence a stall/restart). **Do not treat this as a
  bug to patch in `nginx.conf` for this test** — the goal right now is to
  capture it as evidence. If you see this: check the Range section of the
  `analyze.ps1` report (it breaks down suffix/explicit/multi-range usage
  and counts) for the window around the anomaly, cross-reference against
  `poc/RANGE-FINDINGS.md`, and note it — this is precisely the Plan A vs.
  Plan B evidence `docs/PROJECT_PLAN.md` §9 asks Phase 0 to produce.

**Symptom: `analyze.ps1` reports zero lines / errors on a missing log
file.** Make sure you're pointing it at the right log
(`poc/logs/access.log`, the default) and that nginx actually wrote to it —
check `poc/logs/error.log` for nginx startup problems first.
