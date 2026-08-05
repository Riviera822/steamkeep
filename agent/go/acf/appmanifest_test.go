// Tests for appmanifest_<appid>.acf extraction, incl. StateFlags
// variants. Ported 1:1 from agent/tests/test_appmanifest.py.
//
// StateFlags semantics (documented in agent/vault_agent/acf.py): bit 4
// means "fully installed". Empirically verified against every real
// appmanifest on the dev machine's c:\steam install (all currently show
// StateFlags == 4). The update-required (6) and partial (2) fixtures are
// synthetic, modeled on Valve's publicly documented StateFlags bit
// combinations.
package acf

import (
	"os"
	"path/filepath"
	"testing"
	"unicode/utf8"
)

const fakeLibrary = "C:/VaultTest/SteamMain"

func TestFullyInstalledApp(t *testing.T) {
	app, err := ParseAppManifestFile(fixture("appmanifest_installed.acf"), fakeLibrary)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if app.AppID != "999001" {
		t.Errorf("AppID = %q, want 999001", app.AppID)
	}
	if app.Name != "Vault Test Game A" {
		t.Errorf("Name = %q, want Vault Test Game A", app.Name)
	}
	if app.StateFlags != 4 {
		t.Errorf("StateFlags = %d, want 4", app.StateFlags)
	}
	if !app.Installed() {
		t.Error("Installed() = false, want true")
	}
	if app.SizeOnDisk == nil || *app.SizeOnDisk != 1234567890 {
		t.Errorf("SizeOnDisk = %v, want 1234567890", app.SizeOnDisk)
	}
	if app.LibraryPath != fakeLibrary {
		t.Errorf("LibraryPath = %q, want %q", app.LibraryPath, fakeLibrary)
	}
}

func TestUpdateRequiredAppIsStillInstalled(t *testing.T) {
	// StateFlags 6 = installed (4) + update-required (2): still on disk.
	app, err := ParseAppManifestFile(fixture("appmanifest_update_required.acf"), fakeLibrary)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if app.StateFlags != 6 {
		t.Errorf("StateFlags = %d, want 6", app.StateFlags)
	}
	if !app.Installed() {
		t.Error("Installed() = false, want true")
	}
}

func TestPartialDownloadAppIsNotInstalled(t *testing.T) {
	// StateFlags 2 = update-required only, installed bit (4) not set.
	app, err := ParseAppManifestFile(fixture("appmanifest_partial.acf"), fakeLibrary)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if app.StateFlags != 2 {
		t.Errorf("StateFlags = %d, want 2", app.StateFlags)
	}
	if app.Installed() {
		t.Error("Installed() = true, want false")
	}
}

func TestMissingSizeOnDiskIsTolerated(t *testing.T) {
	app, err := ParseAppManifestFile(fixture("appmanifest_missing_size.acf"), fakeLibrary)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if app.SizeOnDisk != nil {
		t.Errorf("SizeOnDisk = %v, want nil", app.SizeOnDisk)
	}
	if !app.Installed() {
		t.Error("Installed() = false, want true")
	}
}

func TestCorruptFileRaisesParseError(t *testing.T) {
	_, err := ParseAppManifestFile(fixture("appmanifest_corrupt.acf"), fakeLibrary)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

func TestMissingAppstateRootRaisesParseError(t *testing.T) {
	_, err := ParseAppManifestFile(fixture("appmanifest_missing_appstate.acf"), fakeLibrary)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

func TestMissingFileRaisesParseError(t *testing.T) {
	_, err := ParseAppManifestFile(fixture("does_not_exist.acf"), fakeLibrary)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

// --------------------------------------------------------------------
// Strict digit grammar (parseStrictUint) — appid / StateFlags /
// SizeOnDisk.
//
// Deliberately stricter than Go's own liberal-parsing helpers, and
// pinned to match Python's int() rejections exactly, since this parser
// is a port of the executable specification (ADR-0005). One documented
// exception: integer overflow on a digit string too large for a machine
// int (Python's bignums have no such ceiling) — see strictuint.go's
// "KNOWN, DELIBERATE DIVERGENCE" doc comment and the overflow tests
// below.
// --------------------------------------------------------------------

// TestParseStrictUintAccepts: one subtest per Python
// @pytest.mark.parametrize case (test_parse_strict_uint_accepts), so Go's
// per-test reporting granularity matches pytest's.
func TestParseStrictUintAccepts(t *testing.T) {
	cases := []struct {
		name     string
		raw      string
		expected int
	}{
		{"plain", "4", 4},
		{"zero", "0", 0},
		{"leading_zeros", "004", 4}, // leading zeros tolerated -- matches int() AND strconv.Atoi
		{"large", "999999999999", 999999999999},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			got, ok := parseStrictUint(c.raw)
			if !ok || got != c.expected {
				t.Errorf("parseStrictUint(%q) = (%d, %v), want (%d, true)", c.raw, got, ok, c.expected)
			}
		})
	}
}

// TestParseStrictUintRejects: one subtest per Python
// @pytest.mark.parametrize case (test_parse_strict_uint_rejects),
// including the Arabic-Indic-digit and underscore-separator attack cases
// called out explicitly in the reviewer's re-review list.
func TestParseStrictUintRejects(t *testing.T) {
	cases := []struct {
		name string
		raw  string
	}{
		{"empty", ""},
		{"surrounding_whitespace", " 4 "}, // int() accepts, strconv.Atoi does not
		{"leading_plus", "+4"},            // int() accepts, strconv.Atoi(base 10) does not
		{"leading_minus", "-4"},
		{"underscore_digit_group_separator", "1_0"}, // int() parses as 10
		{"arabic_indic_digit_prefix", "\u06664"},    // '٦' (six) + ascii '4': non-ASCII digit, int() -> 64
		{"decimal_point", "4.0"},
		{"hex_prefix", "0x4"},
		{"not_a_number", "notanumber"},
		{"trailing_whitespace", "4 "},
		{"leading_whitespace", " 4"},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			if _, ok := parseStrictUint(c.raw); ok {
				t.Errorf("parseStrictUint(%q) accepted, want rejected", c.raw)
			}
		})
	}
}

func TestAppidWithSurroundingWhitespaceRaises(t *testing.T) {
	_, err := ParseAppManifestFile(fixture("appmanifest_appid_whitespace.acf"), fakeLibrary)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

// TestAppidNonNumericRaises: explicit non-numeric-digit attack-case test.
func TestAppidNonNumericRaises(t *testing.T) {
	_, err := ParseAppManifestFile(fixture("appmanifest_appid_nonnumeric.acf"), fakeLibrary)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

func TestStateFlagsWithSurroundingWhitespaceRaises(t *testing.T) {
	// StateFlags is required; a value that fails the strict grammar must
	// raise, not silently coerce via liberal int parsing.
	_, err := ParseAppManifestFile(fixture("appmanifest_stateflags_whitespace.acf"), fakeLibrary)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

func TestSizeOnDiskWithLiberalIntFormIsToleratedAsNone(t *testing.T) {
	// SizeOnDisk is the one field that degrades to nil instead of raising
	// -- but "+123" must become nil, not silently be accepted as 123.
	app, err := ParseAppManifestFile(fixture("appmanifest_sizeondisk_liberal.acf"), fakeLibrary)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if app.SizeOnDisk != nil {
		t.Errorf("SizeOnDisk = %v, want nil", app.SizeOnDisk)
	}
	if app.AppID != "999008" {
		t.Errorf("AppID = %q, want 999008 (rest of the record still parses fine)", app.AppID)
	}
}

func TestAppidLeadingZerosAreTolerated(t *testing.T) {
	// Consistent with parseStrictUint: leading zeros are not corruption.
	text := `"AppState" { "appid" "0042" "name" "Padded" "StateFlags" "4" }`
	app, err := ParseAppManifest(text, fakeLibrary)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if app.AppID != "0042" {
		t.Errorf("AppID = %q, want 0042", app.AppID)
	}
}

// TestUTF8BOMPrefixedAppmanifestFileParsesCorrectly: written as raw bytes
// (not via a fixture file) to prove the BOM-stripping file read handles a
// real BOM'd file on disk. Explicit "BOM on disk" attack-case test per
// the reviewer's re-review list.
func TestUTF8BOMPrefixedAppmanifestFileParsesCorrectly(t *testing.T) {
	content := "\"AppState\"\n{\n\t\"appid\"\t\t\"999009\"\n\t\"name\"\t\t\"BOM Game\"\n" +
		"\t\"StateFlags\"\t\t\"4\"\n}\n"
	bomPath := filepath.Join(t.TempDir(), "appmanifest_999009.acf")
	if err := os.WriteFile(bomPath, append([]byte{0xEF, 0xBB, 0xBF}, content...), 0o644); err != nil {
		t.Fatalf("failed to write fixture: %v", err)
	}

	app, err := ParseAppManifestFile(bomPath, fakeLibrary)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if app.AppID != "999009" {
		t.Errorf("AppID = %q, want 999009", app.AppID)
	}
	if app.Name != "BOM Game" {
		t.Errorf("Name = %q, want BOM Game", app.Name)
	}
	if !app.Installed() {
		t.Error("Installed() = false, want true")
	}
}

// TestInvalidUTF8AppmanifestFileRaisesParseError: a manifest containing a
// raw 0xE9 byte (a Latin-1/cp1252 'é', as a naively-saved non-ASCII game
// name like "Pokémon" might produce) is not valid UTF-8 on its own.
// BLOCKER fix (reviewer re-review): Python's
// read_text(encoding="utf-8-sig", errors="strict") raises
// UnicodeDecodeError on this — wrapped into VdfParseError, file skipped
// with a warning. Before this fix, Go's `string(data)` silently accepted
// it, producing a U+FFFD-mangled name with no error at all. Must now
// raise a *ParseError, matching the Python behavior.
func TestInvalidUTF8AppmanifestFileRaisesParseError(t *testing.T) {
	// "Pok\xe9mon" -- 0xE9 is not a valid UTF-8 continuation/lead byte in
	// this position, so the file as a whole is invalid UTF-8.
	content := []byte("\"AppState\"\n{\n\t\"appid\"\t\t\"999010\"\n\t\"name\"\t\t\"Pok\xe9mon Cafe\"\n" +
		"\t\"StateFlags\"\t\t\"4\"\n}\n")
	if utf8.Valid(content) {
		t.Fatal("test setup error: fixture content is unexpectedly valid UTF-8")
	}
	badPath := filepath.Join(t.TempDir(), "appmanifest_999010.acf")
	if err := os.WriteFile(badPath, content, 0o644); err != nil {
		t.Fatalf("failed to write fixture: %v", err)
	}

	_, err := ParseAppManifestFile(badPath, fakeLibrary)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError for invalid UTF-8, got %v", err)
	}
}

// --------------------------------------------------------------------
// Integer-overflow divergence (KNOWN, DELIBERATE — see strictuint.go and
// agent/README.md's "Known divergences from the Python spec"): a digit
// string too large for a 64-bit machine int is grammatically valid but
// numerically unrepresentable in Go, whereas Python's bignum int accepts
// it without limit. One test per field, pinning each of the three
// concrete consequences.
// --------------------------------------------------------------------

// hugeDigitString is comfortably beyond int64's ~9.2e18 ceiling (30
// digits of '9'), while still being pure ASCII digits -- i.e.
// grammatically valid, just numerically too large for a machine int.
const hugeDigitString = "999999999999999999999999999999"

func TestSizeOnDiskOverflowIsToleratedAsNoneUnlikePythonBignum(t *testing.T) {
	// Consequence 1: SizeOnDisk silently becomes nil, same as any other
	// tolerated/malformed value -- Python would instead return the huge
	// integer itself.
	text := `"AppState" { "appid" "999011" "name" "Overflow Size" "StateFlags" "4" "SizeOnDisk" "` +
		hugeDigitString + `" }`
	app, err := ParseAppManifest(text, fakeLibrary)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if app.SizeOnDisk != nil {
		t.Errorf("SizeOnDisk = %v, want nil (overflow tolerated like any other malformed value)", app.SizeOnDisk)
	}
}

func TestStateFlagsOverflowRejectsWholeRecordUnlikePythonBignum(t *testing.T) {
	// Consequence 2: an oversized StateFlags is a required field, so the
	// WHOLE record is rejected as corrupt -- Python would instead accept
	// it with an enormous StateFlags value.
	text := `"AppState" { "appid" "999012" "name" "Overflow StateFlags" "StateFlags" "` +
		hugeDigitString + `" }`
	_, err := ParseAppManifest(text, fakeLibrary)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError for oversized StateFlags, got %v", err)
	}
}

func TestAppidOverflowRejectsWholeRecordUnlikePythonBignum(t *testing.T) {
	// Consequence 3: appid is rejected the same way despite being stored
	// as a plain string and never used arithmetically -- kept for
	// consistency with the one shared strict-digit-string house rule
	// (see strictuint.go's doc comment), not because appid needs the
	// numeric bound.
	text := `"AppState" { "appid" "` + hugeDigitString + `" "name" "Overflow Appid" "StateFlags" "4" }`
	_, err := ParseAppManifest(text, fakeLibrary)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError for oversized appid, got %v", err)
	}
}
