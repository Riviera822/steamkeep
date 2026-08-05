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
