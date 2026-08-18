# Deploying SteamVault (Phase 1, WP 1.9)

Docker Compose deployment for the three server-side components:

| Service      | What it is                                        | Port  | Enabled |
|--------------|---------------------------------------------------|-------|---------|
| `vault-core` | nginx `proxy_store` cache, path-faithful depot storage | 80 (HTTP) | always |
| `vault-api`  | FastAPI + SQLite control plane, runs SteamPrefill | 8080  | always |
| `vault-dns`  | optional dnsmasq that redirects `*.steamcontent.com` | 53 (UDP+TCP) | `--profile dns` |

Everything is LAN-only. Nothing here should ever be reachable from the
internet — see [Security posture](#security-posture) before you expose
anything.

```
deploy/
├── compose.yaml            # the deployment
├── .env.example            # committed template -> copy to .env
├── examples/
│   └── truenas-scale-dockge.md   # NAS-specific layout (dedicated ZFS cache dataset, etc.)
├── tests/
│   └── verify-stack.sh     # container verification suite (see "Verifying")
└── VERIFICATION-*.md       # recorded evidence from a real run
```

---

## Requirements

- Docker Engine with Compose v2 (`docker compose`, not `docker-compose`).
  Verified against **Docker Engine 29.1.3 / Compose 2.40.3** on Ubuntu 26.04
  (WSL2) — and, as of the 2026-08-17 packaging work package, `deploy/tests/
  verify-stack.sh` has now actually run against that real host: 105/109
  checks passed on the final run, across three total runs spanning two
  review rounds. **The 4 failures were a genuine pre-existing bug in step
  5i**, unrelated to the packaging work package that finally ran it for
  real: nginx's cache-event `access_log` uses `buffer=64k flush=5s`
  (`core/nginx/nginx.conf`), and step 5i grepped the log file immediately
  after the triggering request with no wait for that flush — an isolated
  repro confirmed the correct 9-field line appears once you wait past the
  5-second buffer (a fresh `docker run` of vault-core, one real MISS, and a
  check 7 s later shows the expected line every time; checking at 1 s does
  not). **Reproducible, not deterministic:** the same 4 lines failed on
  every run so far, but the pass/fail line was genuinely timing-dependent —
  a slower host could clear the 5 s window before the grep and pass by
  chance, so a green 5i by itself would not have proven the underlying bug
  was fixed. The feature itself was always correct; only the test's timing
  wasn't. **Fixed in WP 4g** (2026-08-18): step 5i now polls for the line
  with a bounded wait-for-line loop (up to 10 s) instead of reading
  immediately, so a green run means the flush-and-read path actually
  worked within budget — see `verify-stack.sh`'s comment above step 5i.
  Every check the packaging work package itself added, across both review
  rounds, passed on every run.
- Outbound internet (the cache fetches from the Steam CDN on a miss).
- Disk space for the cache. There is no eviction, ever — that is the
  project's whole point (`docs/PROJECT_PLAN.md` §3). You delete games
  explicitly via the API.

---

## Quickstart

```bash
cd deploy
cp .env.example .env
$EDITOR .env                      # set VAULT_API_KEY (only mandatory value)
docker compose up -d --build
```

Check it:

```bash
curl http://<server>/health                     # -> ok            (vault-core)
curl -I http://<server>/lancache-heartbeat      # -> X-LanCache-Processed-By: steamvault
curl http://<server>:8080/v1/health             # -> {"status":"ok"}
curl -H "X-Api-Key: $VAULT_API_KEY" http://<server>:8080/v1/games
```

### Health and liveness at a glance

Every image carries its own `HEALTHCHECK`, so `docker compose ps` tells you the
truth without external tooling. Each probe was chosen to prove the thing that
actually matters for that service, not merely that a process exists:

| Service | Container `HEALTHCHECK` | Externally pollable | Proves |
|---|---|---|---|
| `vault-core` | `wget -q -O /dev/null http://127.0.0.1/health` | `GET http://<server>/health` → `ok` | nginx is up and serving. Local-only location: no Host allowlist entry needed, no upstream contact — a liveness probe, *not* an "is the internet reachable" probe |
| `vault-api`  | `python -c "urllib.request.urlopen('http://127.0.0.1:8080/v1/health')"` (no extra packages in the image) | `GET http://<server>:8080/v1/health` → `{"status":"ok"}` | the app is serving. The **one** unauthenticated route by design (`api/README.md` "Auth"): fixed body, no data, meant for exactly this |
| `vault-dns`  | `nslookup -type=a healthcheck.steamcontent.com 127.0.0.1` must answer `$CACHE_IP` | `dig +short A <any>.steamcontent.com @<server>` | the **redirect is live**, not just that dnsmasq is running — a resolver answering the wrong address would pass a process check and fail this one |

Interval 30 s, 3 retries; start period 5 s (core, dns) / 10 s (api).
`docs/PROJECT_PLAN.md` §10 designates `/v1/health` for external monitoring —
point your uptime checker at that one.

Then pick a DNS mode (below), and do the one-time SteamPrefill login.

`docker compose up` **refuses to start without `VAULT_API_KEY`** — that is
deliberate. There is no default API key anywhere in this project; a shipped
default is a shipped vulnerability.

---

## First run: the one-time SteamPrefill login

vault-api drives SteamPrefill as a subprocess, and SteamPrefill needs a Steam
session. That session is created **once, interactively, by you** — vault-api
never sees, stores, transmits or logs Steam credentials (ADR-0004), and no
login ever happens during an image build.

```bash
cd deploy
docker compose run --rm --no-deps -it vault-api \
    /opt/steamprefill/SteamPrefill select-apps
```

Enter your account name, password and Steam Guard code when prompted, then
exit the app selector (vault-api overwrites the app selection per job anyway —
`Config/selectedAppsToPrefill.json` is how it tells SteamPrefill which app to
prefill, see `api/README.md`). The session lands in the `vault-steamprefill`
volume at `/opt/steamprefill/Config` and survives restarts and image upgrades.

Until you do this, everything else works — `/v1/games`, `/v1/mapping`,
`/v1/cache/*`, the cache itself — and only *prefill jobs* fail, with an
actionable message telling you to run the command above.

**Treat the `vault-steamprefill` volume as sensitive.** It holds a logged-in
Steam session.

---

## A container-specific trap: does SteamPrefill actually reach your cache?

This has only ever been proven **natively** (Phase 0, WP 1.7 — via a Windows
hosts-file entry) and never inside the container SteamPrefill actually ships
in here, which matters because the mechanism is different from every DNS mode
above — and, measured, matters in only ONE of the two `VAULT_CORE_BIND`
layouts, not both. Read to the end before adding anything to your setup.

### The real detection mechanism (four candidates, not one)

SteamPrefill runs *inside* the `vault-api` container as a subprocess, and it
does **not** simply trust the Windows client's hosts-file hostname. Per its
own source (confirmed by this project's own read, `poc/steamprefill/
PROTOCOL.md` §0 "SteamPrefill's cache-detection contract", and independently
confirmed by scanning the shipped SteamPrefill binary itself for embedded
strings), it tries, **in this order**, resolving each to an
RFC1918-or-loopback IPv4 address:

1. `lancache.steamcontent.com` (DNS — the same name the Windows client and
   vault-agent's hosts mode use)
2. `localhost`
3. **the fixed literal `172.17.0.1`** — the classic Docker default bridge's
   gateway address, hardcoded verbatim inside the binary (confirmed present
   as a UTF-16LE string in the shipped `.NET` executable; it is the only
   private IPv4 literal there matching SteamPrefill's documented candidate
   list — `127.0.0.1` also appears, as candidate 2 — and no
   `host.docker.internal`-style hostname appears at all). This
   is NOT SteamPrefill dynamically detecting "whatever this container's own
   gateway happens to be" — it is one specific, unconditional address.
4. the local machine's own hostname

For **each** candidate that resolves to a private/loopback IPv4, it sends
`GET http://<ip>/lancache-heartbeat` and accepts the candidate only if the
response carries `X-LanCache-Processed-By` — vault-core answers this at
`core/nginx/nginx.conf`'s `/lancache-heartbeat` location with `steamvault`.
It stops at the first candidate that passes. If none does, SteamPrefill
quietly downloads straight from Valve instead: **the job still finishes and
reports success, and the cache stays empty.** No error, no red job status —
the same silent-failure shape requirement A12 is scoped to catch for
*client* traffic, except here it is vault-api's own prefill traffic
bypassing itself.

### Whether this bites you depends entirely on `VAULT_CORE_BIND`

**Default layout (`VAULT_CORE_BIND` unset, i.e. `0.0.0.0`): candidate 3
already succeeds, DNS-independently, even though `172.17.0.1` is not
`vault-api`'s own network's gateway.** `deploy/compose.yaml` puts every
service on its own Compose-managed bridge network (a DIFFERENT subnet from
the classic default bridge — `172.19.0.0/16` in one measured run, not
`172.17.0.0/16`), so `172.17.0.1` is not directly reachable the way a
same-network address would be. It works anyway, for a specific, checked
reason: Docker publishes vault-core's port 80 on **every** host interface
when bound to `0.0.0.0`, including the classic default bridge's own gateway
address `172.17.0.1` (that bridge always exists on a Docker host, used or
not). A packet from `vault-api`'s container aimed at `172.17.0.1` leaves via
its own network's gateway, arrives at the HOST, and the host — which has a
direct, local route to `172.17.0.0/16` via its own `docker0` interface —
forwards it the rest of the way to vault-core's published port. This is
ordinary host-level routing between two of the host's own interfaces, not
container-to-container traffic crossing Docker's inter-network isolation
(which does block THAT). Measured directly, from inside a real Compose
stack's `vault-api` container whose OWN network gateway is `172.19.0.1`,
probing the literal `172.17.0.1` regardless:

```
$ docker compose exec vault-api python3 -c "import urllib.request as u; r=u.urlopen('http://172.17.0.1/lancache-heartbeat',timeout=5); print(r.status, r.headers.get('X-LanCache-Processed-By'))"
200 steamvault
```

So in the layout `docker compose up` gives you out of the box, **there is
nothing to fix here** — candidate 1 (DNS) may well fail exactly as
`docs/PROJECT_PLAN.md`'s evidence note records, but candidate 3 catches it
DNS-independently before SteamPrefill ever falls back to Valve, as long as
the host's default bridge is up (it is, by default, on any Docker
installation) and nothing has firewalled inter-bridge host routing.

**The trap is real in the *other* layout: `VAULT_CORE_BIND` set to a
dedicated address** — the port-80-conflict recipe above, and exactly what
[the TrueNAS guide](examples/truenas-scale-dockge.md) instructs whenever
something else already owns port 80. Binding to one specific address means
Docker publishes port 80 **only** there — not on `172.17.0.1`, not on
loopback. Measured, same command, this time against a stack with
`VAULT_CORE_BIND` set to a dedicated address instead of `0.0.0.0`:

```
$ docker compose exec vault-api python3 -c "import urllib.request as u; r=u.urlopen('http://172.17.0.1/lancache-heartbeat',timeout=5); print(r.status, r.headers.get('X-LanCache-Processed-By'))"
[...]
urllib.error.URLError: <urlopen error [Errno 111] Connection refused>

$ docker compose exec vault-api python3 -c "import urllib.request as u; r=u.urlopen('http://127.0.0.1/lancache-heartbeat',timeout=5); print(r.status)"
[...]
urllib.error.URLError: <urlopen error [Errno 111] Connection refused>

$ docker compose exec vault-api python3 -c "import urllib.request as u; r=u.urlopen('http://192.168.1.50/lancache-heartbeat',timeout=5); print(r.status, r.headers.get('X-LanCache-Processed-By'))"
200 steamvault
```

(`[...]` above elides the Python traceback's middle frames for readability —
the meaningful line is the final `URLError`/`ConnectionRefusedError`; nothing
is hidden except stack-frame noise, and the exit still happened with no
response.) Candidates 2 and 3 both refuse; candidate 1 (DNS) is your only
remaining chance, and only if your resolver rewrites the zone for the
CONTAINER too (not a given — see the earlier DNS section). If it doesn't,
this is exactly where prefill jobs silently fill nothing.

### Check it

Probe the heartbeat directly, from inside `vault-api` — a DNS lookup
answers the wrong question, since candidates 2–4 never involve DNS at all.
**`curl` and `ip` are not installed in the `vault-api` image** (it's
`python:3.13-slim`, not vault-core's nginx/Alpine image) — use `python3`,
which is:

```bash
# the fixed-literal candidate (works out of the box on the default 0.0.0.0 bind):
docker compose exec vault-api python3 -c "import urllib.request as u; r=u.urlopen('http://172.17.0.1/lancache-heartbeat',timeout=5); print(r.status, r.headers.get('X-LanCache-Processed-By'))"

# the address you actually bound VAULT_CORE_BIND to, if you set one:
docker compose exec vault-api python3 -c "import urllib.request as u; r=u.urlopen('http://<VAULT_CORE_BIND value>/lancache-heartbeat',timeout=5); print(r.status, r.headers.get('X-LanCache-Processed-By'))"
```

A line printing `200 steamvault` means that candidate works. A traceback
ending in `ConnectionRefusedError`/`URLError` means it doesn't — check the
next candidate down the list. If you're on the default `0.0.0.0` bind and
the first command already prints `200 steamvault`, you are done — skip the
fix below. (If you'd rather probe from vault-core's own shell instead,
vault-core's Alpine/nginx image does have `curl` — but that checks
vault-core's OWN reachability of an address, a related but different
question from what `vault-api` can reach; the commands above check the
right container.)

### Fix it (only needed with a dedicated `VAULT_CORE_BIND`)

Two options, neither baked into `compose.yaml` by default (a wrong default
here would break setups where the container already finds the cache without
it):

1. **Preferred, DNS-independent, and confirmed sufficient on its own:**
   pin `lancache.steamcontent.com` directly via `extra_hosts` on `vault-api`
   only, in a `deploy/compose.override.yaml` you create yourself:
   ```yaml
   services:
     vault-api:
       extra_hosts:
         - "lancache.steamcontent.com:192.168.1.50"   # vault-core's own address
   ```
   **The value must be a plain private IPv4 address, not a hostname** —
   SteamPrefill's own resolution step requires an RFC1918-or-loopback IPv4
   before it ever sends the heartbeat probe, so anything else (a hostname, an
   IPv6 literal) is rejected before it gets that far. Pinning only this one
   name is enough: once cache detection succeeds, SteamPrefill uses the
   resolved IP for every subsequent depot request too (confirmed in this
   project's own testing, WP 0.4: 1272 chunks prefilled through a single
   hosts entry), and vault-core accepts requests under the real CDN Host
   header regardless of which address they arrived on — depot hostnames
   themselves never need to resolve to the cache.
   Then `docker compose -f compose.yaml -f compose.override.yaml up -d`.
2. **Alternative:** point the container's own resolver at your LAN's
   rewriting DNS server instead:
   ```yaml
   services:
     vault-api:
       dns:
         - 192.168.1.50   # your AdGuard Home / Pi-hole / vault-dns address
   ```
   Caveat: this makes vault-api resolve *everything* (Steam login, the
   manifest oracle if enabled, webhook URLs) through that resolver too — the
   `extra_hosts` route above changes nothing except this one hostname, which
   is why it is the preferred fix.

Re-run the heartbeat probe above after either change to confirm it actually
took.

---

## DNS: pick one of three modes

The Steam client has to be told to fetch from your cache instead of Valve's
CDN. `docs/PROJECT_PLAN.md` §10 lists three ways; **`dns/README.md` has
copy-paste instructions for each** — read it, it is the most consequential
configuration decision in this project.

1. **You already run a local DNS server** (AdGuard Home, Pi-hole, dnsmasq,
   Unbound) — *recommended*. Add the rewrite there. One less container.
2. **Bundled `vault-dns`** — for LANs with no DNS server of their own:
   ```bash
   # in .env:
   #   CACHE_IP=192.168.1.50        <- the LAN IP of THIS host
   #   VAULT_DNS_BIND=192.168.1.50  <- publish :53 on that LAN IP only
   docker compose --profile dns up -d
   ```
   Then point your router's DHCP-advertised DNS server at that address.
3. **Hosts-file mode** — a single Windows gaming PC, no DNS server involved.

Whichever you pick: **the AAAA record must be handled too.** If your resolver
answers `AAAA` for `*.steamcontent.com` with Valve's real IPv6 address,
IPv6-capable clients silently bypass the cache entirely — no error, no log
entry, the cache just never gets used. `vault-dns` closes this by design
(`address=` paired with `local=`, ADR-0001 req 6, verified live in the
transcript in this directory). For modes 1 and 3 it is on you; `dns/README.md`
shows exactly what to add and how to verify it with `dig`.

> `VAULT_RESOLVER` (vault-core's own upstream resolver) must **never** point at
> **any** resolver that rewrites `*.steamcontent.com` to this cache. `vault-dns`
> is the obvious case — it answers `*.steamcontent.com` with vault-core's own
> address by design — but the identical failure hits an **AdGuard Home or
> Pi-hole instance running on this same host** if you configured the
> `*.steamcontent.com` rewrite there instead (mode 1 above; a very common
> homelab layout — AdGuard Home/Pi-hole and this stack side by side on a NAS
> or a small server). Point `VAULT_RESOLVER` at that resolver and vault-core
> would proxy every cache miss back into itself, indistinguishable from a
> hung upstream from the outside. `deploy/.env.example` carries the same
> warning next to the setting itself.

---

## Port 80 and the dedicated-IP question

vault-core **must** answer on port 80. Steam CDN traffic is plain HTTP and the
Steam client only ever asks for port 80 — a cache on another port is a cache
nothing uses. `VAULT_CORE_PORT` exists for testing, not as a way out of a port
clash.

If something else on this host already owns port 80 (another reverse proxy, a
web UI), give SteamVault **its own address** instead
(`docs/PROJECT_PLAN.md` §10):

```bash
# IP alias on the existing NIC (persist it the way your distro does)
sudo ip addr add 192.168.1.50/24 dev eth0

# deploy/.env
VAULT_CORE_BIND=192.168.1.50
```

…then point your DNS rewrite (or `CACHE_IP`) at `192.168.1.50`. A macvlan
network or a dedicated VLAN interface works equally well.

---

## Volumes and backup

Three named volumes, created automatically (a fourth location,
`vault-cache`, becomes a bind mount instead if `VAULT_CACHE_PATH` is set --
see ["Using a dedicated cache mount"](#using-a-dedicated-cache-mount) above):

| Volume               | Mounted at                 | Contains | Back up? |
|----------------------|----------------------------|----------|----------|
| `vault-cache` (or `VAULT_CACHE_PATH` if set) | `/vault` in **both** vault-core and vault-api | the depot cache (`cache/depot/…`) plus nginx's `tmp/` | **No** — it is a cache; large, and re-fillable by prefilling again |
| `vault-db`           | `/data` in vault-api       | `vault.db` — depot→app mapping, jobs, agent reports | **Yes** — small, and it is the knowledge the cache cannot rebuild |
| `vault-steamprefill` | `/opt/steamprefill/Config` in vault-api | SteamPrefill's Steam **session** and selection state | **Yes**, and treat it as a secret |
| `vault-steamprefill-home` | `/opt/steamprefill/home` in vault-api | `HOME` for the container user — SteamPrefill's manifest/depot cache | **No** — regenerable, and it grows |

```bash
# back up the two small ones
docker run --rm -v steamvault_vault-db:/data:ro \
                -v steamvault_vault-steamprefill:/cfg:ro \
                -v "$PWD:/backup" alpine:3.23.5 \
                tar czf /backup/steamvault-state-$(date +%F).tar.gz /data /cfg
```

### Why SteamPrefill gets a HOME volume

SteamPrefill creates a directory under `$HOME` in a **static constructor**,
before it parses a single argument. The usual service-account idiom
(`--home-dir /nonexistent`) therefore does not merely break login — it kills
every invocation, including prefill jobs, with a
`TypeInitializationException` and no useful message. That was a real defect in
this package's first build, caught in review and fixed by giving uid 101 a
genuine home in both the passwd entry and `ENV HOME`, backed by its own volume
so the cache it builds there survives restarts.

Two practical consequences:

- **Don't override `HOME`** for `vault-api` in `.env` or an override file, and
  don't drop the `vault-steamprefill-home` mount. The image asserts both
  definitions agree at build time, and `deploy/tests/verify-stack.sh` re-checks
  it plus a credential-free SteamPrefill smoke run on all three invocation
  paths.
- **It is safe to delete this volume** to reclaim space; SteamPrefill rebuilds
  it. Deleting `vault-steamprefill` instead logs you out — that is the one you
  back up.

### One volume for cache/ and tmp/ — not negotiable

`vault-cache` is mounted as a **single** volume at `/vault`, containing both
`cache/` and `tmp/`. nginx's `proxy_store` finishes every cached object by
`rename()`-ing it out of `tmp/` into `cache/depot/…`, which is atomic only
within one filesystem; split across two mounts it degrades to a full copy —
slower, and briefly double the disk usage per chunk
(`core/README.md`, "Same-filesystem requirement").

You do not have to remember this: vault-core compares the two directories'
`st_dev` at every start and **refuses to boot** if they differ.

### Using a dedicated cache mount

To put the cache on a specific disk -- a second drive, a NAS's own storage
pool, anything other than wherever Docker keeps its named volumes -- set
`VAULT_CACHE_PATH` in `deploy/.env` (`.env.example` documents it in full).
No `compose.yaml` edit needed: both services' `/vault` mount is already
`${VAULT_CACHE_PATH:-vault-cache}:/vault`, and Compose's volume short-syntax
resolves a bare name (the default, unset case) as the named volume declared
under `volumes:` and an absolute path as a bind mount to that path instead --
so leaving the variable unset is byte-for-byte what this line always was.

```bash
# deploy/.env
VAULT_CACHE_PATH=/srv/steamvault-cache
```

Prepare the directory **before the first start** -- a bind mount, unlike a
fresh named volume, does not get seeded with the image's pre-created
`cache/depot/` and `tmp/` (that seeding only happens for an empty named
volume; see `core/Dockerfile`'s `VOLUME ["/vault"]` step). Skipping this
step is not silent: vault-core's preflight will refuse to start with
`/vault/cache is missing`.

```bash
sudo mkdir -p /srv/steamvault-cache/cache/depot /srv/steamvault-cache/tmp
sudo chown -R 101:101 /srv/steamvault-cache
```

**uid/gid 101 is required, not a suggestion.** It is the numeric identity of
the nginx image's worker user, and vault-api's container user is created with
the same numbers so both services can write the shared cache. Named volumes
get this right automatically; bind mounts do not. If you get it wrong,
vault-core refuses to start and tells you the exact `chown` to run.

**`VAULT_CACHE_PATH` must be an absolute path** (start with `/`). Compose
treats anything else as a *named-volume reference* rather than a bind path;
since only `vault-cache` is declared under the top-level `volumes:` key, a
typo here fails loudly at `docker compose config`/`up` time (`refers to
undefined volume ...: invalid compose project`), not silently.

It always covers `cache/` **and** `tmp/` together, because it redirects the
single `/vault` mount point both services already share -- the
same-filesystem requirement below is only satisfiable by moving both at
once, and there is no way to move just one with this variable.

**TrueNAS SCALE + Dockge users:** `deploy/examples/truenas-scale-dockge.md`
has the full recipe for putting this on a dedicated ZFS dataset, including
`recordsize`/`atime`/`compression` reasoning specific to Steam depot chunks
and the port-80/DNS gotchas that come up on a NAS specifically.

---

## Phase-3 knobs: cache-event log and garbage collection

Four Phase-3 settings, documented in full in `.env.example`:

| Variable               | Required | Default                                    | Purpose                                                            |
|-------------------------|----------|---------------------------------------------|---------------------------------------------------------------------|
| `VAULT_EVENT_LOG`       | no       | `/vault/logs/event.log` (**on** by default as of the 2026-08-17 packaging WP) | vault-core: path to the machine-readable cache-event log (WP 3.10, ADR-0008) — its WRITE side |
| `VAULT_EVENT_LOG_PATH`  | no       | `/vault/logs/event.log` (**on** by default, same WP) | vault-api: the SAME path — its READ side. WP 3.11's sweeper tails it to drive miss-triggered prefill completion, per-client hit stats and bypass detection (requirement A12). Must equal `VAULT_EVENT_LOG` above |
| `VAULT_GC_GRACE_DAYS`   | no       | `14`         | vault-api: days a freshly stored chunk is protected from garbage collection purely by its own store time (protects beta-branch/demo content GC cannot otherwise see); `0` disables the window. See `api/README.md` "The recently-stored grace window" |
| `VAULT_AUTO_GC`         | no       | `off`        | vault-api: `off` \| `dry-run` \| `execute` — automatically queue a GC job after a prefill that actually updated something. See `api/README.md` "Auto-GC" |

A fifth, `VAULT_MANIFEST_ORACLE` (WP 3.9), stays off by default and is
**not** in this table on purpose — see `.env.example`'s privacy note before
touching it: enabling it sends outbound queries to a third party.

**The cache-event log is now the feed for a real feature, and needs no extra
volume.** `VAULT_EVENT_LOG` writes into `/vault/logs/`, which lives on the
exact same `/vault` volume `cache/` and `tmp/` already share —
`core/Dockerfile` pre-creates it there. vault-api's matching
`VAULT_EVENT_LOG_PATH` reads from that identical volume (see the "Volumes"
table above) with zero extra `compose.yaml` wiring. It now feeds WP 3.11's
sweeper: miss-triggered prefill completion (a cache miss on an
unknown/partial app queues a prefill job for it), per-client hit statistics,
and bypass detection (`GET /v1/clients`, `GET /v1/stats`) — this was
groundwork with no consumer through WP 3.10, and stayed off-by-default
UNTIL the packaging work package that closed the actual
`deploy/compose.yaml` forwarding gap (`VAULT_EVENT_LOG_PATH` existed in
`config.py` since WP 3.11 but was never wired into vault-api's
`environment:` block, so the whole feature was unreachable in the shipped
stack even though the code was correct — see `docs/LEARNINGS.md`
"Containers"). To turn it back off, set BOTH variables to empty in `.env` —
`.env.example` has the exact wording. It genuinely grows over time (one TSV
line per request); `.env.example` also states plainly that vault-api can
read the file but not truncate it, so rotation is on the operator.

**Turning on auto-GC deletes files automatically once you pick `execute`.**
Start with `dry-run` and read a few job logs (`GET /v1/jobs/{id}`) before
trusting `execute` on a deployment you care about — `api/README.md` "Auto-GC"
has the full decision tree for when it fires.

---

## Logs and rotation

All three containers log to stdout/stderr, so `docker compose logs -f` is the
single place to look, and **rotation is the json-file driver's job**:

```yaml
logging:
  driver: json-file
  options: { max-size: "10m", max-file: "5" }
```

That is ~50 MB per service worst case, enforced by the Docker daemon — no
logrotate, no cron job, no `SIGUSR1` reopen dance, and nothing unbounded inside
a container. (This closes the "log rotation is documented but not implemented"
caveat `core/README.md` left open for this work package; the logrotate sketch
there is superseded by this.)

Tune with `VAULT_LOG_MAX_SIZE` / `VAULT_LOG_MAX_FILE` in `.env`. vault-core
writes **one line per depot request**, so a large prefill can churn through
10 MB quickly — raise `max-file` if you want that history to survive.

`vault-dns` deliberately does **not** log queries. It is your LAN's forwarding
resolver for *every* domain, so query logging would record full
browsing-metadata-level history for every device. See `dns/README.md`
("Privacy note") for how to enable it temporarily when debugging.

---

## Upgrading

```bash
cd deploy
git pull
docker compose up -d --build
```

**Newly-enforced `.env` keys (2026-08-17 packaging work package).** A dozen
settings that Compose used to silently drop — set them in `.env` and nothing
happened, no error, no effect — are forwarded and validated now (the full
list is in `.env.example`'s upgrade note and `docs/PROJECT_PLAN.md` §7
Phase 5). If your existing `.env` already has a stale or malformed value for
one of them, it was harmless before this upgrade and becomes vault-api
refusing to start, with an explicit error naming the bad key, after it.
Check `docker compose logs vault-api` for exactly that message if a
previously-working `.env` suddenly fails to start post-upgrade.

**Database schema.** vault-api creates and upgrades its schema itself at
startup (`init_db`, `api/README.md` "Database schema"). Every change so far is
additive and applied with `CREATE … IF NOT EXISTS`, so a newer image simply
brings the existing `vault.db` up to date and records the new
`schema_version`. There is nothing to run by hand.

**Rolling back is the direction that bites.** If a database has been upgraded
to `schema_version` N and you then start an *older* image that only knows
N-1, vault-api raises `RuntimeError` and refuses to start rather than operate
on a schema it does not understand. That is intentional (silent data damage is
worse than a failed start), but it means: **back up the `vault-db` volume
before an upgrade** if you might want to roll back.

**Do not scale vault-api.** Exactly one process may own the database: it runs a
single job worker and, at startup, fails any job still marked `running` as a
crash orphan — a second instance would kill the first one's live prefill
(`api/README.md` "Worker lifecycle"). `docker compose up --scale vault-api=2`
is not supported.

Bumping a base image or the pinned SteamPrefill release is a deliberate edit to
the relevant `Dockerfile` (tag **and** digest together) — nothing here tracks
a floating tag.

---

## Security posture

What this deployment assumes, stated plainly so it can be checked:

- **vault-core has no authentication and cannot have any.** The Steam client
  can't present a credential. Its only protections are that it serves nothing
  but stored depot chunks, and that a cache *miss* will only ever connect
  upstream to a `*.steamcontent.com` / `*.steamserver.net` host (the Host
  allowlist, ADR-0001 req 4 — this is what stops it being an open HTTP proxy).
  **Never port-forward it, never put it behind a public reverse proxy**
  (`docs/PROJECT_PLAN.md` §10).
- **vault-api is API-key authenticated on every route except `/v1/health`**,
  which returns a fixed body and exists so external monitoring can poll it.
  For access from outside the LAN use Tailscale, Twingate, or your own TLS
  reverse proxy with forward-auth on top of the key — never a bare port
  forward.
- **vault-dns is an open resolver if you publish it wrong.** It forwards
  arbitrary queries upstream with no source-address ACL, which is fine on a
  trusted LAN and a DNS amplification/reflection weapon on the internet.
  Publish it on one specific LAN IP (`VAULT_DNS_BIND=192.168.1.50`), never on
  `0.0.0.0`. If you leave the variable unset it publishes on `127.0.0.1`, i.e.
  it fails *closed* — visibly broken rather than invisibly dangerous.
- **No secrets in `compose.yaml`.** `VAULT_API_KEY` appears only as a required
  `${…}` reference; the real value lives in `deploy/.env`, which is gitignored.
- All three services run with `no-new-privileges`; vault-api drops **all**
  Linux capabilities and runs as uid 101; vault-dns keeps only the four it
  needs to bind :53 and drop privileges. vault-core's nginx master needs root
  to bind :80 and runs its workers as uid 101.

---

## Verifying a deployment

```bash
sudo sh deploy/tests/verify-stack.sh
```

Builds all three images and runs 109 checks against real containers: the
config-drift contract (both directions), **the web UI baked into the
vault-api image and served from it with no bind mount involved** (packaging
work package), all twelve env-forwarding-audit keys (`VAULT_EVENT_LOG_PATH`,
`VAULT_MANIFEST_ORACLE` and the ten more B1 found — see §7 Phase 5 in
`docs/PROJECT_PLAN.md` for the full list) actually reaching vault-api's
process environment with the correct default (not just rendering in the
YAML), a **real Steam CDN** cache MISS → stored → HIT with byte-identical
bodies, the LanCache heartbeat, the Host allowlist, the `?nocache=1` bypass,
API auth and a mapping round-trip, vault-api reading the same cache volume
vault-core just wrote, DNS A/AAAA behaviour, a **credential-free SteamPrefill
smoke run on all three invocation paths** (it must reach the username
prompt, never a `TypeInitializationException`), and every fail-fast guard
(split filesystems, empty/injected resolver, unrendered template, unwritable
cache, missing/invalid `CACHE_IP`).

**Historical result (2026-08-17 packaging work package, three real runs
across two review rounds):** 105/109 pass on the final run; the 4 failures
were step 5i's own timing bug (nginx's event-log buffer flushes after 5 s,
the step checked immediately, and this was reproducible rather than
strictly deterministic — see "Requirements" above). Everything else,
including every check the packaging work package added across both rounds,
was green.

**Fixed in WP 4g (2026-08-18):** step 5i now waits for the event-log line
with a bounded poll (up to 10 s — the 5 s flush plus scheduling slack)
instead of grepping immediately, and fails loudly (with a message that
distinguishes "the line never arrived" from "the line arrived but didn't
parse") if the line still hasn't shown up when the deadline passes — see
`verify-stack.sh`'s comment above step 5i. **Measured, not expected:** a
full run passes **109/109**, exit 0, with clean teardown — confirmed twice
on 2026-08-18 (the fixing run and an independent review re-run) against
Docker Engine 29.1.3 / Compose 2.40.3, with the event line arriving after
~4 s. Note the wait bound is 10 polls, each preceded by a
`docker compose exec` round trip, so the effective window is 2-4x the 5 s
flush and widens on exactly the slow hosts that need it.

It never enters credentials — reaching the login prompt is the pass condition.

It uses its own Compose project name and loopback-only, non-default ports, so
it cannot touch a running deployment, and it removes its own containers and
volumes afterwards. A recorded run is in `VERIFICATION-*.md` in this directory.

Component-level tests live with their components: `core/tests/test-core.ps1`,
`api/tests/` (pytest), `dns/tests/test-dnsmasq-config.ps1`, and
`core/docker/check-config-drift.sh`.

**One check `verify-stack.sh` deliberately does NOT cover** (it never enters
Steam credentials and this trap only shows up with a dedicated
`VAULT_CORE_BIND`, which the suite doesn't use): the SteamPrefill
cache-detection trap above. **Only relevant if you set `VAULT_CORE_BIND` to
a dedicated address** — the default `0.0.0.0` bind already works, measured
(see ["A container-specific
trap"](#a-container-specific-trap-does-steamprefill-actually-reach-your-cache)
above for why). If you did set a dedicated bind, add the heartbeat probe
from that section to your own post-deploy checklist — a DNS lookup answers
the wrong question here, since the mechanism that actually matters in this
layout is which address the heartbeat reaches, not what `lancache.
steamcontent.com` resolves to.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `required variable VAULT_API_KEY is missing a value` | `.env` missing or the key not set. Copy `.env.example`. |
| vault-core exits at boot with `FATAL: … DIFFERENT filesystems` | `cache/` and `tmp/` were split across two mounts. Mount one volume at `/vault`. |
| vault-core exits with `FATAL: … not writable by the nginx worker user` | bind-mounted cache directory not owned by `101:101`. |
| vault-core exits with `FATAL: /vault/cache is missing` | `VAULT_CACHE_PATH` is set but `<path>/cache/depot` and `<path>/tmp` weren't created first — a bind mount isn't seeded the way a fresh named volume is. See ["Using a dedicated cache mount"](#using-a-dedicated-cache-mount). |
| `docker compose config`/`up` fails with `refers to undefined volume ...: invalid compose project` | `VAULT_CACHE_PATH` doesn't start with `/` — Compose parsed it as a named-volume reference instead of a bind path. Use an absolute path. |
| vault-dns exits with `FATAL: CACHE_IP is not set` | the `dns` profile is enabled but `CACHE_IP` is empty in `.env`. |
| Clients download at internet speed and the cache stays empty | DNS redirection isn't reaching them, or the AAAA leak is open. Check with `dig A` **and** `dig AAAA` against your resolver (`dns/README.md`). |
| Prefill jobs fail with "A Steam account is required" | the one-time interactive login hasn't been done — see [First run](#first-run-the-one-time-steamprefill-login). |
| Port 80 already in use on the host | use a dedicated IP, not a different port — see [Port 80](#port-80-and-the-dedicated-ip-question). |
