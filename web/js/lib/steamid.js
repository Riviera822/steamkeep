/**
 * SteamID64 validation (WP 4a.6), mirroring the server's exact rule
 * (`api/vault_api/steam_relay.py::valid_steamid64`) so a client-side
 * rejection and a `422` from `GET /v1/steam/owned-games`/`player-summaries`
 * always agree: exactly 17 ASCII decimal digits, range-checked against the
 * individual-account SteamID64 space (universe 1, type 1, instance 1,
 * account number 0..2**32-1).
 *
 * `BigInt` is required for the range check, not a style choice: the base
 * value (76561197960265728) already exceeds `Number.MAX_SAFE_INTEGER`
 * (9007199254740991) — a plain `Number(text)` comparison would silently
 * round every candidate to the same handful of representable doubles near
 * that magnitude, accepting or rejecting values based on floating-point
 * rounding rather than the actual digits typed.
 */

export const STEAM_ID64_DIGITS = 17;
export const STEAM_ID64_BASE = 76561197960265728n;
export const STEAM_ID64_MAX = STEAM_ID64_BASE + 0xffffffffn;

/**
 * ASCII `0`-`9` only — deliberately NOT `/^\d+$/` (JavaScript's `\d` is
 * already ASCII-only, unlike Python's Unicode-aware `str.isdigit()`, but a
 * manual char-code walk keeps this module's guarantee self-evident rather
 * than resting on an engine detail, and gives every case below a single,
 * obvious place to fail).
 */
function isAsciiDigits(text) {
  if (typeof text !== "string" || text.length === 0) return false;
  for (let i = 0; i < text.length; i++) {
    const code = text.charCodeAt(i);
    if (code < 48 || code > 57) return false;
  }
  return true;
}

/**
 * @param {unknown} value
 * @returns {string | null} the canonical 17-digit string, or `null` if
 *   `value` is not a plausible individual-account SteamID64.
 */
export function validSteamId64(value) {
  if (typeof value !== "string") return null;
  if (value.length !== STEAM_ID64_DIGITS) return null;
  if (!isAsciiDigits(value)) return null;
  let parsed;
  try {
    parsed = BigInt(value);
  } catch {
    return null; // unreachable given isAsciiDigits, kept for defensiveness
  }
  if (parsed < STEAM_ID64_BASE || parsed > STEAM_ID64_MAX) return null;
  return value;
}
