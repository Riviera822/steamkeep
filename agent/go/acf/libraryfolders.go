package acf

// ParseLibraryFolders parses a libraryfolders.vdf document into a list of
// library root paths.
//
// Supports both:
//   - The modern format (Steam client 2019+): numbered blocks each with a
//     "path" key (and an "apps" sub-block we don't need here).
//   - The older flat format: numbered keys mapping directly to a path
//     string, alongside unrelated top-level keys (TimeNextStatsReport,
//     ContentStatsID, ...) which are skipped.
//
// Non-numeric keys are ignored in both formats (that's how the two are
// told apart from the unrelated bookkeeping keys in the old format).
// Returns a *ParseError if the file has no recognizable root block at
// all.
func ParseLibraryFolders(text string) ([]string, error) {
	parsed, err := ParseVDF(text)
	if err != nil {
		return nil, err
	}

	rootAny, ok := getCI(parsed, "libraryfolders")
	root, isObj := rootAny.(*KeyValues)
	if !ok || !isObj {
		return nil, newParseError("libraryfolders.vdf has no top-level 'libraryfolders' block")
	}

	var paths []string
	// Iterate in insertion (file) order — root.Keys() preserves it, unlike
	// ranging a plain Go map. The reference test corpus asserts the
	// resulting path LIST is in file order, so this is not cosmetic.
	for _, key := range root.Keys() {
		if !isAllASCIIDigits(key) {
			continue // e.g. TimeNextStatsReport, ContentStatsID — not a library entry
		}
		value, _ := root.Get(key)

		switch v := value.(type) {
		case *KeyValues:
			// Modern format: nested block with a "path" key.
			if pathValue, ok := getCI(v, "path"); ok {
				if s, ok := pathValue.(string); ok && s != "" {
					paths = append(paths, s)
				}
			}
		case string:
			// Old flat format: the value itself is the path.
			if v != "" {
				paths = append(paths, v)
			}
		}
	}

	return paths, nil
}

// isAllASCIIDigits reports whether s is a non-empty string of ASCII digit
// characters (0-9) only.
//
// KNOWN, DELIBERATE DIVERGENCE: this does NOT mirror Python's
// str.isdigit(), which the reference parse_libraryfolders calls directly
// on library keys (`if not key.isdigit(): continue`) — str.isdigit() is
// Unicode-aware and also accepts some non-ASCII digit-like characters
// (e.g. superscript digits, other scripts' decimal digits) that this
// ASCII-only check rejects. Every real Steam-written libraryfolders.vdf
// only ever uses plain ASCII numeric keys ("0", "1", "2", ...), and the
// fixture corpus doesn't exercise the non-ASCII case either, so this is
// treated as a low-risk, acceptable divergence (stricter, not looser,
// than the spec) rather than a bug to chase — but it is a real
// difference in what a corrupt/hostile file with an exotic-digit library
// index would do in each implementation, so it's named here rather than
// left to look like an intentional 1:1 mirror.
func isAllASCIIDigits(s string) bool {
	if s == "" {
		return false
	}
	for _, r := range s {
		if r < '0' || r > '9' {
			return false
		}
	}
	return true
}

// ParseLibraryFoldersFile reads and parses libraryfolders.vdf from disk.
//
// Returns a *ParseError (wrapping OS/decode errors too).
func ParseLibraryFoldersFile(path string) ([]string, error) {
	text, err := readFileStripBOM(path)
	if err != nil {
		return nil, err
	}
	return ParseLibraryFolders(text)
}
