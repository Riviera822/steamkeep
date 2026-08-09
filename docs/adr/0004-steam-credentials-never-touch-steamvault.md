# ADR-0004: Steam credentials never touch SteamVault code

Date: 2026-08-05
Status: Accepted (binds Phase 4 app design and all server-side tooling)

## Context

SteamVault has two Steam login touchpoints: the server-side prefill
(SteamPrefill needs a real Steam session with the user's licenses to
download depot content) and the Android app (library + covers via the
Steam Web API). Both are trust-critical for an open-source project that
asks homelab users to run it next to their Steam account.

## Decision

1. **Server side:** the Steam session belongs to SteamPrefill alone. Login
   happens once, interactively, in SteamPrefill's own prompt — QR login via
   the Steam Mobile App is the recommended and documented path (the
   password is never typed on the server at all). SteamPrefill persists
   only its refresh token in its own config directory (gitignored; a
   dedicated volume in the container setup). vault-api drives the already
   authenticated CLI with stdin closed and treats "not logged in" as a job
   error with instructions — there is deliberately NO code path that
   accepts, forwards, or stores Steam credentials.
2. **App side (Phase 4):** identity via "Sign in with Steam" (OpenID)
   against Valve's official login page — the app never sees credentials,
   only the resulting SteamID64. Library data is fetched directly from the
   Steam Web API (device-local user-owned API key, stored only on the
   phone), never proxied through vault-api. OpenID cannot replace the
   server-side session (it asserts identity, it cannot download content) —
   that asymmetry is why the two touchpoints differ.
3. **Verifiability:** the community release ships a SECURITY.md section
   ("Where your Steam credentials go: only to Valve") documenting both
   flows and the data paths; no telemetry.

## Addendum (2026-08-09): web UI library relay — user decision A+C

The Steam Web API sends no CORS headers, so the Phase-4a WEB UI (unlike
the native Android app) cannot call `GetOwnedGames` from the browser.
User decision: **option A with option C's input UX** — vault-api gains a
small opt-in relay, and the Web API key is entered by the user in the web
UI's settings, then stored server-side (one-time setup).

Boundaries that keep this inside the spirit of this ADR:

- The relay covers ONLY public-profile read endpoints needed for the
  library grid (`GetOwnedGames`, `GetPlayerSummaries`) — never anything
  that could act on the account.
- What is stored is a revocable, read-scoped **Web API key**, never the
  account password; login still happens on Valve's OpenID page. Decision 1
  (server-side prefill session) is untouched.
- The key is stored like the vault API key is handled elsewhere: never
  logged, never echoed back in full by any endpoint, redacted in job logs.
- The relay is off until a key is configured; the Android app keeps the
  original device-local path (decision 2 stands unmodified for native
  clients).
- SECURITY.md documents the added data path: with the relay configured,
  library queries originate from the SERVER (they leave the LAN toward
  Valve), not from the browser.
