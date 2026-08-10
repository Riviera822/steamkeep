import { test } from "node:test";
import assert from "node:assert/strict";
import { validSteamId64 } from "../js/lib/steamid.js";

/**
 * LITERAL fixture values only — never derived from `steamid.js`'s own
 * exported `STEAM_ID64_BASE`/`STEAM_ID64_MAX` constants. An earlier version
 * of this file imported those constants and built its expectations from
 * them, which made every assertion a tautology: mutating `STEAM_ID64_BASE`
 * or `STEAM_ID64_MAX` in the module moved the test's own yardstick along
 * with the bug, so all four reviewer mutations survived (BASE change, MAX
 * change, the length-check deletion, and the digit-check deletion). Shared
 * with `api/tests/test_steam_relay.py`'s own literal cases
 * (`STEAMID_MIN`/`STEAMID_MAX` = `str(steam_relay.STEAM_ID64_BASE)`/
 * `str(steam_relay.STEAM_ID64_MAX)`, evaluated once against the server at
 * review time) so a drift between the two languages' notion of "the
 * individual-account range" would show up as a literal mismatch, not a
 * silently-matching derived pair.
 */
const BASE = "76561197960265728"; // universe 1, type 1, instance 1, account 0
const MAX = "76561202255233023"; // BASE + 0xFFFFFFFF (account number ceiling)
const REAL_SHAPED = "76561198042117903"; // the mockup's example SteamID64

test("accepts the exact base and max boundary values", () => {
  assert.equal(validSteamId64(BASE), BASE);
  assert.equal(validSteamId64(MAX), MAX);
});

test("accepts a real-shaped SteamID64 well inside the range", () => {
  assert.equal(validSteamId64(REAL_SHAPED), REAL_SHAPED);
});

test("MUTATION PIN (S1, BASE): one below the base is rejected", () => {
  // If STEAM_ID64_BASE were mutated (wrong constant, off-by-one in either
  // direction), this literal — one less than the REAL base — would flip to
  // accepted.
  assert.equal(validSteamId64("76561197960265727"), null);
});

test("MUTATION PIN (S2, MAX): one above the max is rejected", () => {
  assert.equal(validSteamId64("76561202255233024"), null);
});

test("MUTATION PIN (S3, length check): 16 and 18 digits are both rejected", () => {
  assert.equal(validSteamId64("7656119796026572"), null); // 16 digits: any 16-digit
  // number is numerically too small to reach BASE regardless of the length
  // check (the smallest 17-digit number, 10**16, is already below BASE) —
  // this case alone cannot kill a length-check deletion, it only pins the
  // ordinary "too short" behaviour.
  assert.equal(validSteamId64("076561197960265728"), null); // 18 digits: BASE
  // with a leading zero. This one DOES kill a length-check deletion — with
  // the length check removed, BigInt("076561197960265728") parses to
  // exactly BASE (leading zeros are numerically insignificant), which is
  // in-range, so this string would be WRONGLY ACCEPTED if the exact-17
  // length gate were ever dropped (verified against a hand-mutated copy of
  // the function while writing this test).
});

test("rejects a non-digit character mixed into an otherwise correct-length string", () => {
  assert.equal(validSteamId64("7656119804211790a"), null);
  assert.equal(validSteamId64("76561198-4211790"), null);
});

test("whitespace padding is rejected (BigInt's own leading/trailing whitespace tolerance must not leak through)", () => {
  // BigInt(" 6561198042117903") parses successfully (BigInt trims
  // leading/trailing whitespace like Number() does) to 6561198042117903n —
  // a 16-digit magnitude, which the range check alone already rejects
  // (no 16-digit number can reach BASE — see the S3 comment above), so this
  // specific case does not by itself distinguish "digit check present" from
  // "digit check absent". It is still worth pinning as a literal regression
  // case: any future refactor that widens the parse path (e.g. trimming the
  // input before the length check) must not start accepting padded input.
  assert.equal(validSteamId64(" 6561198042117903"), null);
  assert.equal(validSteamId64("6561198042117903 "), null);
});

test("MUTATION PIN (S4, digit check): a 0x-prefixed hex string that BigInt parses to a real, in-range value is rejected", () => {
  // The actual gap the ASCII-digit check closes: BigInt() also accepts
  // 0x/0o/0b-prefixed numeric strings. "0x110000100000000" is exactly 17
  // characters (passes the length check) and is BASE's own value written in
  // hexadecimal (0x110000100000000n === 76561197960265728n) — with the
  // isAsciiDigits() guard removed, BigInt(value) would parse it straight to
  // an in-range result and this would be WRONGLY ACCEPTED. Verified against
  // a hand-mutated copy of validSteamId64 with the digit check deleted:
  // that copy returns the string itself (accepted) for this exact input,
  // while the real, unmutated function must return null.
  assert.equal(validSteamId64("0x110000100000000"), null);
});

test("rejects non-ASCII look-alike digits (Python str.isdigit() trap, LEARNINGS Parsers)", () => {
  // U+0663 ARABIC-INDIC DIGIT THREE repeated 17 times: 17 "digit-like"
  // characters, none of them ASCII 0-9. BigInt() itself already rejects
  // these (SyntaxError, caught -> null), so this pins the try/catch path
  // rather than the isAsciiDigits guard specifically — kept as a named,
  // literal regression case regardless.
  assert.equal(validSteamId64("٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣٣"), null);
});

test("rejects non-string input", () => {
  assert.equal(validSteamId64(76561198042117903), null);
  assert.equal(validSteamId64(null), null);
  assert.equal(validSteamId64(undefined), null);
});

test("rejects a numerically-valid-looking value that is merely the wrong shape (17 zeros)", () => {
  assert.equal(validSteamId64("00000000000000000"), null);
});
