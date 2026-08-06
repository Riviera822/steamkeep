# Dedicated cache mount (`VAULT_CACHE_PATH`) — verification, 2026-08-06 08:12 UTC

Evidence for the deploy/ follow-up work package: a first-class `VAULT_CACHE_PATH`
knob in `deploy/compose.yaml` / `deploy/.env.example`, plus
`deploy/examples/truenas-scale-dockge.md`. This file supplements
`VERIFICATION-20260805-214238.md` (the WP 1.9 baseline, still the source of
truth for everything *not* related to the cache-path knob) rather than
replacing it.

## Environment

| | |
|---|---|
| Host | Ubuntu 26.04 LTS on WSL2 (`6.6.87.2-microsoft-standard-WSL2 x86_64`) |
| Docker Engine | 29.1.3 |
| Docker Compose | 2.40.3 |
| Repo | `/mnt/c/claude-dev/SteamVault` |

## What changed under test

`deploy/compose.yaml`'s two `/vault` volume mount lines (vault-core,
vault-api) changed from the literal `vault-cache:/vault` to
`${VAULT_CACHE_PATH:-vault-cache}:/vault`. Compose's short-syntax volume
source disambiguates a bare name (still a named-volume reference, resolved
against the unchanged top-level `volumes: vault-cache:` entry) from a path
starting with `/` (a bind mount) — no override file, no second compose file,
no change to `core/` or `api/`.

## 1. Named-volume mode (VAULT_CACHE_PATH unset) — full regression

Ran the complete, unmodified `deploy/tests/verify-stack.sh` against
`deploy/compose.yaml` with the cache-path change applied and
`VAULT_CACHE_PATH` never set (the default for every existing deployment).

```
$ docker compose ... config   (excerpt)
      volumes:
        - type: volume
          source: vault-cache
          target: /vault
          volume: {}
```

Byte-identical to the pre-change rendering — confirming the "zero changes for
named-volume users" requirement, not just asserting it.

**Result: 62 checks passed, 0 failed — `ALL CHECKS PASSED`.** Same count as
the WP 1.9 baseline (`VERIFICATION-20260805-214238.md`); every existing
check (config drift, image builds, the real Steam-CDN MISS→HIT→byte-identity
chain, vault-api auth/mapping/cache-summary, vault-dns A/AAAA/fail-fast, and
every existing fail-fast guard) still passes unchanged.

Full transcript retained locally during this session
(`deploy/_verify-named.tmp.log`, not committed — same throwaway nature as the
existing `VERIFICATION-*.md` files' source runs). Headline:

```
## 9. Result
checks passed: 62
checks failed: 0
ALL CHECKS PASSED
```

## 2. Bind-mode smoke test (VAULT_CACHE_PATH set)

Host dir prepared exactly per the documented bind-mount recipe (a bind mount
does **not** get the image's seeded `cache/depot`/`tmp` — that seeding is a
named-volume-only behavior of a fresh, empty volume; deploy/README.md /
deploy/examples/truenas-scale-dockge.md both state this as a required manual
step, and this run re-confirms it: skipping it once during this verification
reproduced `40-vault-preflight.sh: FATAL: /vault/cache is missing` exactly as
documented, before the fix below):

```
mkdir -p <hostdir>/cache/depot <hostdir>/tmp
chown -R 101:101 <hostdir>
# deploy/.env:
VAULT_CACHE_PATH=<hostdir>
```

`docker compose up -d` (isolated project, loopback ports 8190/8191, same
pattern as `verify-stack.sh`):

```
40-vault-preflight.sh: upstream resolver (ADR-0001 req 4): 1.1.1.1
40-vault-preflight.sh: preflight OK
```

(`cache/` and `tmp/` share one filesystem because they are both under the one
bind-mounted directory — the preflight's `st_dev` check passes for the same
reason it always does.)

Real Steam CDN object (depot `70403`, chunk
`773d10050d99b2544665873ec2125b3bf273e8b2`, the same known-good object
`core/tests/test-core.ps1` and `deploy/tests/verify-stack.sh` use):

```
MISS http=200 bytes=999232
HIT  http=200 bytes=999232
sha256(MISS) = sha256(HIT) = c78fb9f8a88318dd61f318bb95f0b59911c9bbbf8678f6ef2d2724cdbc56a66c
```

**The file landed on the actual host filesystem**, not inside a Docker
volume:

```
$ find <hostdir> -maxdepth 4
<hostdir>/cache/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2
$ sha256sum <hostdir>/cache/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2
c78fb9f8a88318dd61f318bb95f0b59911c9bbbf8678f6ef2d2724cdbc56a66c
```

vault-api, mounted at the identical `VAULT_CACHE_PATH`, reads the same bytes:

```
GET /v1/cache/summary ->
{"total_bytes":999232,"top_consumers":[],"unmapped_depots":{"count":1,"size_bytes":999232},"free_disk_bytes":1015890276352}
```

`docker compose down -v` afterwards removed the containers and the *other*
named volumes (db, steamprefill, steamprefill-home) but — correctly — left
the bind-mounted host directory and its data untouched (it is not a Docker
volume, so `-v` cannot and should not touch it):

```
$ find <hostdir> -maxdepth 4   # after `down -v`
<hostdir>/cache/depot/70403/chunk/773d10050d99b2544665873ec2125b3bf273e8b2   (still there)
```

## 3. Negative test: tmp/ deliberately on a different filesystem

Same bind-mounted host directory, but this time with a `tmpfs` layered over
just the `tmp/` subdirectory before starting the container — exactly what an
operator accidentally bind-mounting a *second* disk under `<VAULT_CACHE_PATH>/tmp`
would produce, and exactly the scenario `deploy/tests/verify-stack.sh` step
8a already covers for the *named-volume* case. This run repeats it for the
*bind-mount* case specifically, since that is the new code path this WP adds.

```
$ stat -c '%n st_dev=%d' <hostdir>/cache <hostdir>/tmp   # before
<hostdir>/cache st_dev=2096
<hostdir>/tmp   st_dev=2096

$ docker run --rm -v <hostdir>:/vault --tmpfs /vault/tmp steamvault/vault-core:0.1.0
40-vault-preflight.sh: upstream resolver (ADR-0001 req 4): 1.1.1.1
40-vault-preflight.sh: FATAL: /vault/cache (st_dev=2096) and /vault/tmp (st_dev=100) are on DIFFERENT
  filesystems. proxy_store finishes every cached object by rename()-ing it from
  tmp/ into cache/depot/..., which only works within one filesystem; across two
  it falls back to a full copy (slower, briefly doubles disk usage per chunk).
  Mount ONE volume at /vault instead of separate mounts for cache/ and tmp/.
  See core/README.md 'Same-filesystem requirement'.
exit=1
```

**PASS** — the container refuses to start, loudly, with the exact same
message and guard as the named-volume case. `40-vault-preflight.sh` was not
touched by this work package; this confirms the existing guard needs no
changes to keep protecting the new bind-mount path (both are, from the
preflight's point of view, just "whatever got mounted at `/vault`").

## Result

| Test | Outcome |
|---|---|
| Named-volume mode, full `verify-stack.sh` | 62/62 PASS, unchanged from baseline |
| Bind-mode smoke: preflight, real CDN MISS→HIT, host-file placement, vault-api read | PASS |
| Bind-mode negative: split cache/tmp filesystems | Refused at boot (PASS) |
