#!/bin/sh
# SteamVault vault-core container hook -- optional cache-event log toggle
# (WP 3.10, ADR-0008).
#
# Runs from the official nginx image's /docker-entrypoint.d/ hook directory,
# AFTER 20-envsubst-on-templates.sh has rendered nginx.conf.template ->
# nginx.conf (envsubst already substituted ${VAULT_EVENT_LOG} with whatever
# the environment holds, INCLUDING an empty string if the variable is unset
# or set to "" -- Compose/`docker run -e` always EXPORT the key, so envsubst
# treats it as "defined but empty", never as "missing" -- see
# core/docker/nginx.conf.template's header), and BEFORE
# 40-vault-preflight.sh's own checks and before nginx itself is exec'd (this
# script is named to sort between the two: 20- then 25- then 40-).
# docker-entrypoint.sh runs with `set -e`, so a non-zero exit here aborts
# container start, same as every other hook in this directory.
#
# --- Why this can't be handled by envsubst alone ----------------------------
# An empty ${VAULT_EVENT_LOG} substitution leaves:
#     access_log  vault_event buffer=64k flush=5s; # VAULT_EVENT_LOG_LINE
# which nginx parses as "access_log vault_event buffer=64k flush=5s;" -- the
# FORMAT NAME ("vault_event") lands in the PATH argument slot instead, and
# nginx refuses to start with a confusing error, not a clean "logging
# disabled". envsubst has no conditional substitution, so turning "empty
# means off" into an actually-clean no-op requires a second, explicit pass
# over the rendered file -- this script is that pass.
#
# The two identical access_log lines in nginx.conf.template (one in
# location /depot/, one in location @miss) both carry a stable trailing
# comment marker, "# VAULT_EVENT_LOG_LINE", specifically so this script can
# find and remove (or clean up) them by that marker -- never by trying to
# re-parse or guess at the rendered path.

set -eu

ME="25-vault-eventlog.sh"

log()  { echo "$ME: $*"; }
die()  { echo "$ME: FATAL: $*" >&2; exit 1; }

CONF="/etc/nginx/nginx.conf"
MARKER="# VAULT_EVENT_LOG_LINE"
VALUE="${VAULT_EVENT_LOG:-}"
WORKER_USER="nginx"

[ -f "$CONF" ] || die "$CONF does not exist -- expected to run after envsubst rendering
  (after /docker-entrypoint.d/20-envsubst-on-templates.sh)."

if [ -z "$VALUE" ]; then
    # --- Feature OFF (ADR-0008: "optional at runtime") ----------------------
    # Remove both marked lines entirely: no access_log directive at all for
    # the event log, so vault-core behaves exactly as if this work package
    # never shipped -- no file is ever created or written to, and the
    # request-processing cost is zero (not even a format-string build).
    sed -i "/${MARKER}\$/d" "$CONF"

    remaining=$(grep -c "$MARKER" "$CONF" 2>/dev/null || true)
    if [ "${remaining:-0}" != "0" ]; then
        die "failed to remove all cache-event-log lines while VAULT_EVENT_LOG is
  unset/empty ($remaining marker(s) remain in $CONF). Refusing to start with a
  half-disabled event log rather than guessing which line survived."
    fi

    # REVIEW FINDING N1: the marker-count check above only proves no MARKER
    # survived -- it says nothing about whether a live "vault_event"
    # access_log directive is still in the file. A line that somehow lost
    # its trailing "# VAULT_EVENT_LOG_LINE" comment (a future edit to the
    # template that drops the marker but keeps the directive, a corrupted
    # render, a hand-edited nginx.conf.template) would sail through the
    # check above with remaining=0 while leaving a real, live event-log
    # access_log behind -- exactly the "half-disabled" state this script
    # exists to prevent. Check for the DIRECTIVE itself too, independent of
    # the marker: nothing naming "vault_event" (the log_format name used
    # only by this feature's access_log lines) may remain once the feature
    # is off. Keep the Dockerfile's marker-count build-time assertion as
    # the FIRST line of defense (it catches a template edit before the
    # image even ships); this is the second, runtime one.
    survivors=$(grep -c "vault_event" "$CONF" 2>/dev/null || true)
    if [ "${survivors:-0}" != "0" ]; then
        die "VAULT_EVENT_LOG is unset/empty but $survivors line(s) referencing
  'vault_event' still remain in $CONF after marker-based removal -- a live
  event-log access_log directive may have survived without its marker
  comment. Refusing to start half-disabled rather than guessing which line
  is safe to ignore."
    fi

    log "VAULT_EVENT_LOG unset/empty -- cache-event log disabled (ADR-0008 optional-at-runtime), no access_log directive rendered"
    exit 0
fi

# --- Feature ON: validate the path -------------------------------------------
# This value is embedded verbatim into nginx.conf by envsubst, so anything
# that could end/open a directive (';', '{', '}') or a quote would be config
# injection -- same class of risk 40-vault-preflight.sh already guards for
# VAULT_RESOLVER, same fix: a strict character allowlist rather than trying
# to enumerate everything that's dangerous. Absolute path only (nginx would
# otherwise resolve a relative one against its prefix, /vault, which works
# but hides the actual location from anyone reading the env var alone).
case "$VALUE" in
    /*) : ;;
    *) die "VAULT_EVENT_LOG='$VALUE' must be an absolute path (start with '/'), or
  empty/unset to disable the cache-event log entirely." ;;
esac
case "$VALUE" in
    *[!A-Za-z0-9/_.-]*)
        die "VAULT_EVENT_LOG='$VALUE' contains characters outside the allowed set
  (letters, digits, '/', '_', '-', '.'). This value is written verbatim into
  nginx.conf; refusing rather than risking config injection." ;;
esac

# REVIEW FINDING N2: the checks above accept ANY absolute path, and this
# script `mkdir -p`s and `chown`s the value's PARENT DIRECTORY below --
# unconstrained, VAULT_EVENT_LOG=/etc/nginx/x.log would hand /etc/nginx
# itself over to the nginx worker user (uid 101), and worse paths
# (/etc, /) are just as syntactically "valid absolute paths". This value
# only ever needs to point somewhere on the /vault volume (core/README.md
# "Docker: VAULT_EVENT_LOG" -- the log lives alongside cache/ and tmp/ on
# the one volume vault-api also mounts), so require that explicitly instead
# of trusting an operator-supplied path to be well-intentioned.
case "$VALUE" in
    /vault/*) : ;;
    *) die "VAULT_EVENT_LOG='$VALUE' must be a path under /vault/ (the shared cache
  volume, e.g. /vault/logs/event.log). Refusing to mkdir/chown a directory
  outside /vault/ for the nginx worker user -- that could hand over an
  unrelated system directory (e.g. /etc/nginx) depending on the value." ;;
esac

# Strip only the trailing marker comment -- it has done its job identifying
# the line for this script; nginx would silently ignore it either way (it's
# a valid trailing comment), but removing it keeps the deployed config free
# of a marker that only ever mattered pre-boot.
sed -i "s/[[:space:]]*${MARKER}\$//" "$CONF"

still_marked=$(grep -c "$MARKER" "$CONF" 2>/dev/null || true)
if [ "${still_marked:-0}" != "0" ]; then
    die "failed to strip the cache-event-log marker comment from $CONF while enabling it ($still_marked remain)."
fi

# Same reasoning as 40-vault-preflight.sh's depot/ pre-creation: don't make
# a fresh install depend on the operator manually creating the log
# directory. mkdir -p is a no-op if it already exists (the image seeds
# /vault/logs itself -- see core/Dockerfile), so this only matters for a
# custom VAULT_EVENT_LOG path pointing somewhere else on the /vault volume.
event_dir=$(dirname "$VALUE")
mkdir -p "$event_dir"
chown "$WORKER_USER:$WORKER_USER" "$event_dir"
log "cache-event log ENABLED (ADR-0008): $VALUE"
