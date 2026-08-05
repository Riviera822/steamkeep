#!/usr/bin/env bash
# SteamVault vault-dns -- WSL2-side helper for test-dnsmasq-config.ps1 (WP 1.8).
#
# Starts a THROWAWAY dnsmasq instance (foreground, --no-daemon) against the
# rendered config passed as $1, queries it for A and AAAA records against
# TWO names, reports the results as simple KEY=VALUE lines the caller can
# parse, then kills exactly that instance. Never touches port 53 or any
# other dnsmasq process -- the rendered config given to this script must
# already use a non-53 port (test-dnsmasq-config.ps1 is responsible for
# that; this script does not rewrite the config itself).
#
# Two query names, not one: an arbitrary synthetic subdomain (proves the
# wildcard match itself, independent of any real hostname Valve happens to
# use) AND a realistic Steam CDN edge hostname
# (cache2-ams1.steamcontent.com -- the same one poc/linux-client-test's
# scenario-b.sh and core/tests/test-core.ps1 both use), so this check
# mirrors what a real client actually queries, not just an abstract
# wildcard proof.
#
# Deliberately a real .sh file (not an inline `bash -c "..."` string built
# by the caller): a multi-layer quoted string spanning PowerShell -> wsl.exe
# -> bash -c proved fragile in practice (variable expansion and background-
# job semantics disagreed across the layers during this WP's development).
# A plain script invoked as `wsl -u root bash <this-file> <conf-path>` has
# exactly one shell interpreting it, which is far more predictable.
#
# Usage: functional-check.sh <rendered-conf-path> [port] [name1] [name2]
# Output (stdout), one block per query name:
#   A_RESULT_WILDCARD=<ip-or-empty>       AAAA_STATUS_WILDCARD=... AAAA_ANSWERS_WILDCARD=...
#   A_RESULT_REALNAME=<ip-or-empty>       AAAA_STATUS_REALNAME=... AAAA_ANSWERS_REALNAME=...
# Exit 0 if dnsmasq started successfully and all queries completed (the
# VALUES still need to be checked by the caller -- this script reports,
# it does not itself assert pass/fail). Exit 1 if dnsmasq failed to start.

set -uo pipefail

CONF="${1:?usage: functional-check.sh <rendered-conf-path> [port] [name1] [name2]}"
TEST_PORT="${2:-5533}"
QUERY_NAME_WILDCARD="${3:-anything-random-steamvault-test.steamcontent.com}"
QUERY_NAME_REALNAME="${4:-cache2-ams1.steamcontent.com}"
LOG=/tmp/steamvault-func-check.log

rm -f "$LOG"

dnsmasq --no-daemon -C "$CONF" --port="$TEST_PORT" > "$LOG" 2>&1 &
DPID=$!
sleep 1

if ! kill -0 "$DPID" 2>/dev/null; then
    echo "DNSMASQ_START_FAILED"
    cat "$LOG" >&2
    exit 1
fi

query_one() {
    local name="$1" label="$2"
    local a_result aaaa_out aaaa_status aaaa_answers

    a_result=$(dig +short +time=3 +tries=1 @127.0.0.1 -p "$TEST_PORT" A "$name" | head -1)
    aaaa_out=$(dig +noall +comments +answer +time=3 +tries=1 @127.0.0.1 -p "$TEST_PORT" AAAA "$name")
    aaaa_status=$(echo "$aaaa_out" | grep -oE 'status: [A-Z]+' | head -1)
    aaaa_answers=$(echo "$aaaa_out" | grep -oE 'ANSWER: [0-9]+' | head -1)

    echo "A_RESULT_${label}=$a_result"
    echo "AAAA_STATUS_${label}=$aaaa_status"
    echo "AAAA_ANSWERS_${label}=$aaaa_answers"
}

query_one "$QUERY_NAME_WILDCARD" "WILDCARD"
query_one "$QUERY_NAME_REALNAME" "REALNAME"

# Kill exactly this PID -- never a pattern/name match that could catch the
# live scenario-B instance (poc/linux-client-test) or anything else.
kill "$DPID" 2>/dev/null
wait "$DPID" 2>/dev/null
rm -f "$LOG"
exit 0
