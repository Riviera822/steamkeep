#!/bin/sh
# SteamVault WP 1.9 -- drift check between the native and container nginx configs.
#
#   core/nginx/nginx.conf              (WP 1.1, reviewed, test-covered by
#                                       core/tests/test-core.ps1 against the real
#                                       Steam CDN -- the source of truth)
#   core/docker/nginx.conf.template    (WP 1.9, what actually runs in the image)
#
# The container variant exists because five directives cannot be shared (log
# destinations, pid path, worker user, and the resolver becoming an env
# placeholder). Everything else -- every map, every proxy_set_header, the store
# guard, the Host allowlist, the nocache bypass, the log_format -- MUST stay
# identical, or the container silently stops being the thing that was reviewed
# and tested.
#
# This script makes that contract executable:
#   1. normalise both files (drop comments and blank lines, trim, collapse runs
#      of whitespace -- none of which is semantic in nginx)
#   2. un-apply the five allowed container deltas from the template, asserting
#      each one was present EXACTLY once (so a delta that silently disappears is
#      also a failure, not just an unexpected extra line)
#   3. diff. Any remaining difference fails with a unified diff.
#
# Usage:  sh core/docker/check-config-drift.sh   [from anywhere]
# Exit:   0 = in sync, 1 = drift (diff printed), 2 = usage/IO error
#
# Runs on any POSIX shell (verified in WSL2/Ubuntu 26.04 and inside the built
# image); no bashisms, no GNU-only sed features.

set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
core_dir=$(CDPATH= cd -- "$script_dir/.." && pwd)

NATIVE="$core_dir/nginx/nginx.conf"
TEMPLATE="$core_dir/docker/nginx.conf.template"

[ -f "$NATIVE" ]   || { echo "check-config-drift: missing $NATIVE" >&2;   exit 2; }
[ -f "$TEMPLATE" ] || { echo "check-config-drift: missing $TEMPLATE" >&2; exit 2; }

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT INT TERM

# --- 1. normalise ------------------------------------------------------------
# Strip CR (a Windows checkout of the .conf is legitimate -- it is run natively
# on Windows), drop comment-only and blank lines, trim, collapse whitespace runs.
normalise() {
    tr -d '\r' < "$1" \
    | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' \
    | grep -v '^#' \
    | grep -v '^$' \
    | sed -e 's/[[:space:]][[:space:]]*/ /g'
}

normalise "$NATIVE"   > "$work/native.norm"
normalise "$TEMPLATE" > "$work/template.norm"

# --- 2. un-apply the five allowed deltas ------------------------------------
fail=0

# expect_once <file> <fixed-string> <human description>
expect_once() {
    n=$(grep -F -c -x -- "$2" "$1" || true)
    if [ "$n" != "1" ]; then
        echo "check-config-drift: FAIL: expected exactly 1 occurrence of '$2' in the container template ($3), found $n" >&2
        fail=1
    fi
}

expect_once "$work/template.norm" "user nginx;"                                "delta 1: explicit worker user"
expect_once "$work/template.norm" "pid /var/run/nginx.pid;"                    "delta 2: pid outside the volume"
expect_once "$work/template.norm" "error_log /dev/stderr warn;"                "delta 3: error log to stderr"
expect_once "$work/template.norm" 'resolver ${VAULT_RESOLVER} ipv6=off valid=30s;' "delta 4: resolver placeholder"
expect_once "$work/template.norm" "access_log /dev/stdout vault;"              "delta 5: access log to stdout"

# The native config must NOT already contain the container forms (would mean the
# two files drifted in the other direction, e.g. someone containerised the
# native config in place).
expect_once "$work/native.norm" "pid logs/nginx.pid;"                          "native: pid under the prefix"
expect_once "$work/native.norm" "error_log logs/error.log warn;"               "native: error log to a file"
expect_once "$work/native.norm" "resolver 1.1.1.1 ipv6=off valid=30s;"         "native: literal resolver"
expect_once "$work/native.norm" "access_log logs/access.log vault;"            "native: access log to a file"

[ "$fail" = "0" ] || exit 1

sed \
    -e '/^user nginx;$/d' \
    -e 's|^pid /var/run/nginx\.pid;$|pid logs/nginx.pid;|' \
    -e 's|^error_log /dev/stderr warn;$|error_log logs/error.log warn;|' \
    -e 's|^resolver \${VAULT_RESOLVER} ipv6=off valid=30s;$|resolver 1.1.1.1 ipv6=off valid=30s;|' \
    -e 's|^access_log /dev/stdout vault;$|access_log logs/access.log vault;|' \
    "$work/template.norm" > "$work/template.unapplied"

# --- 3. diff -----------------------------------------------------------------
if diff -u "$work/native.norm" "$work/template.unapplied" > "$work/diff" 2>&1; then
    lines=$(wc -l < "$work/native.norm" | tr -d ' ')
    echo "check-config-drift: OK -- $lines normalised directive lines identical"
    echo "  native:   $NATIVE"
    echo "  template: $TEMPLATE"
    exit 0
fi

echo "check-config-drift: FAIL -- the container template diverges from core/nginx/nginx.conf" >&2
echo "  (left = core/nginx/nginx.conf, right = core/docker/nginx.conf.template with the" >&2
echo "   five allowed container deltas un-applied)" >&2
echo >&2
cat "$work/diff" >&2
exit 1
