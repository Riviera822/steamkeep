package acf

import (
	"path/filepath"
	"runtime"
)

// fixturesDir returns the ABSOLUTE path to agent/tests/fixtures — the
// SAME fixture corpus the Python executable specification (agent/
// vault_agent/acf.py, removed at the Phase-2 close-out, WP 2.6 — see
// acf.go's package doc) was tested against, and that this package's own
// tests (agent/tests/fixtures/*) continue to consume unchanged, never
// copied. runtime.Caller(0) gives this source file's own path regardless
// of the test binary's working directory, so `go test ./...` from any
// directory finds the fixtures reliably.
func fixturesDir() string {
	_, thisFile, _, _ := runtime.Caller(0)
	// this file: agent/go/acf/testhelpers_test.go
	// fixtures:  agent/tests/fixtures
	return filepath.Join(filepath.Dir(thisFile), "..", "..", "tests", "fixtures")
}

func fixture(name string) string {
	return filepath.Join(fixturesDir(), name)
}

// kvEqualsMap recursively compares a parsed *KeyValues against a plain
// map[string]any / string literal tree (nested maps for nested blocks),
// so ported tests can write expected values almost exactly like the
// Python tests' dict literals (e.g. map[string]any{"Root": map[string]any
// {"key": "value"}}) instead of hand-building *KeyValues trees.
//
// Order-independent, matching Python dict `==` semantics (which also
// ignores insertion order for equality).
func kvEqualsMap(actual *KeyValues, expected map[string]any) bool {
	if actual == nil {
		return len(expected) == 0
	}
	if actual.Len() != len(expected) {
		return false
	}
	for _, k := range actual.keys {
		av, _ := actual.Get(k)
		ev, ok := expected[k]
		if !ok {
			return false
		}
		switch evv := ev.(type) {
		case string:
			as, ok := av.(string)
			if !ok || as != evv {
				return false
			}
		case map[string]any:
			akv, ok := av.(*KeyValues)
			if !ok || !kvEqualsMap(akv, evv) {
				return false
			}
		default:
			return false
		}
	}
	return true
}

// asParseError asserts err is a *ParseError (the one exception type this
// package raises for malformed input, mirroring VdfParseError's single-
// exception-type contract in the Python spec).
func isParseError(err error) bool {
	_, ok := err.(*ParseError)
	return ok
}
