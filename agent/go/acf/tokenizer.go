package acf

import "strings"

// Token kinds.
type tokenKind int

const (
	tokString tokenKind = iota
	tokLBrace
	tokRBrace
)

const (
	lbrace = '{'
	rbrace = '}'
)

// token mirrors Python's _Token. pos is a rune (character) offset into
// the input, matching Python's character-indexed string semantics (Python
// strings are indexed by code point, not byte) — used only for error
// messages.
type token struct {
	kind  tokenKind
	value string
	pos   int
}

// tokenize turns raw KeyValues text into a flat token list.
//
// Handles: quoted strings with \" \\ \n \t \r escapes (unknown escapes
// preserve the backslash — see the docstring in parser.go), unquoted
// bareword tokens, { / }, and // line comments. Returns a *ParseError on
// an unterminated quoted string.
//
// Operates on runes (not bytes) throughout so offsets and slicing behave
// like Python's code-point-indexed strings, and so multi-byte UTF-8
// content inside quoted values is never split mid-character.
func tokenize(text string) ([]token, error) {
	runes := []rune(text)
	n := len(runes)
	var tokens []token
	i := 0

	for i < n {
		ch := runes[i]

		// Whitespace
		if ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n' {
			i++
			continue
		}

		// Line comment
		if ch == '/' && i+1 < n && runes[i+1] == '/' {
			nl := indexRune(runes, '\n', i)
			if nl == -1 {
				i = n
			} else {
				i = nl + 1
			}
			continue
		}

		// Braces
		if ch == lbrace {
			tokens = append(tokens, token{tokLBrace, string(lbrace), i})
			i++
			continue
		}
		if ch == rbrace {
			tokens = append(tokens, token{tokRBrace, string(rbrace), i})
			i++
			continue
		}

		// Quoted string
		if ch == '"' {
			start := i
			i++
			var out strings.Builder
			closed := false
			for i < n {
				c := runes[i]
				if c == '\\' && i+1 < n {
					next := runes[i+1]
					switch next {
					case 'n':
						out.WriteRune('\n')
					case 't':
						out.WriteRune('\t')
					case 'r':
						out.WriteRune('\r')
					case '"', '\\':
						out.WriteRune(next)
					default:
						// Unknown escape: PRESERVE the backslash rather
						// than silently dropping it. See parser.go and
						// acf.go's package doc (the rationale was
						// originally written up in the since-removed
						// Python reference implementation,
						// agent/vault_agent/acf.py) for the full
						// rationale (under-escaped Windows paths like
						// "C:\Steam\common" must survive unchanged).
						out.WriteRune('\\')
						out.WriteRune(next)
					}
					i += 2
					continue
				}
				if c == '"' {
					closed = true
					i++
					break
				}
				out.WriteRune(c)
				i++
			}
			if !closed {
				return nil, newParseErrorAt(
					"unterminated quoted string starting", start)
			}
			tokens = append(tokens, token{tokString, out.String(), start})
			continue
		}

		// Unquoted bareword token: read until whitespace, brace, or a
		// comment start.
		start := i
		var out strings.Builder
		wrote := false
		for i < n {
			c := runes[i]
			if c == ' ' || c == '\t' || c == '\r' || c == '\n' || c == lbrace || c == rbrace {
				break
			}
			if c == '/' && i+1 < n && runes[i+1] == '/' {
				break
			}
			out.WriteRune(c)
			wrote = true
			i++
		}
		if !wrote {
			// Shouldn't happen (all single chars are handled above), but
			// guard against infinite loops on unexpected input.
			return nil, newParseErrorAt(
				"unexpected character", i)
		}
		tokens = append(tokens, token{tokString, out.String(), start})
	}

	return tokens, nil
}

func indexRune(runes []rune, target rune, from int) int {
	for idx := from; idx < len(runes); idx++ {
		if runes[idx] == target {
			return idx
		}
	}
	return -1
}
