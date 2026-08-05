// Package acf is a Go port of the Python "executable specification"
// (agent/vault_agent/acf.py, ADR-0005) for Valve's KeyValues text format
// (VDF/ACF), used by the Steam client for two files vault-agent cares
// about:
//
//   - steamapps/appmanifest_<appid>.acf — one installed app's metadata.
//   - steamapps/libraryfolders.vdf — the list of library folders (drives)
//     Steam knows about, in both the modern (numbered blocks with
//     "path"/"apps" keys) and the older flat format (numbered key -> path
//     string directly).
//
// Every parsing decision here (escape handling, nesting cap, conditional
// tags, BOM handling, the strict-uint grammar, duplicate-key/appid
// tolerance) is pinned to match agent/vault_agent/acf.py — see that
// file's docstrings and agent/README.md for the rationale. This package
// does not re-derive those decisions; it reproduces them, with a small
// number of KNOWN, DELIBERATE divergences documented at the point they
// occur (parseStrictUint's integer-overflow behavior; isAllASCIIDigits'
// stricter-than-str.isdigit() grammar) and summarized in
// agent/README.md's "Known divergences from the Python spec" section.
package acf

import (
	"fmt"
	"strings"
)

// StateFlagFullyInstalled is bit 4 of the StateFlags bitmask on an
// appmanifest — "fully installed". Other bits may be set alongside it
// (e.g. an app mid-update still has this bit set: still installed and
// playable, just stale), so callers must check with bitwise AND, never
// equality. See agent/vault_agent/acf.py for the full documented
// (SteamKit EAppState) bit table — only this bit was empirically verified
// against a real Steam install; the rest is reproduced for reference only.
const StateFlagFullyInstalled = 4

// maxNestingDepth caps how deep _parse_object (parseObject) will recurse
// before refusing further input. Real appmanifest/libraryfolders files
// nest 3 levels deep at most; 100 is generous headroom that still turns a
// maliciously/corruptly deep-nested file into a clean ParseError instead
// of a stack overflow.
const maxNestingDepth = 100

// ParseError is returned for malformed KeyValues (VDF/ACF) input, or when
// wrapping an OS/decode error while reading a file. Offset is a
// best-effort character (rune) offset into the input for error messages;
// it is -1 when not applicable (e.g. a wrapped file-read error). Cause
// holds the underlying error when ParseError wraps one (e.g. the
// *fs.PathError from a missing/unreadable file) — nil for a purely
// structural parse error. ParseError implements Unwrap so callers can use
// errors.Is/errors.As (e.g. errors.Is(err, fs.ErrNotExist) in WP 2.2's
// reporter to tell "file vanished" apart from "file is corrupt").
//
// Mirrors vault_agent.acf.VdfParseError: callers that walk many files
// (DiscoverInstalled) catch/inspect this single error type, log a
// warning, and skip the offending file rather than crashing.
type ParseError struct {
	Msg    string
	Offset int
	Cause  error
}

func (e *ParseError) Error() string {
	if e.Offset < 0 {
		return e.Msg
	}
	return fmt.Sprintf("%s (offset %d)", e.Msg, e.Offset)
}

func (e *ParseError) Unwrap() error {
	return e.Cause
}

func newParseError(msg string) *ParseError {
	return &ParseError{Msg: msg, Offset: -1}
}

func newParseErrorAt(msg string, offset int) *ParseError {
	return &ParseError{Msg: msg, Offset: offset}
}

// newParseErrorWrap builds a *ParseError around an underlying error (e.g.
// an OS/decode error), preserving it via Unwrap so errors.Is/errors.As
// still work on the wrapped ParseError.
func newParseErrorWrap(msg string, cause error) *ParseError {
	return &ParseError{Msg: msg, Offset: -1, Cause: cause}
}

// KeyValues is a parsed KeyValues object: a key -> value mapping where
// each value is either a string or a nested *KeyValues (mirrors Python's
// `dict[str, str | KeyValues]`). Callers type-switch on the value:
//
//	switch v := kv.Get("key"); v.(type) {
//	case string:
//	case *KeyValues:
//	}
//
// Insertion order is preserved and iterable via Keys() — Python dicts are
// insertion-ordered (a key overwritten by a duplicate keeps its ORIGINAL
// position, per Python semantics), and at least one spec behavior
// (ParseLibraryFolders' output order) depends on that: a plain Go map's
// randomized iteration order would make that non-deterministic. A bare
// map[string]any would silently diverge from the spec here, so this is a
// small ordered-map type instead.
type KeyValues struct {
	keys   []string
	values map[string]any
}

func newKeyValues() *KeyValues {
	return &KeyValues{values: make(map[string]any)}
}

// set stores key -> value. A duplicate key overwrites the value but
// keeps its original position in Keys() — last-value-wins, first-
// position-wins, matching Python dict assignment semantics exactly.
func (kv *KeyValues) set(key string, value any) {
	if _, exists := kv.values[key]; !exists {
		kv.keys = append(kv.keys, key)
	}
	kv.values[key] = value
}

// Get looks up key with exact (case-sensitive) matching.
func (kv *KeyValues) Get(key string) (any, bool) {
	v, ok := kv.values[key]
	return v, ok
}

// Keys returns keys in insertion order.
func (kv *KeyValues) Keys() []string {
	return kv.keys
}

// Len returns the number of keys.
func (kv *KeyValues) Len() int {
	return len(kv.keys)
}

// getCI is a case-insensitive key lookup (Valve's own tools are
// inconsistent about casing across KeyValues files in the wild). Mirrors
// Python's _get_ci: an exact match wins first; otherwise the first key
// (in insertion order) whose lowercased form matches.
func getCI(obj *KeyValues, key string) (any, bool) {
	if v, ok := obj.Get(key); ok {
		return v, true
	}
	lowered := strings.ToLower(key)
	for _, k := range obj.keys {
		if strings.ToLower(k) == lowered {
			v, _ := obj.Get(k)
			return v, true
		}
	}
	return nil, false
}
