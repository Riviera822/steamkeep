# Tuned setup: dedicated cache disk, scheduler cadence, GC grace window

Start from [`minimal-lan.md`](minimal-lan.md) or your existing install, then
apply the pieces below once the default setup is working. Each section is
independent — take what you need.

## 1. Dedicated cache disk/dataset (works out of the box)

The cache defaults to a plain Docker-managed named volume. To put it on a
specific disk or dataset instead — a second drive, a NAS storage pool, a ZFS
dataset tuned for ~1 MiB Steam chunks — set one variable in `deploy/.env`:

```bash
# deploy/.env
VAULT_CACHE_PATH=/srv/steamvault-cache
```

`deploy/compose.yaml` already resolves both services' `/vault` mount from
this variable (`${VAULT_CACHE_PATH:-vault-cache}:/vault`), so no
`compose.yaml` edit is needed — this is the one setting in this document that
is fully wired end to end today.

Before the first start, create the directory and hand it to the nginx worker
user (uid/gid 101 — not a placeholder, see
[`deploy/README.md`](../README.md#using-a-dedicated-cache-mount)):

```bash
sudo mkdir -p /srv/steamvault-cache/cache/depot /srv/steamvault-cache/tmp
sudo chown -R 101:101 /srv/steamvault-cache
```

If you're on TrueNAS SCALE with ZFS specifically,
[`truenas-scale-dockge.md`](truenas-scale-dockge.md) has the full dataset
recipe (`recordsize=1M`, `atime=off`, `compression=off`, and the measured
reasoning behind each — Steam chunks are already Valve-compressed, so ZFS
compression buys nothing here).

## 2. Scheduler cadence and GC grace window (needs one extra step)

`vault-api` reads four more settings that control when the scheduler sweeps
installed-app lists and how long a freshly-stored chunk is protected from
garbage collection — all documented in
[`api/README.md`](../../api/README.md) (see "Scheduler" and "Garbage
collection → The recently-stored grace window"):

| Variable | Meaning | Default |
|---|---|---|
| `VAULT_SCHEDULE_WINDOW` | Daytime window the scheduler sweeps in, `HH:MM-HH:MM` server-local time (overnight windows like `22:00-06:00` are supported). Empty = scheduler off. | *(empty — off)* |
| `VAULT_SCHEDULE_INTERVAL_MINUTES` | Minimum spacing between two sweeps. | `180` |
| `VAULT_SCHEDULE_CLIENT_STALE_DAYS` | A client whose newest agent report is older than this drops out of the sweep's target set. | `7` |
| `VAULT_GC_GRACE_DAYS` | Days a freshly-stored chunk is protected from garbage collection, by store time. `0` disables the window. | `14` |

**Honest gap, found while writing this document:** these four are real
`vault_api/config.py` settings, read directly from the process environment —
but as of this writing, `deploy/compose.yaml`'s `vault-api` service does
**not** forward them from `deploy/.env` the way it forwards
`VAULT_API_KEY`/`VAULT_LOG_LEVEL`/`VAULT_PREFILL_TIMEOUT_SECONDS`/
`VAULT_WORKER_POLL_SECONDS`/`VAULT_SIZE_CACHE_TTL`/`VAULT_AGENT_REPORT_KEEP`.
Compose's `.env` file only feeds `${...}` substitutions inside
`compose.yaml` itself — it is not a blanket pass-through into the
container's environment. Setting these four in `deploy/.env` alone
currently has **no effect**; `vault-api` will run with the defaults above
(scheduler off, GC grace window at 14 days) regardless.

Until that wiring lands in `deploy/compose.yaml` itself, the working way to
set them today is a small Compose override file next to your `.env` — this
does not modify the tracked `deploy/compose.yaml`:

```yaml
# deploy/compose.override.yaml (not tracked by git -- your local addition)
services:
  vault-api:
    environment:
      VAULT_SCHEDULE_WINDOW: "09:00-17:00"
      VAULT_SCHEDULE_INTERVAL_MINUTES: "180"
      VAULT_SCHEDULE_CLIENT_STALE_DAYS: "7"
      VAULT_GC_GRACE_DAYS: "14"
```

`docker compose` picks up a file named `compose.override.yaml` (or
`docker-compose.override.yml`) next to `compose.yaml` automatically — no
extra `-f` flag needed:

```bash
cd deploy
docker compose up -d --build
docker compose exec vault-api env | grep -E 'VAULT_SCHEDULE|VAULT_GC_GRACE'   # confirm it took
curl -H "X-Api-Key: $VAULT_API_KEY" http://<server>:8080/v1/schedule          # confirm the scheduler is enabled
```

If you'd rather see this wired into `deploy/compose.yaml` directly so a
plain `.env` edit is enough, that's a good, small, self-contained
contribution — see [`CONTRIBUTING.md`](../../CONTRIBUTING.md).
