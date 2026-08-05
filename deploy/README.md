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
├── tests/
│   └── verify-stack.sh     # container verification suite (see "Verifying")
└── VERIFICATION-*.md       # recorded evidence from a real run
```

---

## Requirements

- Docker Engine with Compose v2 (`docker compose`, not `docker-compose`).
  Verified against **Docker Engine 29.1.3 / Compose 2.40.3** on Ubuntu 26.04.
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
> vault-dns. vault-dns answers `*.steamcontent.com` with vault-core's address,
> so vault-core would proxy every cache miss back into itself.

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

Three named volumes, created automatically:

| Volume               | Mounted at                 | Contains | Back up? |
|----------------------|----------------------------|----------|----------|
| `vault-cache`        | `/vault` in **both** vault-core and vault-api | the depot cache (`cache/depot/…`) plus nginx's `tmp/` | **No** — it is a cache; large, and re-fillable by prefilling again |
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

### Using a bind mount for the cache

To put the cache on a specific disk, replace the volume line for **both**
services and prepare the directory first:

```yaml
    volumes:
      - /srv/steamvault:/vault      # in vault-core AND vault-api
```

```bash
sudo mkdir -p /srv/steamvault/cache/depot /srv/steamvault/tmp
sudo chown -R 101:101 /srv/steamvault
```

**uid/gid 101 is required, not a suggestion.** It is the numeric identity of
the nginx image's worker user, and vault-api's container user is created with
the same numbers so both services can write the shared cache. Named volumes
get this right automatically; bind mounts do not. If you get it wrong,
vault-core refuses to start and tells you the exact `chown` to run.

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

Builds all three images and runs 62 checks against real containers: the
config-drift contract (both directions), a **real Steam CDN** cache
MISS → stored → HIT with byte-identical bodies, the LanCache heartbeat, the
Host allowlist, the `?nocache=1` bypass, API auth and a mapping round-trip,
vault-api reading the same cache volume vault-core just wrote, DNS A/AAAA
behaviour, a **credential-free SteamPrefill smoke run on all three invocation
paths** (it must reach the username prompt, never a
`TypeInitializationException`), and every fail-fast guard (split filesystems,
empty/injected resolver, unrendered template, unwritable cache, missing/invalid
`CACHE_IP`).

It never enters credentials — reaching the login prompt is the pass condition.

It uses its own Compose project name and loopback-only, non-default ports, so
it cannot touch a running deployment, and it removes its own containers and
volumes afterwards. A recorded run is in `VERIFICATION-*.md` in this directory.

Component-level tests live with their components: `core/tests/test-core.ps1`,
`api/tests/` (pytest), `dns/tests/test-dnsmasq-config.ps1`, and
`core/docker/check-config-drift.sh`.

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| `required variable VAULT_API_KEY is missing a value` | `.env` missing or the key not set. Copy `.env.example`. |
| vault-core exits at boot with `FATAL: … DIFFERENT filesystems` | `cache/` and `tmp/` were split across two mounts. Mount one volume at `/vault`. |
| vault-core exits with `FATAL: … not writable by the nginx worker user` | bind-mounted cache directory not owned by `101:101`. |
| vault-dns exits with `FATAL: CACHE_IP is not set` | the `dns` profile is enabled but `CACHE_IP` is empty in `.env`. |
| Clients download at internet speed and the cache stays empty | DNS redirection isn't reaching them, or the AAAA leak is open. Check with `dig A` **and** `dig AAAA` against your resolver (`dns/README.md`). |
| Prefill jobs fail with "A Steam account is required" | the one-time interactive login hasn't been done — see [First run](#first-run-the-one-time-steamprefill-login). |
| Port 80 already in use on the host | use a dedicated IP, not a different port — see [Port 80](#port-80-and-the-dedicated-ip-question). |
