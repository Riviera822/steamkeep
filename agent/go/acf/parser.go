package acf

import "strings"

const bom = '\uFEFF'

// isConditionalTag reports whether value looks like a KeyValues platform
// conditional, e.g. [$WIN32] / [$LINUX] / [$OSX] / [!$WIN32]. See the
// "tolerate-and-skip" decision documented on parseObject.
func isConditionalTag(value string) bool {
	r := []rune(value)
	return len(r) >= 2 && r[0] == '[' && r[len(r)-1] == ']'
}

// parseObject parses key/value pairs until a closing brace (nested) or
// end of tokens (top level). Returns (parsed object, next position).
//
// Duplicate keys at the same level overwrite (last wins) but do not
// raise — real-world VDF quirk tolerance per the work package.
//
// A nested block (topLevel=false) MUST end with a closing brace — running
// out of tokens first means an unclosed block, which raises. The true
// top level (topLevel=true) has no enclosing braces at all, so it must
// end exactly at EOF — an unmatched closing brace there is structural
// garbage and raises too.
//
// Nesting depth is capped (maxNestingDepth): a nested block deeper than
// that raises a *ParseError instead of recursing further, so a hostile or
// corrupt file with thousands of nested '{' cannot crash the agent with a
// stack overflow.
//
// Platform conditional tags ([$WIN32] etc.) immediately following a key's
// value are tolerated and skipped, not treated as the start of the next
// key/value pair. Valve's KeyValues format uses this suffix in some real
// files (controller configs, localization files) to mark a pair as
// platform-specific; failing the WHOLE file on encountering one would be
// needlessly fragile for a format-compliant construct. Deliberate
// simplification: the tag is stripped and *ignored* — the key/value pair
// is always kept regardless of which platform the tag names. Only the
// position after a value is handled (the common, real-world placement); a
// conditional directly after a key and before its value is not
// recognized as one.
func parseObject(tokens []token, pos int, topLevel bool, depth int) (*KeyValues, int, error) {
	if !topLevel && depth > maxNestingDepth {
		offset := -1
		if pos < len(tokens) {
			offset = tokens[pos].pos
		}
		return nil, 0, newParseErrorAt(
			"exceeded max nesting depth (100); refusing to parse further "+
				"(likely corrupt or hostile input)", offset)
	}

	result := newKeyValues()
	n := len(tokens)

	for pos < n {
		tok := tokens[pos]

		if tok.kind == tokRBrace {
			if topLevel {
				return nil, 0, newParseErrorAt("unexpected closing brace", tok.pos)
			}
			return result, pos + 1, nil // consume the closing brace
		}

		if tok.kind != tokString {
			return nil, 0, newParseErrorAt("expected a key string, found a brace", tok.pos)
		}

		key := tok.value
		pos++

		if pos >= n {
			return nil, 0, newParseErrorAt(
				"key "+quoteForMsg(key)+" has no value (end of input)", tok.pos)
		}

		valueTok := tokens[pos]

		switch valueTok.kind {
		case tokLBrace:
			nested, next, err := parseObject(tokens, pos+1, false, depth+1)
			if err != nil {
				return nil, 0, err
			}
			result.set(key, nested)
			pos = next
		case tokString:
			result.set(key, valueTok.value)
			pos++
		default:
			return nil, 0, newParseErrorAt(
				"key "+quoteForMsg(key)+" followed by an unexpected closing brace", tok.pos)
		}

		// Tolerate-and-skip a platform conditional tag right after the
		// value (see docstring above).
		if pos < n && tokens[pos].kind == tokString && isConditionalTag(tokens[pos].value) {
			pos++
		}
	}

	if !topLevel {
		return nil, 0, newParseError("unexpected end of input: unclosed block")
	}

	return result, pos, nil
}

func quoteForMsg(s string) string {
	return "'" + s + "'"
}

// ParseVDF parses a full KeyValues document into a nested KeyValues
// object.
//
// The top level is itself an implicit object (no enclosing braces), e.g.
// `"AppState" { ... }` parses to KeyValues{"AppState": KeyValues{...}}.
//
// A leading UTF-8 BOM (U+FEFF) is stripped before tokenizing (repeatedly,
// mirroring Python's str.lstrip semantics): without this, the BOM
// character is neither whitespace nor a brace nor a quote, so the
// unquoted-bareword reader would swallow it together with the
// immediately following quoted key (e.g. turning `"AppState"` into one
// garbled bareword token containing the quote characters).
func ParseVDF(text string) (*KeyValues, error) {
	text = strings.TrimLeft(text, string(bom))
	tokens, err := tokenize(text)
	if err != nil {
		return nil, err
	}
	result, _, err := parseObject(tokens, 0, true, 0)
	if err != nil {
		return nil, err
	}
	return result, nil
}
