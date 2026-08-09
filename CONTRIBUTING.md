# Contributing to SteamVault

SteamVault is a self-hosted Steam game cache with true per-game management —
see [`docs/PROJECT_PLAN.md`](docs/PROJECT_PLAN.md) for the full vision,
architecture, and phase plan, and [`docs/adr/`](docs/adr) for the binding
architecture decisions (ADRs) that plan builds on. **Read the plan before your
first PR** — it explains *why* the project is shaped the way it is (e.g. why
the cache uses `proxy_store` instead of a generic HTTP cache, why the scope is
deliberately Steam-only) and will save you from proposing something that was
already deliberately rejected.

If a change you want to make would deviate from a decision recorded in an ADR
(e.g. the storage layout, the credential-handling boundary, the Go-vs-Python
choice for the agent), **open an issue and discuss it first**. A new ADR is
the right way to change a decided architecture — a PR that silently
contradicts one will be asked to start with that discussion instead.

## Before you start

- **Check `docs/PROJECT_PLAN.md`'s phase plan** for what's already done, in
  progress, or deliberately deferred — a checkbox still unticked usually means
  "not built yet," not "rejected."
- **For anything beyond a small, obviously-correct fix** (typo, an off-by-one,
  a clearly missing test), open an issue first describing what you want to
  change and why. This avoids wasted work on a PR that turns out to conflict
  with an ADR or an in-flight package.
- **One logical change per PR.** A PR that mixes a bug fix with an unrelated
  refactor is harder to review and harder to revert if something goes wrong.

## Repository layout

```
steamvault/
├── core/            # nginx config, Dockerfile (the cache itself)
├── dns/             # optional dnsmasq container (Compose profile)
├── api/             # FastAPI, SQLite schema, scheduler
├── agent/           # PC listener (Windows/Linux/SteamOS, Go)
├── web/             # browser UI (Phase 4a) — not started yet
├── app/             # Android (Kotlin + Go tsnet module) — not started yet
├── deploy/          # compose.yaml, example .env, DNS mode docs
├── docs/            # architecture, ADRs, setup guides
├── poc/             # Phase-0 feasibility PoC, frozen as evidence — read-only
└── .github/         # CI, issue/PR templates
```

Each component directory has its own `README.md` with implementation detail —
read the relevant one before touching that component's code.

## Development setup and running tests

All code, comments, commit messages, and documentation in this repository are
**English-only** — no exceptions, regardless of what language an issue or
discussion happens to be filed in.

The table below is honest about what runs where: several component test
suites need a real network path to the Steam CDN, a Windows nginx binary, or a
Docker daemon, and cannot run in an ordinary CI container. Those are marked
**local-only** — expect to run them yourself before opening a PR that touches
the component, and describe what you ran in the PR description.

| Component | Language / tooling | Test command | Notes |
|---|---|---|---|
| `api/` | Python 3.12+ (the Docker image pins 3.13), FastAPI, plain `sqlite3` (no ORM) | `cd api && pip install -r requirements.txt -r requirements-dev.txt`, then `pytest` | Runs fully offline, no Docker needed. Verified for this document: `pytest` from `api/` passes (704 passed, 1 skipped) on Python 3.12 against a clean checkout. This is what CI will run (see the CI note below). |
| `agent/` | Go (module targets windows/amd64, linux/amd64, linux/arm64) | `cd agent/go && go build ./... && go vet ./... && gofmt -l . && go test ./...` | Runs fully offline. Verified for this document: builds and `go test ./...` passes on all six packages. **Windows-checkout caveat, verified while writing this doc:** if your local clone has `core.autocrlf=true` (common on Windows), `gofmt -l .` will list every `.go` file — that's `gofmt`'s LF-normalization disagreeing with your checkout's CRLF line endings, not a real style violation. Judge `gofmt` output from a checkout with `autocrlf=input` or `false` (or run it inside WSL/Linux against a Unix-checked-out copy) if you need a meaningful signal. This is what CI will run, on both Linux and Windows (see the CI note below). |
| `core/` | nginx config (no code, just config + comments) | `core/tests/test-core.ps1` (PowerShell) | **Local-only.** Sends real requests to the live Steam CDN and needs the Windows nginx binary from `poc/` (see `core/README.md`). CI will only run `nginx -t` against the rendered config template — it will not run this suite. |
| `dns/` | dnsmasq config template | `dns/tests/test-dnsmasq-config.ps1` (PowerShell, drives WSL2) | **Local-only.** Needs PowerShell plus a WSL2 distro with `dnsmasq` installed (see `dns/README.md`). Won't run in CI. |
| `deploy/` | Docker Compose | `sudo sh deploy/tests/verify-stack.sh` | **Local-only.** Builds and runs all three containers for real, including a real Steam CDN MISS→HIT cycle; needs a working Docker Engine with Compose v2 and root (or a `docker` group membership) on the host. Won't run in CI — no image publishing or container runtime will be exercised there. |

**CI note:** GitHub Actions workflows under `.github/workflows/` are not in
this repository yet — they land shortly with WP 5.1 (tracked in
`docs/WORKPACKAGES.md`). Once they do, CI is scoped to be
intentionally narrower than "everything above": it runs the `api/` pytest
suite, `agent/` `go build`/`go vet`/`go test` on Linux and Windows,
`nginx -t` against the rendered `core/` templates, and PowerShell 5.1 syntax
checks for the packaging scripts under `agent/packaging/windows/`. Anything
network- or Docker-dependent is deliberately kept local-only rather than
faked in CI — see the table above, and don't be surprised if a CI-green PR
still needs one of the local-only suites run by hand before a component
maintainer approves it.

### A note on style

- **Python:** type hints throughout, plain `sqlite3` with small helper
  functions — no ORM magic (see any file under `api/vault_api/` for the house
  style). Prefer readability over cleverness; this is a project other
  homelabbers should be able to read end to end.
- **Go:** `gofmt`-clean (see the CRLF caveat above), `go vet`-clean, no new
  runtime dependencies without discussion first (`agent/` is deliberately
  dependency-free per ADR-0005 — a static, trivially cross-compiled binary is
  part of the design, not an accident).
- **Docker:** pinned image tags (and, where already established, digests) —
  never `latest` or a floating release tag. `json-file` logging with bounded
  `max-size`/`max-file` on every service, matching `deploy/compose.yaml`'s
  existing pattern.
- **Every feature ships with at least one test proving it works.** A PR
  without a test for its own change will be asked to add one.

### Parsers and input validation

If your change touches anything that parses external input (Steam manifest/
ACF/VDF files, HTTP request bodies, config values that get substituted into a
config file or a filesystem path, subprocess output), the findings in
[`docs/LEARNINGS.md`](docs/LEARNINGS.md) are **binding**, not optional
background reading — they were each paid for with a real bug (e.g. recursive
parsers need an explicit depth limit, `int()`-like coercions need explicit
digit/ASCII validation before the value reaches SQL/a path/another language,
Pydantic's lax-mode `bool`→`int` coercion needs an explicit rejection on ID
fields). A PR that reintroduces one of these is expected to fail review.

## Commit and PR conventions

- **[Conventional Commits](https://www.conventionalcommits.org/):**
  `feat(api): ...`, `fix(agent): ...`, `docs: ...`, `chore(deploy): ...`, and
  so on. Look at `git log` for the established scope names per component
  (`core`, `api`, `agent`, `dns`, `deploy`, `docs`).
- **One logical change per PR** (see "Before you start" above).
- Update the relevant component `README.md` and, if you touched a checkbox
  item, `docs/PROJECT_PLAN.md` in the same PR — documentation drift is a bug.

## Reporting security issues

**Do not open a public issue for a security vulnerability.** This project
ships a `SECURITY.md` with the first tagged release describing the disclosure
process in full; until it lands, please use
[GitHub's private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing/privately-reporting-a-security-vulnerability)
on this repository instead of a regular issue. Ordinary bugs (a crash, a wrong
result, a confusing error message) that don't expose a security risk are fine
as normal issues — see the bug report template.

## License

SteamVault is licensed under [Apache-2.0](LICENSE). By submitting a
contribution, you agree it is licensed under the same terms
(inbound = outbound) — there is no separate Contributor License Agreement
(CLA) to sign.
