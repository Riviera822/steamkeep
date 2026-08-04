#!/usr/bin/env bash
#
# SteamVault Phase 0 PoC -- WP 0.6: WSL2/Ubuntu setup for the Linux-Steam-
# client test kit. Run this INSIDE WSL2 Ubuntu (see PROTOCOL.md section 1
# for prerequisites -- a freshly installed Ubuntu WSL2 distro, nginx PoC
# already running on the Windows host).
#
# What this does, and why:
#   1. Installs prerequisite packages for the Steam client's own installer
#      (multiarch i386 -- the official .deb still wants some 32-bit libs)
#      plus the small utilities the test scripts and PROTOCOL.md rely on
#      (dig/nslookup, netstat, ip). Steam itself is NOT installed here --
#      that step is interactive (EULA acceptance) and belongs in
#      PROTOCOL.md section 3.2, not in an idempotent background script.
#   2. Installs dnsmasq but leaves it DISABLED/masked. scenario-b.sh turns
#      it on deliberately; scenario-a.sh depends on it being OFF so
#      Scenario A's "zero traffic" result isn't accidentally helped along
#      by a DNS server also answering the wildcard.
#   3. Detects the Windows host's IP address as reachable FROM WSL2, by
#      actually probing candidates against nginx's own /health endpoint --
#      not just reading a routing table and hoping. Covers both WSL2's
#      default NAT mode (host reachable via the default-route gateway) and
#      Windows 11 "mirrored" networking mode (host reachable at 127.0.0.1
#      directly) -- see PROTOCOL.md section 2 for the background.
#   4. Writes ./wsl-env (gitignored -- machine-specific) with the detected
#      IP, sourced by scenario-a.sh / scenario-b.sh.
#
# Idempotent: safe to re-run any time (e.g. after the Windows host IP
# changes across a reboot, or a network change). apt installs are no-ops if
# already satisfied; dnsmasq is (re-)masked every run; wsl-env is
# overwritten with fresh values every run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/wsl-env"
NGINX_PORT="80"

log() { echo ">> $*"; }

# --- 0. sanity: this is meant to run inside WSL, not plain Linux/macOS ------
if [[ ! -f /proc/version ]] || ! grep -qi "microsoft" /proc/version; then
    echo "WARNING: /proc/version does not mention Microsoft/WSL -- this" >&2
    echo "         script is written for WSL2/Ubuntu specifically (host-IP" >&2
    echo "         detection in particular assumes a WSL2 network setup)." >&2
    echo "         Continuing anyway, but double-check results if you're" >&2
    echo "         running this somewhere else." >&2
fi

# --- 1. prerequisite packages -----------------------------------------------
log "Updating apt package lists..."
sudo apt-get update -qq

log "Enabling i386 multiarch (Steam's official installer wants some 32-bit libs)..."
sudo dpkg --add-architecture i386
sudo apt-get update -qq

log "Installing prerequisite packages (dnsmasq, DNS/network diagnostic tools)..."
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq \
    ca-certificates \
    curl \
    gnupg \
    dnsmasq \
    dnsutils \
    iproute2 \
    net-tools \
    iputils-ping \
    psmisc

# --- 2. dnsmasq: installed, but deliberately left OFF ------------------------
# scenario-a.sh REQUIRES dnsmasq to be inactive (a running wildcard resolver
# would contaminate Scenario A's "the client never even looks it up" null
# result). scenario-b.sh explicitly (re-)enables it when that scenario runs.
log "Ensuring dnsmasq is installed but disabled (scenario-b.sh turns it on when needed)..."
if command -v systemctl >/dev/null 2>&1 && [[ "$(ps -p 1 -o comm= 2>/dev/null || true)" == "systemd" ]]; then
    sudo systemctl disable --now dnsmasq >/dev/null 2>&1 || true
    sudo systemctl mask dnsmasq
    log "  systemd detected: dnsmasq stopped and masked."
else
    sudo service dnsmasq stop >/dev/null 2>&1 || true
    log "  no systemd PID 1 detected under WSL -- dnsmasq installed but nothing"
    log "  auto-starts it anyway in that case. (If your distro is supposed to run"
    log "  systemd, check /etc/wsl.conf's [boot] systemd=true and 'wsl --shutdown'"
    log "  + reopen the terminal, then re-run this script.)"
fi

# --- 3. detect the Windows host IP, verified against the running nginx PoC --
#
# Tries, in order:
#   a) $WSL_HOST_IP  -- manual override, if the caller set one
#   b) the default-route gateway (WSL2 NAT mode: this IS the Windows host's
#      vEthernet (WSL) address)
#   c) the first nameserver in /etc/resolv.conf (usually == (b), checked
#      separately in case a given WSL setup diverges)
#   d) 127.0.0.1 (Windows 11 "mirrored" networking mode: WSL2 shares the
#      host's network namespace directly, so loopback reaches the host)
#
# Each candidate is verified for real -- curl its /health endpoint and
# require nginx's exact "ok" body -- rather than trusted on routing-table
# inference alone, since inference alone is exactly what breaks silently
# across the NAT-vs-mirrored networking-mode difference.
detect_host_ip() {
    local candidates=()
    local seen=" "

    if [[ -n "${WSL_HOST_IP:-}" ]]; then
        candidates+=("$WSL_HOST_IP")
    fi

    local gw
    gw="$(ip route show default 2>/dev/null | awk '/default/ {print $3; exit}')"
    [[ -n "$gw" ]] && candidates+=("$gw")

    local ns
    ns="$(awk '/^nameserver[ \t]/ {print $2; exit}' /etc/resolv.conf 2>/dev/null || true)"
    [[ -n "$ns" ]] && candidates+=("$ns")

    candidates+=("127.0.0.1")

    local ip
    for ip in "${candidates[@]}"; do
        if [[ "$seen" == *" $ip "* ]]; then
            continue
        fi
        seen="$seen$ip "

        log "  probing candidate $ip:$NGINX_PORT/health ..."
        local body
        body="$(curl -fsS --max-time 2 "http://$ip:$NGINX_PORT/health" 2>/dev/null || true)"
        if [[ "$body" == "ok" ]]; then
            echo "$ip"
            return 0
        fi
    done

    return 1
}

log "Detecting the Windows host IP (requires poc/start.ps1 already running on Windows)..."
HOST_IP=""
if HOST_IP="$(detect_host_ip)"; then
    log "  Found: $HOST_IP"
else
    echo "" >&2
    echo "ERROR: could not find a Windows host reachable from WSL2 on port $NGINX_PORT" >&2
    echo "       serving the nginx PoC's /health endpoint." >&2
    echo "" >&2
    echo "Checked, in order: \$WSL_HOST_IP override, default-route gateway," >&2
    echo "/etc/resolv.conf nameserver, 127.0.0.1 (mirrored-mode case)." >&2
    echo "" >&2
    echo "Fix, most likely causes first:" >&2
    echo "  1. Confirm 'poc\\start.ps1' is actually running on the Windows host," >&2
    echo "     and 'curl.exe -i http://127.0.0.1/health' succeeds THERE." >&2
    echo "  2. Check the Windows Firewall allows the vEthernet (WSL) interface" >&2
    echo "     on port $NGINX_PORT -- see PROTOCOL.md section 2.3." >&2
    echo "  3. Force a specific IP if you know it's correct:" >&2
    echo "       WSL_HOST_IP=<ip> ./wsl-setup.sh" >&2
    exit 1
fi

# --- 4. write the env file for scenario-a.sh / scenario-b.sh ---------------
cat > "$ENV_FILE" <<EOF
# Auto-generated by wsl-setup.sh on $(date -Is).
# Do NOT edit by hand -- re-run wsl-setup.sh to refresh (e.g. after the
# Windows host IP changes across a reboot or network change).
WSL_LINUX_TEST_HOST_IP="$HOST_IP"
WSL_LINUX_TEST_NGINX_PORT="$NGINX_PORT"
EOF

log "Wrote $ENV_FILE:"
sed 's/^/  /' "$ENV_FILE"

echo ""
log "Setup complete. Next steps (see PROTOCOL.md):"
log "  - Install the Steam client manually (PROTOCOL.md section 3.2 -- interactive, EULA)."
log "  - Run ./scenario-a.sh or ./scenario-b.sh to prepare a test."
