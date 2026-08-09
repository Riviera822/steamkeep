package acf

import "strconv"

// parseStrictUint parses raw as a non-negative integer using a strict
// grammar, or returns ok=false (never panics) if it doesn't match.
//
// Accepted grammar: one or more ASCII digit characters (0-9) and nothing
// else. Leading zeros are tolerated ("004" -> 4).
//
// Deliberately stricter than Go's strconv.Atoi would be if fed
// untrimmed/liberal input, and pinned to match Python's int() rejections
// documented on the executable specification's _parse_strict_uint
// (agent/vault_agent/acf.py, removed at the Phase-2 close-out, WP 2.6 —
// see acf.go's package doc): no surrounding whitespace (" 4 "), no leading
// +/- sign ("+4"/"-4"), no digit-group separators ("1_0"), no non-ASCII
// Unicode digits (e.g. Arabic-Indic "٤"). strconv.Atoi itself already
// rejects all of those (unlike Python's int()) EXCEPT a leading sign
// ("+4"/"-4" both parse fine via Atoi) and does not accept underscores by
// default either — the explicit ASCII-digit-only pre-check below is what
// closes the sign gap and doubles as documentation of the exact grammar.
//
// # KNOWN, DELIBERATE DIVERGENCE: integer overflow
//
// For every input this package's test corpus and any real Steam-written
// file exercise, this function agrees with Python's _parse_strict_uint
// exactly. It does NOT agree for a digit string too large to fit a
// machine int (Go's int is 64-bit on every ADR-0005 build target, so the
// practical threshold is ~9.2e18, 19 digits) — Python's int is
// arbitrary-precision and has no such ceiling. This is treated as an
// acceptable, deliberate simplification (pulling in math/big for a field
// that is a StateFlags bitmask, an appid, or a byte count — none of
// which any real Steam client has ever written anywhere near that large
// — would be unused complexity for a corruption case with no realistic
// trigger), but it must be stated plainly rather than silently
// glossed over, with its three concrete consequences (see
// agent/README.md's "Known divergences from the Python spec" for the
// user-facing summary; pinned by tests in appmanifest_test.go):
//
//  1. SizeOnDisk: an oversized digit string is tolerated exactly like a
//     grammatically-invalid one — it becomes size_on_disk = nil. Python
//     would instead return the actual (huge) integer value. Silent
//     information loss, but shaped like the existing "tolerated field"
//     contract, not a crash.
//  2. StateFlags: an oversized digit string makes the WHOLE appmanifest
//     record rejected (a *ParseError, same as any other StateFlags
//     grammar violation — the field is required, not tolerated). Python
//     would instead accept the record with an enormous StateFlags value.
//     A corrupt-looking-only-in-this-one-way file is skipped in Go where
//     Python would keep it.
//  3. appid: same rejection as StateFlags — despite appid being stored
//     as a plain string and NEVER converted to a number for any real use
//     in this package, routing its grammar check through this same
//     function means an oversized-but-otherwise-valid all-digit appid
//     string is rejected too. This is the most avoidable-looking of the
//     three (appid doesn't need the numeric value at all), but is kept
//     for one house-rule reason: this is the SAME strict-digit-string
//     check api/vault_api/deletion.py's coerce_positive_id uses for
//     appid/depotid fields project-wide (see agent/README.md's "Integer
//     field grammar" section) — special-casing appid here to skip the
//     range check would break that one consistent rule for a case with,
//     again, no realistic real-world trigger.
func parseStrictUint(raw string) (int, bool) {
	if raw == "" {
		return 0, false
	}
	for _, r := range raw {
		if r < '0' || r > '9' {
			return 0, false
		}
	}
	n, err := strconv.Atoi(raw)
	if err != nil {
		// Only reachable for a digit string too large for a machine int
		// (never for a grammar violation, which the loop above already
		// rejected) — see the KNOWN DIVERGENCE doc above.
		return 0, false
	}
	return n, true
}
