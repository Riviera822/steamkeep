// Tests for the KeyValues tokenizer/parser primitives (ParseVDF).
//
// Ported 1:1 from agent/tests/test_tokenizer.py (the executable
// specification, ADR-0005). Every test function name below corresponds
// to the Python test of the same name (minus the test_ prefix casing
// convention difference) unless a comment says otherwise.
package acf

import (
	"strings"
	"testing"
)

func mustParseVDF(t *testing.T, text string) *KeyValues {
	t.Helper()
	result, err := ParseVDF(text)
	if err != nil {
		t.Fatalf("ParseVDF(%q) returned unexpected error: %v", text, err)
	}
	return result
}

func TestSimpleKeyValue(t *testing.T) {
	result := mustParseVDF(t, "\"Root\"\n{\n\t\"key\"\t\"value\"\n}\n")
	if !kvEqualsMap(result, map[string]any{"Root": map[string]any{"key": "value"}}) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestNestedBlocks(t *testing.T) {
	text := `
    "Root"
    {
        "outer"
        {
            "inner"
            {
                "leaf" "1"
            }
        }
    }
    `
	result := mustParseVDF(t, text)
	expected := map[string]any{
		"Root": map[string]any{
			"outer": map[string]any{
				"inner": map[string]any{
					"leaf": "1",
				},
			},
		},
	}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestMultipleSiblingKeys(t *testing.T) {
	text := `"Root" { "a" "1" "b" "2" "c" "3" }`
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"a": "1", "b": "2", "c": "3"}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestEscapedQuoteInValue(t *testing.T) {
	text := `"Root" { "name" "Game \"Special Edition\"" }`
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"name": `Game "Special Edition"`}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestEscapedBackslashInValue(t *testing.T) {
	text := `"Root" { "path" "C:\\Steam\\steamapps" }`
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"path": `C:\Steam\steamapps`}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestEscapedNewlineAndTab(t *testing.T) {
	text := `"Root" { "note" "line1\nline2\ttabbed" }`
	result := mustParseVDF(t, text)
	root, _ := result.Get("Root")
	note, _ := root.(*KeyValues).Get("note")
	if note != "line1\nline2\ttabbed" {
		t.Fatalf("unexpected note value: %q", note)
	}
}

// TestUnknownEscapePreservesBackslash: \q is not a recognized escape. The
// pinned decision (WP 2.1 review, originally made in the since-removed
// Python reference implementation agent/vault_agent/acf.py — see
// acf.go's package doc) is to PRESERVE the backslash rather than
// silently drop it — a single
// backslash before an unescaped char is far more likely to be sloppy
// real-world VDF (an under-escaped Windows path) than an intentional
// escape, and dropping it would silently produce a different, wrong
// string with no error raised.
func TestUnknownEscapePreservesBackslash(t *testing.T) {
	text := `"Root" { "weird" "a\qb" }`
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"weird": `a\qb`}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

// TestUnknownEscapeInWindowsPathDoesNotCorruptIt: the motivating case — a
// mildly malformed (single-backslash, under-escaped) Windows path must
// come through unchanged, not silently mangled into a different, wrong
// path. Explicit attack-case test per the reviewer's re-review list.
func TestUnknownEscapeInWindowsPathDoesNotCorruptIt(t *testing.T) {
	text := `"Root" { "path" "C:\Steam\common" }`
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"path": `C:\Steam\common`}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestLineCommentIsSkipped(t *testing.T) {
	text := `
    "Root"
    {
        // this whole line is a comment and must be ignored
        "key" "value" // trailing comment too
    }
    `
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"key": "value"}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestCommentOnlyFileSection(t *testing.T) {
	text := "\"Root\" {\n// comment\n// another comment\n\"k\" \"v\"\n}"
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"k": "v"}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestUnquotedTokensSupported(t *testing.T) {
	// Steam's own files always quote, but the format itself allows bare
	// tokens as both keys and values.
	text := "Root { key value }"
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"key": "value"}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestDuplicateSiblingKeyLastWinsNoCrash(t *testing.T) {
	text := `"Root" { "dup" "first" "dup" "second" }`
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"dup": "second"}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestUnterminatedQuoteRaises(t *testing.T) {
	_, err := ParseVDF(`"Root" { "key" "unterminated`)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

func TestMissingValueRaises(t *testing.T) {
	_, err := ParseVDF(`"Root" { "key" }`)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

// TestStrayClosingBraceRaises: explicit attack-case test (stray brace) per
// the reviewer's re-review list.
func TestStrayClosingBraceRaises(t *testing.T) {
	_, err := ParseVDF(`"Root" { "key" "value" } }`)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

func TestKeyFollowedByNothingRaises(t *testing.T) {
	_, err := ParseVDF(`"Root" { "key"`)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

func TestUnclosedNestedBlockRaises(t *testing.T) {
	_, err := ParseVDF(`"Root" { "key" "value"`)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

func TestEmptyDocumentParsesToEmptyDict(t *testing.T) {
	result := mustParseVDF(t, "")
	if !kvEqualsMap(result, map[string]any{}) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestOnlyCommentsParsesToEmptyDict(t *testing.T) {
	result := mustParseVDF(t, "// nothing but a comment\n")
	if !kvEqualsMap(result, map[string]any{}) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

// nestedText builds `"Root" { "a" { "a" { ... "leaf" "1" ... } } }` nested
// depth levels deep inside Root's own block — same construction as the
// Python spec's _nested_text helper.
func nestedText(depth int) string {
	inner := `"leaf" "1"`
	for i := 0; i < depth; i++ {
		inner = `"a" { ` + inner + ` }`
	}
	return `"Root" { ` + inner + ` }`
}

func TestModerateNestingWithinLimitParsesFine(t *testing.T) {
	// Real files nest 3 deep; this is comfortably within the 100-level cap.
	result := mustParseVDF(t, nestedText(10))
	rootAny, _ := result.Get("Root")
	node := rootAny.(*KeyValues)
	for i := 0; i < 10; i++ {
		aAny, ok := node.Get("a")
		if !ok {
			t.Fatalf("expected nested 'a' key at depth %d", i)
		}
		node = aAny.(*KeyValues)
	}
	if !kvEqualsMap(node, map[string]any{"leaf": "1"}) {
		t.Fatalf("unexpected innermost node: %+v", node)
	}
}

func TestExcessiveNestingRaisesParseErrorNotRecursionError(t *testing.T) {
	// Depth 150 exceeds the 100-level cap: must raise a *ParseError, never
	// an uncaught stack overflow/panic.
	_, err := ParseVDF(nestedText(150))
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

func TestExtremeNestingRaisesParseErrorNotRecursionError(t *testing.T) {
	// A hostile/corrupt file with thousands of nested braces must still
	// degrade to a clean *ParseError rather than crashing the process.
	_, err := ParseVDF(nestedText(5000))
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

// --------------------------------------------------------------------
// Depth-cap boundary attack cases (explicit per reviewer's re-review
// list: "depth 1500/5000, boundary 99/100"). Not separate Python test
// cases (the Python corpus only exercises 10/150/5000), but implied
// directly by _parse_object's `depth > 100` check — see acf.go's
// maxNestingDepth / parseObject docstring for the exact arithmetic:
// nestedText(n) reaches parse depth 1+n at its innermost "a" block
// (Root's own block is already depth 1), so n=99 -> depth 100 (allowed,
// boundary inclusive) and n=100 -> depth 101 (rejected).
// --------------------------------------------------------------------

func TestNestingAtExactCapBoundaryParsesFine(t *testing.T) {
	if _, err := ParseVDF(nestedText(99)); err != nil {
		t.Fatalf("depth exactly at the 100-level cap must still parse, got error: %v", err)
	}
}

func TestNestingOneOverCapBoundaryRaises(t *testing.T) {
	_, err := ParseVDF(nestedText(100))
	if !isParseError(err) {
		t.Fatalf("expected *ParseError one level past the cap, got %v", err)
	}
}

func TestNesting1500Raises(t *testing.T) {
	_, err := ParseVDF(nestedText(1500))
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

// TestStrayTopLevelBraceAfterConditionalStillDetected: sanity check that
// the conditional-tag skip doesn't accidentally swallow a genuine
// structural error.
func TestStrayTopLevelBraceAfterConditionalStillDetected(t *testing.T) {
	_, err := ParseVDF(`"Root" { "key" "value" [$WIN32] } }`)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

// TestConditionalTagAfterValueIsSkipped: KeyValues platform conditional
// suffix, tolerated and stripped, pair kept regardless of platform.
// Explicit [$WIN32]-variant attack-case test per the reviewer's
// re-review list.
func TestConditionalTagAfterValueIsSkipped(t *testing.T) {
	text := `"Root" { "key" "value" [$WIN32] "other" "2" }`
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"key": "value", "other": "2"}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

func TestConditionalTagOnNestedBlockIsSkipped(t *testing.T) {
	text := `"Root" { "block" { "inner" "1" } [$LINUX] "after" "yes" }`
	result := mustParseVDF(t, text)
	expected := map[string]any{
		"Root": map[string]any{
			"block": map[string]any{"inner": "1"},
			"after": "yes",
		},
	}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

// TestNegatedConditionalTagIsSkipped: [!$WIN32] variant, explicit
// attack-case per the reviewer's re-review list.
func TestNegatedConditionalTagIsSkipped(t *testing.T) {
	text := `"Root" { "key" "value" [!$WIN32] }`
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"key": "value"}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}

// TestUTF8BOMPrefixIsStrippedBeforeTokenizing: a leading BOM must not
// corrupt the first token (see ParseVDF's docstring: without stripping,
// the bareword reader would swallow the BOM together with the following
// quoted key). Explicit "BOM on disk"-adjacent attack-case test (the
// on-disk file variant lives in appmanifest_test.go /
// libraryfolders_test.go); this one covers the in-memory-string path.
func TestUTF8BOMPrefixIsStrippedBeforeTokenizing(t *testing.T) {
	text := "\uFEFF" + `"Root" { "key" "value" }`
	if !strings.HasPrefix(text, "\uFEFF") {
		t.Fatal("test setup error: BOM not present in input")
	}
	result := mustParseVDF(t, text)
	expected := map[string]any{"Root": map[string]any{"key": "value"}}
	if !kvEqualsMap(result, expected) {
		t.Fatalf("unexpected result: %+v", result)
	}
}
