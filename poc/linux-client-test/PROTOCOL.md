# SteamVault Phase 0 PoC — WP 0.6: Linux-Steam-client (WSL2) test protocol

> **STATUS: PRE-BUILT, NOT YET EXECUTED.** This whole kit was written and
> self-tested on 2026-08-04 while WSL2 was **not yet available** on the
> build machine (SVM must first be enabled in BIOS, then Ubuntu installed —
> a separate, later step for the user). Everything below that depends on
> WSL2/Ubuntu actually existing is therefore unverified against the real
> thing; only what's testable on plain Windows/PowerShell today has been
> run (see `test-kit.ps1` and its results). Once WSL2 + Ubuntu exist, work
> through this document top to bottom and treat every WSL-side step as
> "first real run", not "re-run".

## 0. Purpose (`docs/PROJECT_PLAN.md` §7)

The Windows Steam client has a built-in cache-discovery mechanism (checks
whether `lancache.steamcontent.com` resolves) — already proven working by
WP 0.3 (`poc/steam-client-test/`). The **Linux desktop client is known,
by upstream community reputation, to not perform that lookup at all.** This
project has not verified that claim itself yet, and there's no Steam Deck
available to test the actual target hardware. This work package produces
our own first-hand evidence, on the closest thing to hand — the Linux
*desktop* client, run inside WSL2/Ubuntu — for two DNS redirection modes:

- **Scenario A (hosts mode):** add the same `lancache.steamcontent.com`
  hosts-file entry WP 0.3 used, but inside WSL2's own `/etc/hosts`.
  Expectation: the Linux client does **not** look it up, so the cache sees
  **zero traffic**. Producing that null result *cleanly* — i.e., ruling out
  "the entry wasn't there" or "nginx wasn't running" as confounders — **is
  the evidence**, not a test failure.
- **Scenario B (DNS-rewrite mode):** run `dnsmasq` inside WSL2 with a
  wildcard rewrite `*.steamcontent.com` → the cache's IP, and point WSL2's
  resolver at it. Expectation: the Linux client's traffic **does** reach
  the cache, and any already-warm objects come back as `HIT`. This is the
  first real evidence for the `vault-dns` approach (`docs/PROJECT_PLAN.md`
  §3) actually working for Linux/Steam Deck-class clients, which is the
  whole reason `vault-dns` exists as a deployment mode instead of relying
  on hosts-file mode universally.

Both scenarios reuse the exact same running nginx PoC (`poc/conf/nginx.conf`,
started via `poc/start.ps1` on the Windows host) and the exact same access
log (`poc/logs/access.log`) that WP 0.3 already writes to — there is
deliberately no separate Linux-specific cache or log; that's what makes
"a Windows-cached depot immediately serving a Linux client a HIT" a
possible (and interesting, not a bug) outcome — see §5.3.

---

## 1. Prerequisites

Assume, by the time you run this:

1. **SVM (AMD-V equivalent) is already enabled in BIOS** and WSL2 itself
   installs and runs (`wsl --status` succeeds, `wsl --version` reports a
   real version). This document does **not** cover the BIOS/Windows-feature
   enablement step — that's a separate, one-time, reboot-requiring
   prerequisite the user handles before this kit is ever touched.
2. **A freshly installed Ubuntu WSL2 distro** (`wsl --install -d Ubuntu`, or
   whatever current Ubuntu LTS the Store/`wsl --install` offers at the
   time). Freshly installed = no assumptions about prior manual tweaks.
3. **The nginx PoC is running on the Windows host** *before* any WSL-side
   step below:
   ```powershell
   cd poc
   .\start.ps1
   curl.exe -i http://127.0.0.1/health   # expect 200 "ok"
   ```
4. **This repo checkout is reachable from WSL2**, either via the `/mnt/c/...`
   Windows-drive mount (simplest — the scripts don't care where they're
   run from) or a separate `git clone` into WSL2's native filesystem (faster
   I/O, but not required for this kit — none of these scripts touch large
   files). Either way, run the WSL-side scripts (`wsl-setup.sh`,
   `scenario-a.sh`, `scenario-b.sh`) **from inside WSL2**, e.g.:
   ```bash
   cd /mnt/c/claude-dev/SteamVault/poc/linux-client-test
   ./wsl-setup.sh
   ```
   (`chmod +x *.sh` once if the executable bit didn't survive — usually
   unnecessary when running via `bash ./scenario-a.sh` explicitly, or when
   the files were written with the executable bit already set.)
5. Ubuntu's WSL2 image typically ships with `systemd` enabled by default on
   current releases; `wsl-setup.sh` detects this and adapts (falls back to
   `service` commands if `systemd` isn't PID 1). If in doubt:
   ```bash
   ps -p 1 -o comm=
   ```
   `systemd` = systemd path; anything else (e.g. `init`) = fallback path.

---

## 2. Reaching the Windows-host nginx from WSL2

WSL2 runs its own lightweight VM with its own network namespace, so
`127.0.0.1` inside WSL2 is **not** automatically the Windows host — unless
you're on "mirrored" networking mode (see below). `wsl-setup.sh` automates
detection (§2.1), but understand the two cases so troubleshooting makes
sense.

### 2.1 Default: WSL2 NAT mode

By default, WSL2 sits behind a NAT'd virtual switch (`vEthernet (WSL)` on
the Windows side). From inside WSL2, the Windows host is reachable at the
**default-route gateway IP**, which is also usually the first nameserver in
`/etc/resolv.conf` (auto-generated by WSL on every start, unless you've
disabled that — Scenario B deliberately does, see §4):

```bash
ip route show default | awk '{print $3}'     # the gateway = Windows host IP
cat /etc/resolv.conf                          # nameserver line, usually the same IP
```

This IP changes across reboots/network changes, which is exactly why
`wsl-setup.sh` detects it fresh each run rather than hardcoding it anywhere.

### 2.2 Alternative: Windows 11 "mirrored" networking mode

Windows 11 22H2+ supports an opt-in WSL networking mode where WSL2 shares
the host's network namespace directly (`networkingMode=mirrored` in
`%UserProfile%\.wslconfig`, requires `wsl --shutdown` to apply). In that
mode, `127.0.0.1`/`localhost` inside WSL2 **is** the Windows host directly —
no NAT gateway involved. `wsl-setup.sh`'s detection logic (§2.1 of that
script) tries `127.0.0.1` as one of its candidates specifically to cover
this case; it does not assume which mode you're in.

This kit does not require switching networking modes — it works with
whichever mode your WSL2 install already uses. Only switch modes
deliberately (and re-run `wsl-setup.sh` afterwards) if you have a specific
reason to (e.g. troubleshooting).

### 2.3 Windows Firewall check (ADMIN + user-executed)

**This step needs an elevated PowerShell and must be run by you** — Claude
does not have and should not be given standing permission to change
firewall rules. Windows Firewall may block inbound connections arriving on
the `vEthernet (WSL)` interface (it's often classified under the "Public"
profile, which blocks unsolicited inbound traffic to non-registered
listeners by default). nginx running as a plain `nginx.exe` process is not
a registered service with its own firewall rule, so a WSL2 client
connecting to it can be silently dropped even though `curl.exe
127.0.0.1/health` works fine *from Windows itself*.

**Check first (read-only, no elevation needed):**
```powershell
Get-NetFirewallProfile | Select-Object Name, Enabled, DefaultInboundAction
Get-NetConnectionProfile -InterfaceAlias "vEthernet (WSL)" -ErrorAction SilentlyContinue
```

**If WSL2 → Windows-host traffic on port 80 is being blocked** (symptom:
`curl` from inside WSL2 to the detected host IP times out even though
nginx is confirmed running), add a scoped inbound rule — **run this in an
elevated (Run as Administrator) PowerShell window yourself**:
```powershell
New-NetFirewallRule -DisplayName "SteamVault PoC - allow WSL2 to nginx:80" `
    -Direction Inbound -Action Allow -Protocol TCP -LocalPort 80 `
    -InterfaceAlias "vEthernet (WSL)" -Profile Any
```
`netsh` equivalent (slightly less scoped — applies to the port on all
interfaces, not just `vEthernet (WSL)`, but works if `New-NetFirewallRule`
for some reason can't resolve that interface alias on your machine):
```
netsh advfirewall firewall add rule name="SteamVault PoC - WSL2 to nginx 80" dir=in action=allow protocol=TCP localport=80
```

**Rollback** (also ADMIN + user-executed, see §7):
```powershell
Remove-NetFirewallRule -DisplayName "SteamVault PoC - allow WSL2 to nginx:80"
```
or, for the `netsh` variant:
```
netsh advfirewall firewall delete rule name="SteamVault PoC - WSL2 to nginx 80"
```

---

## 3. Setup

### 3.1 Run `wsl-setup.sh` inside WSL2

```bash
cd /mnt/c/claude-dev/SteamVault/poc/linux-client-test    # adjust to your checkout path
./wsl-setup.sh
```

This installs prerequisite packages (dnsmasq, `dig`/`nslookup`, `netstat`,
`ip`, multiarch/i386 support Steam's installer wants), leaves dnsmasq
**disabled/masked** (Scenario B turns it on deliberately), detects the
Windows host IP by actually probing it against the running nginx PoC's
`/health` endpoint (not just guessing from routing tables — see the
script's own header comment), and writes `wsl-env` (gitignored, machine-
specific) next to it for `scenario-a.sh`/`scenario-b.sh` to source. Safe to
re-run any time (e.g. after the host IP changes across a reboot).

### 3.2 Install the Steam client inside WSL2

The Linux Steam client isn't in Ubuntu's default repos in a form that
supports GUI installs cleanly inside WSL2's own package set every release,
so grab Valve's official `.deb` directly from `repo.steampowered.com`
(mirrors the "deb from repo.steampowered.com" instruction this work
package was scoped with) — this step is **interactive** (EULA acceptance),
so it's not part of `wsl-setup.sh`:

```bash
curl -fsSL -o /tmp/steam_latest.deb https://repo.steampowered.com/steam/archive/stable/steam_latest.deb
sudo apt install -y /tmp/steam_latest.deb
```

`apt install <path-to-deb>` (not `dpkg -i`) resolves the package's own
dependencies automatically, which is why `wsl-setup.sh` already made sure
`i386` multiarch is enabled beforehand — the official client still pulls
in some 32-bit libraries.

**GUI note:** WSL2 on current Windows 11 ships **WSLg** (a bundled Wayland/X
compositor) out of the box — no separate X server needed. Launch the client
with:
```bash
steam
```
and a window should appear on your Windows desktop directly. If nothing
appears, confirm WSLg is active: `echo $DISPLAY` should be non-empty (e.g.
`:0`), and `wsl --version` should list a WSLg version. First launch will
self-update; let it finish and log in with a Steam account (a throwaway/
family-shared account is fine — no purchases needed for the recommended
test game, see §3.3).

### 3.3 Test game recommendation

Same choice as WP 0.3, for consistency and because it's genuinely the best
fit: **Spacewar — AppID `480`.** Valve's own hidden Steamworks test app:
free, only tens of MB, exists specifically for tooling tests like this one.
Install via:
```bash
steam steam://install/480
```
(or paste `steam://install/480` into the client's own address bar /
`Run`-equivalent once it's open). If unavailable on your account for any
reason, fall back to any small (order of 100–300 MB) free title you own —
see WP 0.3's `PROTOCOL.md` §2 "Pick a small test game" for the same
reasoning; it applies unchanged here.

---

## 4. Scenario A — hosts mode (expect: zero cache traffic)

Run from `poc/linux-client-test/` **inside WSL2**:
```bash
./scenario-a.sh
```
This adds `<host-ip> lancache.steamcontent.com` to WSL2's own `/etc/hosts`
(the same mechanism WP 0.3 used on the *Windows* side — mirrored here for a
fair, apples-to-apples comparison), verifies the resolution took effect
(`getent hosts lancache.steamcontent.com`), confirms `dnsmasq` is **not**
active (a running Scenario-B dnsmasq would contaminate this scenario's
null-result evidence), and prints a timestamp marker (`date -Is`) — **note
it down**, you'll need it for `analyze-windows.ps1 -From`.

### 4.1 Run the test

1. Fully quit Steam if it was already running (cache-discovery, if the
   client did it at all, would only run at startup — same caveat as
   WP 0.3).
2. Note the start time (or just re-read `scenario-a.sh`'s printed marker).
3. Launch Steam, install/download Spacewar (or your chosen small game).
4. Once done, note the end time.

### 4.2 Confirm the null result *properly*

The whole point here is that "nothing happened" needs to be trustworthy
evidence, not silence you can't distinguish from "the test didn't run":

- **Timestamped log marker before/after** — `scenario-a.sh` already printed
  one at setup; run `date -Is` again right after the download finishes.
  These two timestamps become your `-From`/`-To` window.
- **`netstat` inside WSL** — while the download is running (or right after),
  confirm the Steam client process is *not* talking to the cache's IP at
  all, and see what it *is* talking to instead (real Valve CDN IPs, on 80 or
  443):
  ```bash
  netstat -tnp 2>/dev/null | grep steam
  # or, if netstat's output is sparse under WSL2 without root:
  sudo netstat -tnp | grep steam
  ```
  You should **not** see any established connection to the host IP recorded
  in `wsl-env`. Seeing connections to other (real Valve CDN) IPs on port 80
  or 443 is expected and confirms the download really happened — it just
  didn't route through us.
- **On the Windows side**, confirm nginx's access log agrees — this is the
  authoritative check, since it's the cache's own record, not an inference
  from the client side:
  ```powershell
  cd poc\linux-client-test
  .\analyze-windows.ps1 -Scenario A -From "<start-timestamp>" -To "<end-timestamp>"
  ```
  Expect: `[ OK ] 0 requests in window - the expected null result. This IS
  the evidence.` If it instead reports nonzero traffic, that's a genuinely
  interesting finding (it would mean the Linux client's behavior differs
  from the documented community assumption) — see PROTOCOL.md §8
  troubleshooting before concluding either way.

### 4.3 Rollback
```bash
./scenario-a.sh --rollback
```
Removes the hosts-file block again (see §7 for the full rollback list).

---

## 5. Scenario B — DNS-rewrite mode (expect: cache traffic + HITs)

Run from `poc/linux-client-test/` **inside WSL2**:
```bash
./scenario-b.sh
```

This writes a `dnsmasq` config with a wildcard rewrite:
```
address=/steamcontent.com/<host-ip>
```
(matches `*.steamcontent.com`, the family the real client actually resolves
CDN edges under — not just the single `lancache.steamcontent.com` name
Scenario A/WP 0.3 rely on), (re)starts `dnsmasq`, backs up and repoints
`/etc/resolv.conf` at `127.0.0.1` (WSL2's own auto-regeneration of that
file is disabled for this distro so it sticks — see the script's comments),
and verifies — before you touch Steam — that:

1. `lancache.steamcontent.com` resolves to the host IP.
2. An arbitrary made-up `*.steamcontent.com` subdomain **also** resolves to
   the host IP (proves the wildcard, not just a lucky exact-name match).
3. **AAAA note (`docs/PROJECT_PLAN.md` §3):** an `AAAA` query for the same
   name returns `NODATA` (`NOERROR`, zero answers) rather than a real IPv6
   address or `NXDOMAIN` — this is `dnsmasq`'s `address=` directive
   behavior and is exactly what **closes the IPv6 bypass** the plan calls
   out: a client that falls back to `AAAA` when it doesn't like the `A`
   answer gets nothing usable, instead of silently reaching the real CDN
   over IPv6 and skipping the cache.

It then prints a timestamp marker, same as Scenario A.

### 5.1 Run the test

Same mechanics as §4.1 — quit Steam fully first (so it re-resolves on next
launch), launch it, download the test game, note start/end.

### 5.2 Analyze

```powershell
cd poc\linux-client-test
.\analyze-windows.ps1 -Scenario B -From "<start-timestamp>" -To "<end-timestamp>"
```
Expect real traffic in the window, and — if the depot chunks were already
warm from an earlier run (see §5.3) — `HIT`s. If this is the *very first*
time these depots have ever been fetched (cold cache), everything will
correctly show as `MISS` on this run; that's not a failure, it's the same
"first download populates the cache" behavior WP 0.3 already established
for the Windows client. To see a `HIT` within Scenario B itself, uninstall
and reinstall the game (same "wipe nothing, redownload" cycle as WP 0.3 §2.2)
and analyze the second run's window separately.

### 5.3 A note on the shared cache (not a bug — a bonus data point)

This kit deliberately reuses the **same** nginx instance and **same**
on-disk cache (`poc/cache/`) as WP 0.3's Windows-client test, rather than
standing up a separate cache. Two consequences worth knowing before you
run this:

- If you pick the **same** test game WP 0.3 already downloaded (e.g.
  Spacewar `480`) and its depot chunks are already sitting in
  `poc/cache/depot/...` from that earlier Windows-client run, your **very
  first** WSL2/Linux-client download of that same game may come back as an
  immediate `HIT` — no MISS-then-HIT cycle needed within Scenario B at all.
  That's not a mistake; it's actually a nice extra proof point: the
  path-faithful cache is entirely client-agnostic — a depot cached by a
  Windows client serves a completely different client (different OS, same
  machine or not) identically. Worth noting explicitly in your findings
  write-up if it happens.
- If you'd rather isolate the Linux-client path cleanly (a fresh MISS you
  can then turn into a same-scenario HIT via uninstall/reinstall, exactly
  mirroring WP 0.3's structure), pick a **different** small game than
  whatever WP 0.3 already exercised, or clear `poc/cache/` first — **but
  clearing `poc/cache/` also discards WP 0.1–0.5's evidence data along with
  it**, so only do that with a clear reason (this PoC keeps that cache
  around deliberately as its own evidence trail; if you must reset, copy
  `poc/cache/` and `poc/logs/access.log` aside first).

Either path answers the Phase-0 question this work package exists for; pick
whichever framing you want your write-up to lead with.

### 5.4 Rollback
```bash
./scenario-b.sh --rollback
```
Restores `/etc/resolv.conf` from the backup `scenario-b.sh` took, removes
the `generateResolvConf=false` override from `/etc/wsl.conf` (so WSL2 goes
back to auto-managing DNS on its own), and disables/masks `dnsmasq` again.
See §7 for the full rollback list including the Windows-side firewall rule.

---

## 6. Analysis reference

`analyze-windows.ps1` (in this same folder) is a **thin wrapper** around
`poc/steam-client-test/analyze.ps1` (WP 0.3) — it reuses that script's
entire parsing/windowing/reporting logic as-is (same `-From`/`-To`,
same `poc/logs/access.log`, same `RESULTS-*.md` report), and only adds a
short scenario-specific verdict on top (zero-traffic check for A, hit-ratio
narration for B). See that script's own header comment for the full report
contents (URI-scheme conformance, Range usage, hit/miss split, throughput,
per-depot breakdown) — all of it applies here unchanged, since it's the
same log format either client writes to.

```powershell
.\analyze-windows.ps1                                  # whole log, no scenario verdict, no window
.\analyze-windows.ps1 -Scenario A -From "..." -To "..."  # Scenario A verdict
.\analyze-windows.ps1 -Scenario B -From "..." -To "..."  # Scenario B verdict
```

---

## 7. Full rollback checklist

Work through this whether you ran one scenario or both — most steps are
idempotent no-ops if that particular scenario was never run.

1. Inside WSL2:
   ```bash
   cd /mnt/c/claude-dev/SteamVault/poc/linux-client-test   # adjust as needed
   ./scenario-a.sh --rollback
   ./scenario-b.sh --rollback
   ```
2. **Windows side, if you added the firewall rule in §2.3** (elevated
   PowerShell, ADMIN + user-executed):
   ```powershell
   Remove-NetFirewallRule -DisplayName "SteamVault PoC - allow WSL2 to nginx:80"
   ```
3. Quit the WSL2 Steam client (or just leave WSL2 — `wsl --shutdown` from
   an elevated or regular Windows PowerShell tears down the whole VM,
   which is the cleanest full reset if you're done testing for now).
4. `poc/cache/` and `poc/logs/access.log` on the Windows side are untouched
   by any of this — same shared evidence trail as before (see §5.3). Leave
   them, or handle per your own cleanup preference; this kit never deletes
   them for you.
5. Optionally uninstall the Steam client / test game inside WSL2, or leave
   it — WSL2 disk usage doesn't affect the Windows host beyond the VHDX
   file size.

---

## 8. Troubleshooting

**Symptom: `wsl-setup.sh` can't detect a host IP at all.**
It tried, in order: `$WSL_HOST_IP` (manual override, if set), the
default-route gateway, `/etc/resolv.conf`'s first nameserver, and
`127.0.0.1` (mirrored-mode case) — verifying each candidate by actually
`curl`-ing `http://<candidate>/health` and requiring the exact `ok` body
nginx returns. If all fail:
1. Confirm nginx is actually running on the Windows host (§1.3).
2. Re-check the Windows Firewall rule (§2.3) — this is the single most
   likely cause, since routing/DNS can look fine while the firewall still
   silently drops the TCP handshake.
3. As a last resort, force it: `WSL_HOST_IP=<ip> ./wsl-setup.sh`, using
   whatever IP you can confirm manually reaches nginx from WSL2 (e.g. via
   `curl.exe` output for `ipconfig`'s `vEthernet (WSL)` adapter address on
   the Windows side).

**Symptom (Scenario A): traffic *does* show up in the "expect zero" window.**
Don't assume the script is broken — re-verify the window bounds first
(off-by-a-few-seconds timestamps are the most common cause of contaminated
windows), then check whether `dnsmasq` was actually inactive during the
run (`scenario-a.sh` warns about this at setup time, but a manual restart
in between could re-enable it). If both check out, this is a genuinely
interesting result that contradicts the documented community assumption —
capture it, don't discard it; see §0.

**Symptom (Scenario B): zero traffic in the "expect real traffic" window.**
1. Re-run `scenario-b.sh` (no `--rollback`) — it re-verifies DNS state
   every time and will fail loudly (non-zero exit) if resolution isn't
   correct, rather than silently letting you proceed.
2. Confirm Steam was fully quit and relaunched *after* `scenario-b.sh` ran
   (same "must restart to re-resolve" caveat as the Windows client, WP 0.3
   §1 step 4).
3. Check `sudo systemctl status dnsmasq` (or `sudo service dnsmasq status`)
   actually shows it running, and that nothing else is bound to port 53
   already (e.g. `systemd-resolved`, if your Ubuntu WSL2 image happens to
   run it) — a bind conflict there fails `dnsmasq` silently from Steam's
   point of view (DNS queries just time out or fall through to whatever
   *did* win port 53).
4. Re-check the Windows Firewall (§2.3) exactly as in Scenario A — DNS
   resolving correctly does not guarantee the subsequent HTTP connection to
   the cache IP is allowed through.

**Symptom: `steam` doesn't open a window at all under WSLg.**
Confirm `echo $DISPLAY` is non-empty and `wsl --version` (run from the
Windows side) lists a WSLg component. If WSLg genuinely isn't available on
your Windows build, this is outside this kit's scope to fix — see
Microsoft's WSLg documentation for enabling it (requires a reasonably
current Windows 11).

**Symptom: `apt install /tmp/steam_latest.deb` fails on dependencies.**
Re-run `wsl-setup.sh` first (confirms `i386` multiarch + `apt-get update`
ran), then retry. If it still fails, `sudo apt-get install -f` after the
attempted install resolves most remaining dependency gaps.
