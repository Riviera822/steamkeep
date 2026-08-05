# SteamVault vault-api -- FastAPI + SQLite brain, and the SteamPrefill host
# (Phase 1, WP 1.9).
#
# Build from the repo's api/ directory:
#     docker build -t steamvault/vault-api:0.1.0 api/
# or, normally, via deploy/compose.yaml.
#
# --- Base image pin ----------------------------------------------------------
#   python:3.13.14-slim-trixie
#   digest resolved 2026-08-05 via `docker pull` + `docker inspect --format
#   '{{index .RepoDigests 0}}'`.
# Pinned by exact tag AND digest (docs/PROJECT_PLAN.md §9 / CLAUDE.md: pinned
# tags only). Debian-based rather than Alpine on purpose: the SteamPrefill
# release below is a self-contained .NET binary built against glibc, which
# musl/Alpine cannot run.
#
# --- Stage 1: fetch SteamPrefill ---------------------------------------------
# Kept in a throwaway stage so curl/unzip never exist in the shipped image.
FROM python:3.13.14-slim-trixie@sha256:bf503bb2243c5aad0aa951544dd60d165f992646441d35dea90893703fc26251 AS steamprefill

# Pinned to the exact upstream release Phase 0 validated (ADR-0001: SteamPrefill
# v3.7.1 auto-detected the cache via the lancache-heartbeat contract and
# prefilled path-faithfully).
ARG STEAMPREFILL_VERSION=3.7.1
ARG STEAMPREFILL_ARCHIVE=SteamPrefill-3.7.1-linux-x64.zip
ARG STEAMPREFILL_URL=https://github.com/tpill90/steam-lancache-prefill/releases/download/v3.7.1/SteamPrefill-3.7.1-linux-x64.zip
# HOW THIS CHECKSUM WAS OBTAINED (documented because it matters):
# upstream publishes NO checksum file and no signature alongside its release
# assets -- verified against the GitHub releases API for tag v3.7.1 on
# 2026-08-05, whose asset list contains only the four platform .zip files
# (linux-arm64, linux-x64, osx-x64, win-x64) and nothing else. So this is a
# trust-on-first-use pin, established as follows:
#   1. downloaded https://github.com/tpill90/steam-lancache-prefill/releases/
#      download/v3.7.1/SteamPrefill-3.7.1-linux-x64.zip over HTTPS (GitHub's
#      own release CDN, TLS-verified) on 2026-08-05
#   2. checked the byte count against the size the GitHub releases API reports
#      for that asset: 12095541 bytes -- matched
#   3. sha256sum -> 678765365fd3dacd9394912526a086e15f4f5b27dc6db24ea6ef8dc99e2d3748
#   4. repeated the download independently and re-hashed: same digest
# From here on the pin is what protects the build: any change to the bytes at
# that URL -- upstream re-cutting the tag, a compromised release, a truncated
# download -- fails the build instead of silently shipping. Bump version, URL
# and hash together, never one of them alone.
ARG STEAMPREFILL_SHA256=678765365fd3dacd9394912526a086e15f4f5b27dc6db24ea6ef8dc99e2d3748

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends curl unzip; \
    rm -rf /var/lib/apt/lists/*; \
    curl --fail --silent --show-error --location --retry 3 \
         --output /tmp/steamprefill.zip "$STEAMPREFILL_URL"; \
    echo "${STEAMPREFILL_SHA256}  /tmp/steamprefill.zip" | sha256sum --check --strict -; \
    unzip -q /tmp/steamprefill.zip -d /tmp/steamprefill; \
    mkdir -p /opt/steamprefill; \
    cp "/tmp/steamprefill/SteamPrefill-${STEAMPREFILL_VERSION}-linux-x64/SteamPrefill" \
       /opt/steamprefill/SteamPrefill; \
    chmod 0755 /opt/steamprefill/SteamPrefill; \
    rm -rf /tmp/steamprefill /tmp/steamprefill.zip

# --- Stage 2: the runtime image ----------------------------------------------
FROM python:3.13.14-slim-trixie@sha256:bf503bb2243c5aad0aa951544dd60d165f992646441d35dea90893703fc26251

LABEL org.opencontainers.image.title="SteamVault vault-api" \
      org.opencontainers.image.description="FastAPI + SQLite control plane: prefill orchestration, per-game size and deletion" \
      org.opencontainers.image.source="https://github.com/steamvault/steamvault" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.version="0.1.0"

# libicu is the one native dependency a self-contained .NET application needs
# beyond what python:slim already ships (globalization data). ca-certificates is
# already present in this base image, and SteamPrefill needs it to talk to Steam.
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libicu76; \
    rm -rf /var/lib/apt/lists/*

# uid/gid 101 is NOT arbitrary: it is the numeric identity of the stock nginx
# image's `nginx` user, which vault-core's workers run as. vault-core and
# vault-api share the depot cache volume -- vault-core writes it (proxy_store),
# vault-api reads it (size calculation) and deletes from it
# (DELETE /v1/cache/{appid}) -- so the two containers must agree on ownership.
# Anything else means either a chmod 0777 cache or a service that cannot do its
# job. Named `vault` here for readability; only the number is load-bearing.
#
# HOME MUST BE A REAL, WRITABLE, PERSISTENT DIRECTORY (WP 1.9 review, blocker).
# The first revision used the usual service-account idiom, --home-dir
# /nonexistent, and that broke SteamPrefill completely. Measured, on all three
# invocation paths (docker run, docker compose run, docker compose exec):
#
#   System.TypeInitializationException: The type initializer for
#   'SteamPrefill.Settings.AppConfig' threw an exception.
#     System.UnauthorizedAccessException: Access to the path '/nonexistent' is denied.
#       at DirectoryInfo System.IO.Directory.CreateDirectory(string)
#       at SteamPrefill.Settings.AppConfig..cctor()
#       at bool SteamPrefill.Settings.AppConfig.get_DebugLogs()
#       at List<string> SteamPrefill.Program.ParseHiddenFlags()
#
# It is a STATIC CONSTRUCTOR reached from Program.Main before any argument is
# parsed and before any prompt, so it is not a login problem that only shows up
# once someone tries to log in -- the documented interactive login and every
# prefill job die identically, with a stack trace instead of a usable message.
#
# Two places have to agree, because different invocations derive HOME
# differently (measured: `docker compose run` inherits it from the passwd entry,
# `docker exec` fell back to /root -- unwritable for uid 101 either way):
#   1. the passwd entry            --home-dir below
#   2. the image environment       ENV HOME further down
RUN set -eux; \
    groupadd --system --gid 101 vault; \
    useradd --system --uid 101 --gid 101 --no-create-home \
            --home-dir /opt/steamprefill/home --shell /usr/sbin/nologin vault

# Runtime dependencies only -- requirements-dev.txt (pytest, httpx) is
# deliberately not installed: the test suite runs in CI/dev, not in production.
# --no-cache-dir keeps the wheel cache out of the layer.
COPY requirements.txt /tmp/requirements.txt
RUN set -eux; \
    pip install --no-cache-dir -r /tmp/requirements.txt; \
    rm -f /tmp/requirements.txt

COPY --from=steamprefill /opt/steamprefill/SteamPrefill /opt/steamprefill/SteamPrefill
COPY vault_api /app/vault_api

# Mount points, all owned by the service user:
#   /data                     -> SQLite database (own named volume)
#   /opt/steamprefill/Config  -> SteamPrefill's session + selection state (own
#                                named volume; this is where the Steam login
#                                lives after the one interactive
#                                `docker compose run` -- see deploy/README.md).
#                                SteamPrefill resolves Config/ relative to its
#                                own executable, hence this exact path.
#   /opt/steamprefill/home    -> HOME for uid 101 (own named volume). See the
#                                blocker note above for why this must exist and
#                                be writable at all. It gets its own volume
#                                rather than living inside Config/ because what
#                                lands here is a regenerable manifest/depot
#                                cache that grows, while Config/ holds a Steam
#                                session that is small, secret and worth backing
#                                up -- deliberately different lifecycles, so
#                                "back this up" and "you may delete this" stay
#                                separable advice (deploy/README.md).
#   /vault                    -> the shared depot cache volume (same volume
#                                vault-core mounts). cache/depot is pre-created
#                                with the same layout vault-core's image uses so
#                                that whichever service happens to seed the empty
#                                named volume first produces the same result, and
#                                so vault-api's deletion guard (which refuses a
#                                cache root with no depot/ dir) is satisfied on a
#                                fresh install.
RUN set -eux; \
    mkdir -p /data /opt/steamprefill/Config /opt/steamprefill/home /vault/cache/depot /vault/tmp; \
    chown -R 101:101 /data /opt/steamprefill /vault /app; \
    # Assert the two halves of the HOME fix agree with each other and with the
    # directory that actually exists -- a build-time guard, because getting this
    # wrong is silent until someone tries to prefill.
    test "$(getent passwd 101 | cut -d: -f6)" = "/opt/steamprefill/home"; \
    test "$(stat -c '%u:%g' /opt/steamprefill/home)" = "101:101"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    # Must match the passwd entry above; see the blocker note there for why
    # both are needed. Never point this at /nonexistent, /root, or any path the
    # image does not own as 101:101.
    HOME=/opt/steamprefill/home \
    VAULT_DB_PATH=/data/vault.db \
    VAULT_CACHE_ROOT=/vault/cache \
    VAULT_STEAMPREFILL_PATH=/opt/steamprefill/SteamPrefill \
    DOTNET_CLI_TELEMETRY_OPTOUT=1

# VAULT_API_KEY is deliberately NOT given a default here. vault-api raises
# RuntimeError at startup without it (api/vault_api/config.py), which is the
# intended behaviour: a shipped default key is a shipped vulnerability.
# deploy/compose.yaml makes it a required .env value.

USER 101:101
WORKDIR /app
EXPOSE 8080

# /v1/health is the one unauthenticated route by design (api/README.md "Auth"):
# fixed body, no data, explicitly meant for exactly this kind of probe.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import sys,urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/v1/health', timeout=3).status == 200 else 1)"]

# EXACTLY ONE uvicorn worker, on purpose and permanently. vault-api runs a single
# background job-queue thread per process and repairs orphaned 'running' jobs at
# startup; a second process would fail the first one's genuinely running job and
# run two prefill workers against one SQLite file (api/README.md "Worker
# lifecycle"). Do not add --workers.
CMD ["python", "-m", "uvicorn", "vault_api.main:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8080"]
