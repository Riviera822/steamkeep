#!/usr/bin/env bash
#
# SteamVault Phase 0 PoC -- WP 0.6, Scenario B: DNS-rewrite mode (dnsmasq
# inside WSL2) for the Linux-Steam-client test. Run this INSIDE WSL2
# Ubuntu, after wsl-setup.sh has run at least once (see PROTOCOL.md
# section 5).
#
# What this does: writes a dnsmasq config that rewrites *.steamcontent.com
# (wildcard, not just the single lancache.steamcontent.com name) to the
# cache's IP, points WSL2's own /etc/resolv.conf at that dnsmasq instance,
# and verifies -- before you touch Steam -- that the wildcard resolves
# correctly AND that AAAA queries for the same names come back NODATA. Note
# (corrected during the WP 0.6 follow-up fix): on modern dnsmasq (verified
# on 2.92), address= alone does NOT do this -- it only intercepts the RR
# type it was given a literal for (A, for an IPv4 target) and forwards
# every other query type, including AAAA, upstream to the real answer. The
# zone must additionally be made `local=/steamcontent.com/` (authoritative,
# no forwarding) for AAAA to come back NODATA instead of leaking Valve's
# real IPv6 address -- see the comment above the dnsmasq config block below
# and PROTOCOL.md's IPv6/AAAA section for the live evidence. This is the
# first real evidence for the vault-dns approach working for Linux/Steam
# Deck-class clients specifically.
#
# Usage:
#   ./scenario-b.sh              set up (idempotent -- safe to re-run)
#   ./scenario-b.sh --rollback   undo everything (dnsmasq, resolv.conf, wsl.conf)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/wsl-env"
DNSMASQ_CONF="/etc/dnsmasq.d/steamvault-scenario-b.conf"
RESOLV_CONF="/etc/resolv.conf"
RESOLV_BACKUP="/etc/resolv.conf.steamvault-backup"
WSL_CONF="/etc/wsl.conf"

usage() {
    echo "Usage: $0 [--rollback]" >&2
    exit 1
}

MODE="setup"
if [[ $# -gt 0 ]]; then
    case "$1" in
        --rollback) MODE="rollback" ;;
        -h|--help)  usage ;;
        *) echo "Unknown argument: $1" >&2; usage ;;
    esac
fi

using_systemd() {
    command -v systemctl >/dev/null 2>&1 && [[ "$(ps -p 1 -o comm= 2>/dev/null || true)" == "systemd" ]]
}

stop_dnsmasq() {
    if using_systemd; then
        sudo systemctl disable --now dnsmasq >/dev/null 2>&1 || true
        sudo systemctl mask dnsmasq >/dev/null 2>&1 || true
    else
        sudo service dnsmasq stop >/dev/null 2>&1 || true
    fi
}

start_dnsmasq() {
    if using_systemd; then
        sudo systemctl unmask dnsmasq >/dev/null 2>&1 || true
        sudo systemctl enable --now dnsmasq
    else
        sudo service dnsmasq restart
    fi
}

if [[ "$MODE" == "rollback" ]]; then
    echo "Rolling back Scenario B..."

    stop_dnsmasq
    sudo rm -f "$DNSMASQ_CONF"
    echo "dnsmasq stopped/masked again, $DNSMASQ_CONF removed."

    if [[ -f "$RESOLV_BACKUP" ]]; then
        sudo rm -f "$RESOLV_CONF"
        sudo cp "$RESOLV_BACKUP" "$RESOLV_CONF"
        sudo rm -f "$RESOLV_BACKUP"
        echo "Restored $RESOLV_CONF from backup."
    else
        echo "No resolv.conf backup found at $RESOLV_BACKUP -- leaving $RESOLV_CONF as-is."
        echo "If it still points at 127.0.0.1, either run 'wsl --shutdown' from a"
        echo "Windows PowerShell (WSL regenerates resolv.conf on next start, unless"
        echo "generateResolvConf=false is still set below) or edit it back manually."
    fi

    if grep -q "generateResolvConf" "$WSL_CONF" 2>/dev/null; then
        sudo sed -i '/generateResolvConf/d' "$WSL_CONF"
        echo "Removed the generateResolvConf override from $WSL_CONF -- WSL2's"
        echo "default (auto-regenerate resolv.conf on start) is restored on next start."
    fi

    echo ""
    echo "Rollback complete. Verify:"
    echo "  cat /etc/resolv.conf                    (should no longer say 127.0.0.1)"
    echo "  systemctl status dnsmasq  (or: service dnsmasq status)   (should show stopped)"
    exit 0
fi

# --- setup path --------------------------------------------------------------

if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found -- run ./wsl-setup.sh first." >&2
    exit 1
fi
# shellcheck disable=SC1090
source "$ENV_FILE"
: "${WSL_LINUX_TEST_HOST_IP:?WSL_LINUX_TEST_HOST_IP missing from wsl-env -- re-run ./wsl-setup.sh}"

if ! command -v dnsmasq >/dev/null 2>&1; then
    echo "ERROR: dnsmasq not installed -- run ./wsl-setup.sh first." >&2
    exit 1
fi

# --- 1. dnsmasq config: wildcard-rewrite *.steamcontent.com -> cache IP -----
# address=/DOMAIN/IP rewrites DOMAIN and all its subdomains to IP for A
# queries. IMPORTANT CORRECTION (found live on dnsmasq 2.92 / Ubuntu 26.04
# WSL2 during WP 0.6 follow-up): on modern dnsmasq, address= does NOT make
# AAAA (or any other RR type) return NODATA by itself -- it only intercepts
# the RR type(s) it was given an address for (here: A, because
# $WSL_LINUX_TEST_HOST_IP is an IPv4 literal). Every OTHER query type for
# that name, including AAAA, still gets forwarded upstream via `server=`
# and comes back as the REAL Valve CDN address -- silently defeating the
# whole rewrite for any IPv6-capable client. Verified live: a bare
# address=/steamcontent.com/<ipv4> config let
# `dig AAAA cache2-ams1.steamcontent.com` return Valve's real
# 2a01:bc80:7:100::9b85:f80d. docs/PROJECT_PLAN.md section 3's original
# claim ("address= returns NODATA for AAAA") is outdated and has been
# corrected there and in PROTOCOL.md.
#
# The fix: `local=/DOMAIN/` makes dnsmasq authoritative for the whole zone
# -- it answers from its own data for every RR type and never forwards
# zone queries upstream at all. Combined with address= (which still
# supplies the A answer), this makes AAAA (and any other RR type) resolve
# to NODATA (NOERROR, zero answers) locally instead of leaking upstream.
# This is a REQUIRED pairing, not optional hardening -- address= alone is
# an IPv6 bypass. Verified live: with local=/steamcontent.com/ added,
# AAAA for the same name comes back NOERROR/ANSWER: 0, A/wildcard/getent
# behavior is unaffected, and unrelated domains still forward normally.
#
# `no-resolv` + explicit `server=` lines: without this, dnsmasq's own
# upstream lookups would go through /etc/resolv.conf by default -- the very
# file we're about to point at dnsmasq itself (step 2 below). That would be
# a resolution loop (dnsmasq asking dnsmasq for everything, forever). Same
# category of loop-risk poc/conf/nginx.conf already had to avoid on the
# Windows side (see poc/README.md "Upstream choice and the loop-risk it
# avoids") -- this is the WSL2/dnsmasq equivalent of that same problem.
sudo mkdir -p "$(dirname "$DNSMASQ_CONF")"
sudo tee "$DNSMASQ_CONF" >/dev/null <<EOF
# Auto-generated by scenario-b.sh -- safe to delete, or run 'scenario-b.sh --rollback'.
no-resolv
server=1.1.1.1
server=8.8.8.8
address=/steamcontent.com/$WSL_LINUX_TEST_HOST_IP
local=/steamcontent.com/
listen-address=127.0.0.1
bind-interfaces
EOF
echo "Wrote dnsmasq config: $DNSMASQ_CONF"

start_dnsmasq
echo "dnsmasq (re)started."

# --- 2. point resolv.conf at the local dnsmasq -------------------------------
# WSL2 normally auto-regenerates /etc/resolv.conf (pointing at the Windows
# host's own resolver) on every WSL start / network change. Back the
# original up once (not on every re-run), then point it at our local
# dnsmasq and disable auto-regeneration for this distro so it sticks for
# the rest of this WSL session.
if [[ ! -f "$RESOLV_BACKUP" ]]; then
    sudo cp "$RESOLV_CONF" "$RESOLV_BACKUP"
    echo "Backed up original $RESOLV_CONF -> $RESOLV_BACKUP"
fi

# resolv.conf is often a symlink (WSL-managed) -- replace it with a plain file.
if [[ -L "$RESOLV_CONF" ]]; then
    sudo rm -f "$RESOLV_CONF"
fi
echo "nameserver 127.0.0.1" | sudo tee "$RESOLV_CONF" >/dev/null
echo "Pointed $RESOLV_CONF at 127.0.0.1 (local dnsmasq)."

if ! grep -q "generateResolvConf" "$WSL_CONF" 2>/dev/null; then
    printf '\n[network]\ngenerateResolvConf = false\n' | sudo tee -a "$WSL_CONF" >/dev/null
    echo "Set generateResolvConf=false in $WSL_CONF (prevents WSL2 from overwriting"
    echo "our resolv.conf on the next network event / WSL restart)."
fi

# --- 3. verify resolution before handing over to Steam -----------------------
echo ""
echo "Verifying resolution state..."
sleep 1   # give dnsmasq a moment after (re)start

RESOLVED_EXACT="$(getent ahostsv4 lancache.steamcontent.com 2>/dev/null | awk '{print $1; exit}' || true)"
RESOLVED_WILDCARD="$(getent ahostsv4 anything-random-xyz123.steamcontent.com 2>/dev/null | awk '{print $1; exit}' || true)"

CHECKS_OK=1

if [[ "$RESOLVED_EXACT" == "$WSL_LINUX_TEST_HOST_IP" ]]; then
    echo "[ OK ] lancache.steamcontent.com -> $RESOLVED_EXACT"
else
    echo "[FAIL] lancache.steamcontent.com -> '${RESOLVED_EXACT:-<no result>}', expected $WSL_LINUX_TEST_HOST_IP" >&2
    CHECKS_OK=0
fi

if [[ "$RESOLVED_WILDCARD" == "$WSL_LINUX_TEST_HOST_IP" ]]; then
    echo "[ OK ] wildcard confirmed: anything-random-xyz123.steamcontent.com -> $RESOLVED_WILDCARD"
else
    echo "[FAIL] wildcard rewrite did not apply to an arbitrary *.steamcontent.com subdomain" >&2
    CHECKS_OK=0
fi

# AAAA check (plan section 3, corrected): local=/steamcontent.com/ paired
# with address= should yield NODATA -- NOERROR status, zero answers -- not
# a real (routable) address and not NXDOMAIN. This is a HARD requirement,
# not advisory: a routable AAAA leaking through here means an IPv6-capable
# client silently bypasses the cache (see the comment above the dnsmasq
# config block). Check both the exact name and an arbitrary wildcard
# subdomain, and check them against a REAL Steam CDN-style hostname
# (cache2-ams1), not just lancache.steamcontent.com, since that's the class
# of name real clients actually query for AAAA.
if ! command -v dig >/dev/null 2>&1; then
    echo "[FAIL] 'dig' not found (should have been installed by wsl-setup.sh) -- cannot verify the IPv6 bypass is closed" >&2
    exit 1
fi

check_aaaa_nodata() {
    local name="$1"
    local out status answers routable_leak
    out="$(dig +noall +comments +answer AAAA "$name" 2>/dev/null || true)"
    status="$(echo "$out" | grep -oE 'status: [A-Z]+' || true)"
    answers="$(echo "$out" | grep -oE 'ANSWER: [0-9]+' || true)"
    # A routable leak = any AAAA record actually present in the answer
    # section (anything other than "no answer at all").
    routable_leak="$(echo "$out" | grep -E '^[^;].*[[:space:]]AAAA[[:space:]]' || true)"

    if [[ -n "$routable_leak" ]]; then
        echo "[FAIL] AAAA query for $name returned a ROUTABLE address -- IPv6 bypass is OPEN:" >&2
        echo "       $routable_leak" >&2
        return 1
    fi

    if [[ "$status" == "status: NOERROR" && "$answers" == "ANSWER: 0" ]]; then
        echo "[ OK ] AAAA query for $name returns NODATA (NOERROR/ANSWER: 0) -- IPv6 bypass closed"
        return 0
    fi

    echo "[FAIL] AAAA query for $name did not return clean NODATA (got '$status' / '$answers') -- cannot confirm the IPv6 bypass is closed" >&2
    return 1
}

AAAA_OK=1
check_aaaa_nodata "lancache.steamcontent.com" || AAAA_OK=0
check_aaaa_nodata "cache2-ams1.steamcontent.com" || AAAA_OK=0

if [[ "$AAAA_OK" -ne 1 ]]; then
    echo "" >&2
    echo "IPv6 bypass verification FAILED -- a Linux/Steam Deck-class client with" >&2
    echo "IPv6 connectivity would silently skip the cache. Fix the dnsmasq config" >&2
    echo "(local=/steamcontent.com/ must be present alongside address=/steamcontent.com/...)" >&2
    echo "before starting Steam. See PROTOCOL.md section 8." >&2
    exit 1
fi

if [[ "$CHECKS_OK" -ne 1 ]]; then
    echo "" >&2
    echo "Resolution verification FAILED -- fix before starting Steam. See" >&2
    echo "PROTOCOL.md section 8 (Scenario B troubleshooting)." >&2
    exit 1
fi

echo ""
echo "Scenario B is set up. Timestamp marker (note this down for"
echo "analyze-windows.ps1 -From):"
date -Is
echo ""
echo "Next (see PROTOCOL.md section 5.1):"
echo "  1. Fully quit Steam if it's already running, then relaunch it."
echo "  2. Install/download the test game (Spacewar, AppID 480, is recommended --"
echo "     see PROTOCOL.md section 5.3 for a note on cache reuse if you pick the"
echo "     same game WP 0.3 already downloaded on the Windows client)."
echo "  3. Once done, run 'date -Is' again for the -To marker."
echo "  4. On the Windows side:"
echo "       poc\\linux-client-test\\analyze-windows.ps1 -Scenario B -From <start> -To <end>"
