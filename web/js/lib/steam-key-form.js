/**
 * Steam Web API key entry — pure validation plus the submit orchestration
 * (WP 4a.6; ADR-0004 addendum, api/README.md "Steam Web API relay").
 *
 * Pulled out of the Settings/onboarding views for the same reason
 * docs/LEARNINGS.md's "Testing discipline" section asks for everywhere
 * else: the one guarantee that actually matters here — **the typed key is
 * never retained after a submit attempt, win or lose** — has to be
 * mechanically provable, not just "the code looks right". `submitSteamKey`
 * below takes a plain `{value}`-shaped field object (a real `<input>` in
 * the view, an inert stand-in in tests) and a `client` with a
 * `putSteamKey(key)` method (the real `api.putSteamKey`, or a spy in
 * tests) — no DOM, no fetch, no browser required to prove the guarantee.
 *
 * `validSteamWebApiKey` mirrors `vault_api.steam_relay.valid_steam_web_api_key`
 * exactly (api/README.md: "422 unless key is exactly 32 hexadecimal
 * characters") so a client-side rejection and the server's 422 always agree.
 */

const KEY_LENGTH = 32;
const HEX_RE = /^[0-9A-Fa-f]{32}$/;

/** @param {unknown} value @returns {boolean} */
export function validSteamWebApiKey(value) {
  if (typeof value !== "string") return false;
  if (value.length !== KEY_LENGTH) return false;
  return HEX_RE.test(value);
}

/**
 * Submit whatever is currently in `field.value` as the relay's Web API key.
 *
 * The typed value is read exactly once (into `raw`), and `field.value` is
 * unconditionally cleared in a `finally` — on a validation failure, a
 * network error, a rejected `PUT`, or success alike. This is deliberate:
 * ADR-0004's boundary is "never echoed, logged, or leaked", and clearing
 * unconditionally (rather than only on success) means a mistyped key does
 * not linger in the DOM either. Callers that want to help the user fix a
 * typo show `result.error`, not the old value.
 *
 * `result.error` (when present) is always a short human string derived
 * from the caught `ApiError`'s `detail`/`message` — never `raw` itself, so
 * a thrown error can never smuggle the key back out through the UI's error
 * toast.
 *
 * @param {{value: string}} field
 * @param {{putSteamKey: (key: string) => Promise<unknown>}} client
 * @returns {Promise<{ok: true, result: unknown} | {ok: false, error: string}>}
 */
export async function submitSteamKey(field, client) {
  const raw = typeof field.value === "string" ? field.value.trim() : "";
  try {
    if (!validSteamWebApiKey(raw)) {
      return { ok: false, error: "Enter exactly 32 hexadecimal characters (0-9, a-f)." };
    }
    const result = await client.putSteamKey(raw);
    return { ok: true, result };
  } catch (err) {
    const detail = err && typeof err.detail === "string" && err.detail ? err.detail : null;
    return { ok: false, error: detail || (err && err.message) || "Request failed." };
  } finally {
    field.value = "";
  }
}
