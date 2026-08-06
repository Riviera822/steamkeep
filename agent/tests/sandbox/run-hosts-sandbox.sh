#!/usr/bin/env bash
# WP 2.3 sandbox driver (host side): cross-build the Linux agent, then run
# hosts-sandbox-container.sh against the REAL /etc/hosts of a throwaway
# container.
#
# Why a container and not this machine: `vault-agent hosts` edits the system
# hosts file. The dev machine's own hosts file is off limits (it holds a live
# SteamVault entry added by hand during Phase 0), and so is WSL's. A container
# gives us a genuine, kernel-visible /etc/hosts that the resolver really reads,
# and throws it away afterwards.
#
# Note on what this exercises: Docker manages a container's /etc/hosts as a
# BIND-MOUNTED FILE. A bind-mounted file cannot be renamed over (EBUSY), so
# this sandbox happens to drive the in-place write fallback rather than the
# atomic-rename path - see the inode line in section 5 of the output, and
# writeFile's doc comment in agent/go/hostsfile/write.go. The rename path is
# covered by the fixture tests (agent/go/hostsfile) instead.
#
# Usage (from anywhere):
#   wsl -u root bash /mnt/c/claude-dev/SteamVault/agent/tests/sandbox/run-hosts-sandbox.sh
#
# Requires: docker, and a Go toolchain for the cross-build (or pass a
# pre-built linux/amd64 binary as $1).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GO_DIR="$(cd "$SCRIPT_DIR/../../go" && pwd)"

# Pinned tag, never latest/release (project rule). alpine ships getent as a
# busybox applet, which is all this sandbox needs from the base image.
IMAGE="alpine:3.23.5"

BIN="${1:-}"
if [[ -z "$BIN" ]]; then
    BIN="$(mktemp -d)/vault-agent-linux-amd64"
    echo "building $BIN ..."
    # -buildvcs=false: this driver runs as root (docker), and git refuses to
    # report VCS status for a repo owned by another user ("dubious
    # ownership"), which would otherwise fail the build. The sandbox binary is
    # throwaway - no VCS stamp needed.
    (cd "$GO_DIR" && CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
        go build -buildvcs=false -o "$BIN" ./cmd/vault-agent)
fi
chmod 755 "$BIN"
echo "binary: $BIN ($(sha256sum "$BIN" | cut -d' ' -f1))"

# --network none: no DNS server is reachable, so every name resolution in the
# sandbox can only be answered by the hosts file itself. That makes the
# getent assertions unambiguous and keeps the sandbox from touching the
# network at all.
docker run --rm \
    --network none \
    --user 0:0 \
    -v "$BIN:/vault-agent:ro" \
    -v "$SCRIPT_DIR/hosts-sandbox-container.sh:/sandbox.sh:ro" \
    "$IMAGE" \
    sh /sandbox.sh
