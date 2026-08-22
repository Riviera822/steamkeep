#!/bin/sh
# SteamHangar vault-proxy -- shared VAULT_EGRESS_ALLOW hostname validation
# (WP EG-1, ADR-0011, round-2 review S2/N4).
#
# Defines ONE function, `normalize_egress_hostname`, with NO side effects
# (no filter-file writes, no `exec`) -- sourced by docker-entrypoint.sh (the
# real, shipped path) AND invoked directly, via a real `sh` subprocess, by
# api/tests/test_eg1_egress_lock.py, so the test pins the ACTUAL script
# vault-proxy runs, not a Python reimplementation of it
# (docs/LEARNINGS.md's "pinned-the-fake" class: WP 4b.2/4b.3/4b.8 already
# name the same mistake for a different language pair).
#
# Deliberately the SAME rule `api/vault_api/config.py::_env_egress_allow`
# enforces on the exact same operator-supplied value, on the OTHER
# container -- two independent renderers of one value should agree on every
# input, not just the happy path. Round-2 review measured three real
# disagreements before this file existed:
#   - "a..b" (empty label): config.py's leading/trailing-only check accepted
#     it; the entrypoint's `*..*` case arm rejected it -- api booted "clean"
#     while vault-proxy crash-looped on a value api had already blessed.
#   - internal whitespace ("ap i.com"): the OLD entrypoint's
#     `tr -d '[:space:]'` erased ALL whitespace (not just leading/trailing)
#     BEFORE the character-class check ever ran, so the check that should
#     have refused it never saw the evidence -- it silently became
#     "api.com" instead. config.py correctly refused the same input.
#   - "*": the OLD entrypoint's `for raw in ${VAULT_EGRESS_ALLOW}` (no
#     `set -f`) let the shell's own pathname expansion run BEFORE the loop
#     body -- a literal "*" expanded into every filename in the working
#     directory, refuted the original "the character check would catch it"
#     assumption, and never reached the validator at all.
# `set -f` around the loop (see docker-entrypoint.sh) fixes the third; this
# file's own trim-then-validate order (trim first, check the trimmed
# result, never delete characters the check is supposed to see) fixes the
# first two.
#
# normalize_egress_hostname <raw-entry>
#   Prints the normalized (trimmed, lowercased) hostname to stdout and
#   returns 0 on a valid, non-blank entry.
#   Returns 2 (prints nothing) on a BLANK entry (only whitespace, or empty)
#   -- the caller's job to treat that as "skip", not "die": a stray comma
#   is a cosmetic typo, not a security-relevant one.
#   Returns 1 (prints nothing) on a non-blank entry that is not a plausible
#   hostname -- the caller's job to `die` loudly, naming the original raw
#   entry (this function does not have the env var NAME to put in a message).

normalize_egress_hostname() {
    _raw=$1
    # Trim leading/trailing whitespace ONLY -- matching Python's
    # `str.strip()`, and deliberately NOT `tr -d '[:space:]'` (which would
    # delete INTERNAL whitespace too, hiding it from the character-class
    # check below the way the pre-fix version of this script did).
    _trimmed=$(printf '%s' "$_raw" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')
    _host=$(printf '%s' "$_trimmed" | tr 'A-Z' 'a-z')
    if [ -z "$_host" ]; then
        return 2
    fi
    case "$_host" in
        *[!a-z0-9.-]*)
            return 1
            ;;
        .*|*.|*..*|-*|*-)
            return 1
            ;;
    esac
    printf '%s\n' "$_host"
    return 0
}
