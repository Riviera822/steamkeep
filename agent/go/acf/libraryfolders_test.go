// Tests for libraryfolders.vdf parsing: modern + old flat formats.
// Ported 1:1 from agent/tests/test_libraryfolders.py.
//
// Ground truth for the modern format was the real file at
// c:\steam\steamapps\libraryfolders.vdf on the dev machine (single
// library, numbered block "0" with "path"/"apps" keys — see
// agent/README.md for the fixture policy). The old flat format fixture
// is synthetic, modeled on the documented pre-2019 Steam client format.
//
// NOTE on path comparison: the Python spec wraps paths in pathlib.Path,
// whose equality normalizes '/' and '\' as equivalent separators — but
// only on Windows (Path is WindowsPath there); the Python test suite
// runs on Windows for exactly this reason. This Go port keeps paths as
// plain strings (no Path abstraction — ADR-0005 doesn't call for one),
// so expectations here are the literal unescaped string the parser
// produces: Steam's own files use backslashes, and a "\\" escape in the
// VDF source unescapes to one literal backslash — so expected values
// use backslashes, not forward slashes.
package acf

import (
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"unicode/utf8"
)

func TestModernFormatMultipleLibraries(t *testing.T) {
	paths, err := ParseLibraryFoldersFile(fixture("libraryfolders_modern.vdf"))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	expected := []string{`C:\VaultTest\SteamMain`, `D:\VaultTest\SteamLibrary2`}
	// NOTE: order matters here — it proves KeyValues preserves file
	// (insertion) order rather than a Go map's randomized iteration
	// order. See acf.go's KeyValues doc comment.
	if !reflect.DeepEqual(paths, expected) {
		t.Fatalf("paths = %v, want %v", paths, expected)
	}
}

func TestOldFlatFormatSkipsNonNumericKeys(t *testing.T) {
	paths, err := ParseLibraryFoldersFile(fixture("libraryfolders_old_flat.vdf"))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	expected := []string{`E:\VaultTest\OldStyleLibrary`, `F:\VaultTest\AnotherOldLibrary`}
	if !reflect.DeepEqual(paths, expected) {
		t.Fatalf("paths = %v, want %v", paths, expected)
	}
}

func TestLibraryEntryWithoutAppsKeyStillYieldsPath(t *testing.T) {
	paths, err := ParseLibraryFoldersFile(fixture("libraryfolders_no_apps_key.vdf"))
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	expected := []string{`C:\VaultTest\SteamMain`}
	if !reflect.DeepEqual(paths, expected) {
		t.Fatalf("paths = %v, want %v", paths, expected)
	}
}

func TestLibraryfoldersCorruptFileRaisesParseError(t *testing.T) {
	_, err := ParseLibraryFoldersFile(fixture("libraryfolders_corrupt.vdf"))
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

func TestLibraryfoldersMissingFileRaisesParseError(t *testing.T) {
	_, err := ParseLibraryFoldersFile(fixture("does_not_exist.vdf"))
	if !isParseError(err) {
		t.Fatalf("expected *ParseError, got %v", err)
	}
}

// TestDuplicateNumberedEntryLastWins: a malformed/hostile file with the
// same library index twice: the underlying dict-level "last wins" rule
// (ParseVDF/KeyValues.set) means only the second path survives -- it
// never becomes a 2-entry list. Explicit "duplicate numbered libraries"
// attack-case test per the reviewer's re-review list.
func TestDuplicateNumberedEntryLastWins(t *testing.T) {
	text := `
    "libraryfolders"
    {
        "0" { "path" "C:\\VaultTest\\First" }
        "0" { "path" "C:\\VaultTest\\Second" }
    }
    `
	paths, err := ParseLibraryFolders(text)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	expected := []string{`C:\VaultTest\Second`}
	if !reflect.DeepEqual(paths, expected) {
		t.Fatalf("paths = %v, want %v", paths, expected)
	}
}

// TestUTF8BOMPrefixedLibraryfoldersFileParsesCorrectly: written as raw
// bytes (not via a fixture file) to prove the BOM-stripping file read
// handles a real BOM'd file on disk. Explicit "BOM on disk" attack-case
// test per the reviewer's re-review list.
func TestUTF8BOMPrefixedLibraryfoldersFileParsesCorrectly(t *testing.T) {
	content := "\"libraryfolders\"\n{\n\t\"0\"\n\t{\n\t\t\"path\"\t\t\"C:\\\\VaultTest\\\\BomLib\"\n\t}\n}\n"
	bomPath := filepath.Join(t.TempDir(), "libraryfolders.vdf")
	if err := os.WriteFile(bomPath, append([]byte{0xEF, 0xBB, 0xBF}, content...), 0o644); err != nil {
		t.Fatalf("failed to write fixture: %v", err)
	}

	paths, err := ParseLibraryFoldersFile(bomPath)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	expected := []string{`C:\VaultTest\BomLib`}
	if !reflect.DeepEqual(paths, expected) {
		t.Fatalf("paths = %v, want %v", paths, expected)
	}
}

// TestInvalidUTF8LibraryfoldersFileRaisesParseError: same BLOCKER fix as
// TestInvalidUTF8AppmanifestFileRaisesParseError (see that test's
// comment) applied to the OTHER readFileStripBOM caller — a raw 0xE9
// byte (as in a naively-saved non-ASCII library label, e.g. "café
// Steam library") makes the whole file invalid UTF-8, which must raise a
// *ParseError rather than silently decode to a U+FFFD-mangled path.
func TestInvalidUTF8LibraryfoldersFileRaisesParseError(t *testing.T) {
	content := []byte("\"libraryfolders\"\n{\n\t\"0\"\n\t{\n\t\t\"path\"\t\t\"C:\\\\Caf\xe9Library\"\n\t}\n}\n")
	if utf8.Valid(content) {
		t.Fatal("test setup error: fixture content is unexpectedly valid UTF-8")
	}
	badPath := filepath.Join(t.TempDir(), "libraryfolders.vdf")
	if err := os.WriteFile(badPath, content, 0o644); err != nil {
		t.Fatalf("failed to write fixture: %v", err)
	}

	_, err := ParseLibraryFoldersFile(badPath)
	if !isParseError(err) {
		t.Fatalf("expected *ParseError for invalid UTF-8, got %v", err)
	}
}
