package acf

import (
	"os"
	"unicode/utf8"
)

// InstalledApp is one installed app, as reported by an
// appmanifest_<appid>.acf file.
type InstalledApp struct {
	AppID       string
	Name        string
	StateFlags  int
	SizeOnDisk  *int // nil when missing/ungrammatical (tolerated field)
	LibraryPath string
}

// Installed reports whether the "fully installed" bit is set in
// StateFlags. Other bits (e.g. update-required) may be set alongside it —
// an app mid-update is still installed and playable, just stale.
func (a InstalledApp) Installed() bool {
	return a.StateFlags&StateFlagFullyInstalled != 0
}

// ParseAppManifest parses one appmanifest file's content into an
// InstalledApp.
//
// Returns a *ParseError if the file is structurally malformed or missing
// required fields (appid, name, StateFlags), or if appid/StateFlags do
// not match the strict digit grammar (see parseStrictUint) — e.g.
// " 480 " or "notanumber" as an appid is rejected, not silently coerced.
// SizeOnDisk is the one field that is tolerated when missing or
// malformed (see below).
func ParseAppManifest(text string, libraryPath string) (InstalledApp, error) {
	parsed, err := ParseVDF(text)
	if err != nil {
		return InstalledApp{}, err
	}

	rootAny, ok := getCI(parsed, "AppState")
	root, isObj := rootAny.(*KeyValues)
	if !ok || !isObj {
		return InstalledApp{}, newParseError("appmanifest has no top-level 'AppState' block")
	}

	appidRaw, appidOK := asString(getCI(root, "appid"))
	name, nameOK := asString(getCI(root, "name"))
	stateFlagsRaw, stateFlagsOK := asString(getCI(root, "StateFlags"))
	sizeRaw, sizeIsString := asString(getCI(root, "SizeOnDisk"))

	if !appidOK || appidRaw == "" {
		return InstalledApp{}, newParseError("appmanifest missing required 'appid'")
	}
	if !nameOK {
		return InstalledApp{}, newParseError("appmanifest missing required 'name'")
	}
	if !stateFlagsOK {
		return InstalledApp{}, newParseError("appmanifest missing required 'StateFlags'")
	}

	// appid is kept as a string (it's an identifier, not a quantity to do
	// arithmetic on) but still validated against the same strict digit
	// grammar as StateFlags/SizeOnDisk — a poisoned/corrupt appid must
	// not silently pass through as a lookalike value.
	if _, ok := parseStrictUint(appidRaw); !ok {
		return InstalledApp{}, newParseError("appid " + quoteForMsg(appidRaw) + " is not a valid ASCII digit string")
	}

	stateFlags, ok := parseStrictUint(stateFlagsRaw)
	if !ok {
		return InstalledApp{}, newParseError("StateFlags " + quoteForMsg(stateFlagsRaw) + " is not a valid ASCII digit string")
	}

	var sizeOnDisk *int
	if sizeIsString {
		// Missing/garbled SizeOnDisk is tolerated — not fatal to the rest
		// of the record — so an unparseable value degrades to nil rather
		// than raising.
		if v, ok := parseStrictUint(sizeRaw); ok {
			sizeOnDisk = &v
		}
	}

	return InstalledApp{
		AppID:       appidRaw,
		Name:        name,
		StateFlags:  stateFlags,
		SizeOnDisk:  sizeOnDisk,
		LibraryPath: libraryPath,
	}, nil
}

// ParseAppManifestFile reads and parses an appmanifest file from disk.
//
// Returns a *ParseError (wrapping OS/decode errors too) so callers have
// one error type to check when walking many files.
func ParseAppManifestFile(path string, libraryPath string) (InstalledApp, error) {
	text, err := readFileStripBOM(path)
	if err != nil {
		return InstalledApp{}, err
	}
	return ParseAppManifest(text, libraryPath)
}

// readFileStripBOM reads a file as UTF-8, transparently stripping a
// leading UTF-8 BOM if present (mirrors Python's encoding="utf-8-sig"),
// and wraps any OS error as a *ParseError (preserving it via Unwrap) so
// callers have one error type to catch.
//
// Rejects invalid UTF-8 with a *ParseError instead of silently decoding
// it. This mirrors Python's read_text(encoding="utf-8-sig",
// errors="strict"): a file containing a byte sequence that isn't valid
// UTF-8 (e.g. a Latin-1/cp1252 manifest with a raw 0xE9 for an accented
// character) raises UnicodeDecodeError there, which the Python spec
// wraps into VdfParseError — the file is skipped with a warning, never
// silently misread. Go's `string(data)` conversion has no such built-in
// guard: it happily produces a string containing the U+FFFD replacement
// character for every invalid byte, which would let a mojibake-mangled
// name (or path) flow all the way to InstalledApp/WP 2.2's reporter with
// no error raised at all — the one behavior this port must NOT
// reproduce, since "corrupt file, skip it" is safer than "silently wrong
// data, ship it".
func readFileStripBOM(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", newParseErrorWrap("cannot read "+path+": "+err.Error(), err)
	}
	// The BOM is also stripped defensively inside ParseVDF itself, but
	// stripping it here too matches parse_appmanifest_file's explicit
	// utf-8-sig read path documented in the Python spec.
	if len(data) >= 3 && data[0] == 0xEF && data[1] == 0xBB && data[2] == 0xBF {
		data = data[3:]
	}
	if !utf8.Valid(data) {
		return "", newParseError("cannot read " + path + ": file is not valid UTF-8")
	}
	return string(data), nil
}

// asString type-asserts an (any, bool) pair from getCI as a string. The
// returned bool is true only if the key was present AND its value is a
// string (not a nested object) — mirroring the Python code's
// isinstance(x, str) checks.
func asString(v any, present bool) (string, bool) {
	if !present {
		return "", false
	}
	s, ok := v.(string)
	return s, ok
}
