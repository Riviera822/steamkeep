package dev.steamvault.app.net.steam

/**
 * SteamID64 validation (WP 4b.3), mirroring the server's exact rule
 * (`api/vault_api/steam_relay.py::valid_steamid64`) and the web client's
 * (`web/js/lib/steamid.js::validSteamId64`) bit for bit: exactly
 * [DIGITS] ASCII decimal digits, range-checked against the
 * individual-account SteamID64 space (universe 1, account type 1, instance
 * 1, account number 0..2**32-1).
 *
 * **Why this is NOT just `value.toLongOrNull()` plus a range check
 * (LEARNINGS "Android (Phase 4b)"'s BigInt finding, restated for Kotlin).**
 * The web client needed an explicit ASCII-digit guard in FRONT of `BigInt()`
 * because `BigInt` also accepts `0x`/`0o`/`0b`-prefixed numeric strings —
 * `BigInt("0x110000100000000")` is exactly 17 characters and parses to
 * [BASE]'s own value written in hexadecimal, so without the digit guard it
 * would be wrongly accepted. Kotlin's `String.toLongOrNull()` does NOT have
 * that specific hole (it never interprets a leading "0x" as a radix marker;
 * decimal parsing just fails on the literal `x` character) — but it DOES
 * tolerate a leading `+`/`-` sign, which is not a Steam ID digit either.
 * Rather than reason about `toLongOrNull()`'s exact accepted grammar (which
 * could change, or be mis-remembered), [validate] runs its own ASCII-digit
 * walk FIRST, unconditionally, exactly like the web/server validators — a
 * string that passes it contains only `0`-`9`, so whatever `toLongOrNull()`
 * would have additionally tolerated (sign, whitespace, hex/octal/binary
 * prefixes, underscores, non-ASCII look-alike digits) can never reach it.
 */
object SteamId64 {
    const val DIGITS = 17
    const val BASE = 76561197960265728L
    const val MAX = BASE + 0xFFFFFFFFL

    /**
     * @return the canonical 17-digit string form of [value] if it is a
     *   plausible individual-account SteamID64, else `null`.
     */
    fun validate(value: String?): String? {
        if (value == null) return null
        if (value.length != DIGITS) return null
        for (ch in value) {
            if (ch < '0' || ch > '9') return null
        }
        // Safe: every character is already proven to be an ASCII digit, and
        // exactly 17 of them can never overflow a Long (max ~9.2e18, 19
        // digits) -- toLongOrNull() cannot see a sign, whitespace, or a
        // radix prefix at this point, so its own tolerances are moot.
        val parsed = value.toLongOrNull() ?: return null
        if (parsed < BASE || parsed > MAX) return null
        return value
    }
}
