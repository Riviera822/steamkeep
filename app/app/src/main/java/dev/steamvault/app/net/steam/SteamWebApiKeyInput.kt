package dev.steamvault.app.net.steam

/**
 * Steam Web API key entry -- pure validation plus the submit orchestration
 * (WP 4b.7; ADR-0004, closing the WP 4b.3 "setWebApiKey has no UI path" gap
 * recorded in app/README.md). A port of `web/js/lib/steam-key-form.js`'s
 * `validSteamWebApiKey`/`submitSteamKey` onto this app's field-state shape.
 *
 * [validSteamWebApiKey] mirrors `vault_api.steam_relay.valid_steam_web_api_key`
 * / the web port exactly ("exactly 32 hexadecimal characters") so a
 * client-side rejection agrees with what the server would say.
 */
private const val KEY_LENGTH = 32
private val HEX_RE = Regex("^[0-9A-Fa-f]{32}$")

fun validSteamWebApiKey(value: String): Boolean = value.length == KEY_LENGTH && HEX_RE.matches(value)

/** Outcome of [submitWebApiKey]. [nextFieldValue] is ALWAYS `""` -- see that function's kdoc. */
data class WebApiKeySubmitResult(val ok: Boolean, val error: String?, val nextFieldValue: String)

/**
 * Submits [rawInput] as the Steam Web API key, if it validates.
 *
 * **The load-bearing guarantee, pulled out into its own pure function
 * specifically so it can be mechanically proven rather than "the code looks
 * right" (docs/LEARNINGS.md "Testing discipline"): [WebApiKeySubmitResult
 * .nextFieldValue] is `""` on EVERY path below** -- a validation failure, a
 * [persist] that throws, or success alike. This mirrors
 * `steam-key-form.js::submitSteamKey`'s unconditional `field.value = ""` in
 * a `finally`: ADR-0004's boundary is "never echoed, logged, or leaked", and
 * clearing unconditionally (not just on success) means a mistyped key does
 * not linger in the UI's field state either.
 *
 * No DOM/Compose dependency: [persist] is a plain `(String) -> Unit`
 * callback (production: `SteamIdentityRepository::setWebApiKey`; a test: a
 * spy lambda) -- the caller (`OnboardingController`/`SettingsController`)
 * is the one that actually owns a `mutableStateOf` field and assigns
 * [nextFieldValue] into it, keeping THIS function fully Compose-free and
 * directly unit-testable.
 *
 * @param error a fixed, secret-free string on failure -- never [rawInput]
 *   itself, so a caught exception's message can never smuggle the key back
 *   out through a UI error banner.
 */
fun submitWebApiKey(
    rawInput: String,
    invalidFormatError: String,
    genericError: (Throwable) -> String,
    persist: (String) -> Unit,
): WebApiKeySubmitResult {
    val raw = rawInput.trim()
    if (!validSteamWebApiKey(raw)) {
        return WebApiKeySubmitResult(ok = false, error = invalidFormatError, nextFieldValue = "")
    }
    return try {
        persist(raw)
        WebApiKeySubmitResult(ok = true, error = null, nextFieldValue = "")
    } catch (e: Exception) {
        WebApiKeySubmitResult(ok = false, error = genericError(e), nextFieldValue = "")
    }
}
