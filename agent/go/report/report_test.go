package report

import (
	"strconv"
	"strings"
	"testing"

	"github.com/Riviera822/steamvault/agent/acf"
)

func installedApp(appid, name string, stateFlags int) acf.InstalledApp {
	return acf.InstalledApp{AppID: appid, Name: name, StateFlags: stateFlags, LibraryPath: "/lib"}
}

func TestBuildReport_FiltersToInstalledOnly(t *testing.T) {
	apps := []acf.InstalledApp{
		installedApp("440", "TF2", 4),     // fully installed
		installedApp("570", "Dota 2", 6),  // update required, still installed (bit 4 set)
		installedApp("730", "CS2", 2),     // update required, NOT installed (bit 4 unset)
		installedApp("999", "Partial", 0), // nothing set
	}
	payload, err := BuildReport(apps, "gaming-pc")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	want := []int{440, 570}
	if !equalInts(payload.AppIDs, want) {
		t.Errorf("AppIDs = %v, want %v", payload.AppIDs, want)
	}
	if payload.ClientID != "gaming-pc" {
		t.Errorf("ClientID = %q, want %q", payload.ClientID, "gaming-pc")
	}
}

func TestBuildReport_DedupesByAppID(t *testing.T) {
	// Same appid appearing twice (e.g. duplicate manifest across libraries
	// that discover.go itself already tries to prevent, but the report
	// layer must not assume that invariant either).
	apps := []acf.InstalledApp{
		installedApp("440", "TF2", 4),
		installedApp("440", "TF2 (dup)", 4),
	}
	payload, err := BuildReport(apps, "pc")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(payload.AppIDs) != 1 || payload.AppIDs[0] != 440 {
		t.Errorf("AppIDs = %v, want [440]", payload.AppIDs)
	}
}

func TestBuildReport_SortsAscending(t *testing.T) {
	apps := []acf.InstalledApp{
		installedApp("999", "C", 4),
		installedApp("100", "A", 4),
		installedApp("500", "B", 4),
	}
	payload, err := BuildReport(apps, "pc")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	want := []int{100, 500, 999}
	if !equalInts(payload.AppIDs, want) {
		t.Errorf("AppIDs = %v, want %v (sorted ascending)", payload.AppIDs, want)
	}
}

func TestBuildReport_EmptyInstalledListIsLegitimate(t *testing.T) {
	// ADR-0002 / api/README.md: an empty appids list is a legitimate
	// report (a machine with nothing installed), not an error.
	payload, err := BuildReport(nil, "empty-pc")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(payload.AppIDs) != 0 {
		t.Errorf("AppIDs = %v, want empty", payload.AppIDs)
	}
}

func TestBuildReport_RejectsAppIDZero(t *testing.T) {
	// "0" is a grammatically valid digit string (acf's own parser would
	// accept it), but the server's AppId type requires ge=1 - reject
	// locally rather than let the server 422 it.
	apps := []acf.InstalledApp{installedApp("0", "Weird", 4)}
	_, err := BuildReport(apps, "pc")
	if err == nil {
		t.Fatal("expected an error for appid 0, got nil")
	}
	if _, ok := err.(*ValidationError); !ok {
		t.Errorf("error type = %T, want *ValidationError", err)
	}
}

func TestBuildReport_PropagatesClientIDValidation(t *testing.T) {
	apps := []acf.InstalledApp{installedApp("440", "TF2", 4)}
	_, err := BuildReport(apps, "")
	if err == nil {
		t.Fatal("expected an error for an empty client_id")
	}
}

func TestBuildReport_RejectsMoreThanMaxAppIDs(t *testing.T) {
	apps := make([]acf.InstalledApp, 0, MaxAppIDs+1)
	for i := 1; i <= MaxAppIDs+1; i++ {
		apps = append(apps, installedApp(strconv.Itoa(i), "game", 4))
	}
	_, err := BuildReport(apps, "pc")
	if err == nil {
		t.Fatal("expected an error when exceeding MaxAppIDs")
	}
}

func TestValidateClientID_Valid(t *testing.T) {
	cases := []string{"pc", "gaming-pc", "steam-deck-01", strings.Repeat("a", MaxClientIDLength)}
	for _, id := range cases {
		if err := ValidateClientID(id); err != nil {
			t.Errorf("ValidateClientID(%q) = %v, want nil", id, err)
		}
	}
}

func TestValidateClientID_RejectsEmpty(t *testing.T) {
	if err := ValidateClientID(""); err == nil {
		t.Error("expected error for empty client_id")
	}
}

func TestValidateClientID_RejectsTooLong(t *testing.T) {
	id := strings.Repeat("a", MaxClientIDLength+1)
	if err := ValidateClientID(id); err == nil {
		t.Error("expected error for client_id longer than 64 chars")
	}
}

func TestValidateClientID_RejectsSurroundingWhitespace(t *testing.T) {
	for _, id := range []string{" pc", "pc ", " pc ", "\tpc"} {
		if err := ValidateClientID(id); err == nil {
			t.Errorf("ValidateClientID(%q): expected error for surrounding whitespace", id)
		}
	}
}

func TestValidateClientID_RejectsControlCharacters(t *testing.T) {
	for _, id := range []string{"pc\nname", "pc\x00name", "pc\tname", "pc\rname"} {
		if err := ValidateClientID(id); err == nil {
			t.Errorf("ValidateClientID(%q): expected error for control character", id)
		}
	}
}

// --- S1 (WP 2.2 review): unicode.IsPrint, not just IsControl/Zl/Zp, is
// required to match the server's Python str.isprintable() rule. These
// pin the exact cases the review's 46-case parity sweep flagged as
// missed by the earlier, narrower check: Cf (invisible format
// characters), non-ASCII Zs (NBSP), Co (private use), and Cn
// (unassigned) - plus the positive case (a single ordinary emoji stays
// allowed) that must NOT regress while fixing the above.

func TestValidateClientID_RejectsInnerNBSP(t *testing.T) {
	// U+00A0 NO-BREAK SPACE: category Zs, but NOT the ASCII space - Go's
	// unicode.IsPrint (like Python's str.isprintable()) only special-cases
	// U+0020 among Zs, so an INNER (non-trimmed) NBSP must still be
	// rejected even though it "looks like" whitespace that TrimSpace
	// would otherwise catch only at the edges.
	id := "gaming pc"
	if err := ValidateClientID(id); err == nil {
		t.Errorf("ValidateClientID(%q) (inner NBSP): expected an error, got nil", id)
	}
}

func TestValidateClientID_RejectsZeroWidthSpace(t *testing.T) {
	// U+200B ZERO WIDTH SPACE: category Cf (format), invisible - two ids
	// differing only by a ZWSP would be visually indistinguishable but
	// compare unequal as identity keys.
	id := "gaming​pc"
	if err := ValidateClientID(id); err == nil {
		t.Errorf("ValidateClientID(%q) (ZWSP): expected an error, got nil", id)
	}
}

func TestValidateClientID_RejectsZWJJoinedCompoundEmoji(t *testing.T) {
	// A "family" emoji built from base emoji joined by U+200D ZERO WIDTH
	// JOINER (category Cf). The server's Python str.isprintable() rejects
	// this too (confirmed by the review's parity sweep) - the local check
	// must agree, not accept something the server would then 422.
	id := "\U0001F468‍\U0001F469‍\U0001F467" // man+ZWJ+woman+ZWJ+girl
	if err := ValidateClientID(id); err == nil {
		t.Errorf("ValidateClientID(%q) (ZWJ-joined emoji): expected an error, got nil", id)
	}
}

func TestValidateClientID_RejectsPrivateUseCodepoint(t *testing.T) {
	// U+E000: first codepoint of the Basic Multilingual Plane Private Use
	// Area (category Co) - by definition has no assigned meaning outside
	// a private agreement, so it is never legitimately part of a hostname.
	id := "gamingpc"
	if err := ValidateClientID(id); err == nil {
		t.Errorf("ValidateClientID(%q) (private-use Co): expected an error, got nil", id)
	}
}

func TestValidateClientID_RejectsUnassignedCodepoint(t *testing.T) {
	// U+FFFF: a permanently-reserved Unicode "noncharacter" - guaranteed
	// by the Unicode Standard to never be assigned a character, making it
	// a stable choice for pinning Cn (unassigned) rejection across Unicode
	// table updates in future Go versions.
	id := "gaming￿pc"
	if err := ValidateClientID(id); err == nil {
		t.Errorf("ValidateClientID(%q) (unassigned/noncharacter Cn): expected an error, got nil", id)
	}
}

func TestValidateClientID_AllowsPlainEmoji(t *testing.T) {
	// Positive case that must NOT regress: a single ordinary emoji
	// (U+1F3AE, category So - Symbol, Other) is visible/printable and
	// stays a valid client_id, same as the server accepts it.
	id := "\U0001F3AE"
	if err := ValidateClientID(id); err != nil {
		t.Errorf("ValidateClientID(%q) (plain emoji) = %v, want nil", id, err)
	}
}

func TestValidateClientID_RejectsDotAndDotDot(t *testing.T) {
	for _, id := range []string{".", ".."} {
		if err := ValidateClientID(id); err == nil {
			t.Errorf("ValidateClientID(%q): expected error", id)
		}
	}
}

func TestValidateClientID_DotDotDotIsAllowed(t *testing.T) {
	// Only the exact strings "." and ".." are rejected, not anything
	// containing them (mirrors the server's `value in {".", ".."}` check,
	// not a substring check).
	if err := ValidateClientID("..."); err != nil {
		t.Errorf("ValidateClientID(\"...\") = %v, want nil", err)
	}
}

func TestValidateClientID_CountsCharactersNotBytes(t *testing.T) {
	// Multi-byte UTF-8 characters must be counted as one character each
	// (mirrors Python's len()), not as their byte length - otherwise a
	// 64-character non-ASCII client_id would be wrongly rejected.
	id := strings.Repeat("é", MaxClientIDLength) // 2 bytes each in UTF-8, 64 runes
	if err := ValidateClientID(id); err != nil {
		t.Errorf("ValidateClientID(64 non-ASCII chars) = %v, want nil", err)
	}
}

// --- small local helpers (avoid importing strconv/strings just for these) ---

func equalInts(a, b []int) bool {
	if len(a) != len(b) {
		return false
	}
	for i := range a {
		if a[i] != b[i] {
			return false
		}
	}
	return true
}
