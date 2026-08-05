"""Tests for the KeyValues tokenizer/parser primitives (parse_vdf)."""

from __future__ import annotations

import pytest

from vault_agent.acf import VdfParseError, parse_vdf


def test_simple_key_value() -> None:
    result = parse_vdf('"Root"\n{\n\t"key"\t"value"\n}\n')
    assert result == {"Root": {"key": "value"}}


def test_nested_blocks() -> None:
    text = """
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
    """
    result = parse_vdf(text)
    assert result == {"Root": {"outer": {"inner": {"leaf": "1"}}}}


def test_multiple_sibling_keys() -> None:
    text = '"Root" { "a" "1" "b" "2" "c" "3" }'
    result = parse_vdf(text)
    assert result == {"Root": {"a": "1", "b": "2", "c": "3"}}


def test_escaped_quote_in_value() -> None:
    text = r'"Root" { "name" "Game \"Special Edition\"" }'
    result = parse_vdf(text)
    assert result == {"Root": {"name": 'Game "Special Edition"'}}


def test_escaped_backslash_in_value() -> None:
    text = r'"Root" { "path" "C:\\Steam\\steamapps" }'
    result = parse_vdf(text)
    assert result == {"Root": {"path": "C:\\Steam\\steamapps"}}


def test_escaped_newline_and_tab() -> None:
    text = r'"Root" { "note" "line1\nline2\ttabbed" }'
    result = parse_vdf(text)
    assert result["Root"]["note"] == "line1\nline2\ttabbed"


def test_unknown_escape_preserves_backslash() -> None:
    # \q is not a recognized escape. The safe-for-paths decision (pinned
    # after WP 2.1 review) is to PRESERVE the backslash rather than
    # silently drop it -- a single backslash before an unescaped char is
    # far more likely to be sloppy real-world VDF (an under-escaped
    # Windows path) than an intentional escape, and dropping it would
    # silently produce a different, wrong string with no error raised.
    text = r'"Root" { "weird" "a\qb" }'
    result = parse_vdf(text)
    assert result == {"Root": {"weird": "a\\qb"}}


def test_unknown_escape_in_windows_path_does_not_corrupt_it() -> None:
    # The motivating case: a mildly malformed (single-backslash,
    # under-escaped) Windows path must come through unchanged, not
    # silently mangled into a different, wrong path.
    text = r'"Root" { "path" "C:\Steam\common" }'
    result = parse_vdf(text)
    assert result == {"Root": {"path": "C:\\Steam\\common"}}


def test_line_comment_is_skipped() -> None:
    text = """
    "Root"
    {
        // this whole line is a comment and must be ignored
        "key" "value" // trailing comment too
    }
    """
    result = parse_vdf(text)
    assert result == {"Root": {"key": "value"}}


def test_comment_only_file_section() -> None:
    text = '"Root" {\n// comment\n// another comment\n"k" "v"\n}'
    result = parse_vdf(text)
    assert result == {"Root": {"k": "v"}}


def test_unquoted_tokens_supported() -> None:
    # Steam's own files always quote, but the format itself allows bare
    # tokens as both keys and values.
    text = "Root { key value }"
    result = parse_vdf(text)
    assert result == {"Root": {"key": "value"}}


def test_duplicate_sibling_key_last_wins_no_crash() -> None:
    text = '"Root" { "dup" "first" "dup" "second" }'
    result = parse_vdf(text)
    assert result == {"Root": {"dup": "second"}}


def test_unterminated_quote_raises() -> None:
    with pytest.raises(VdfParseError):
        parse_vdf('"Root" { "key" "unterminated')


def test_missing_value_raises() -> None:
    with pytest.raises(VdfParseError):
        parse_vdf('"Root" { "key" }')


def test_stray_closing_brace_raises() -> None:
    with pytest.raises(VdfParseError):
        parse_vdf('"Root" { "key" "value" } }')


def test_key_followed_by_nothing_raises() -> None:
    with pytest.raises(VdfParseError):
        parse_vdf('"Root" { "key"')


def test_unclosed_nested_block_raises() -> None:
    with pytest.raises(VdfParseError):
        parse_vdf('"Root" { "key" "value"')


def test_empty_document_parses_to_empty_dict() -> None:
    assert parse_vdf("") == {}


def test_only_comments_parses_to_empty_dict() -> None:
    assert parse_vdf("// nothing but a comment\n") == {}


def _nested_text(depth: int) -> str:
    """Build ``"Root" { "a" { "a" { ... "leaf" "1" ... } } }`` nested
    ``depth`` levels of "a" blocks deep inside Root's own block."""
    inner = '"leaf" "1"'
    for _ in range(depth):
        inner = f'"a" {{ {inner} }}'
    return f'"Root" {{ {inner} }}'


def test_moderate_nesting_within_limit_parses_fine() -> None:
    # Real files nest 3 deep; this is comfortably within the 100-level cap.
    result = parse_vdf(_nested_text(10))
    node = result["Root"]
    for _ in range(10):
        node = node["a"]  # type: ignore[assignment]
    assert node == {"leaf": "1"}


def test_excessive_nesting_raises_parse_error_not_recursion_error() -> None:
    # Depth 150 exceeds the 100-level cap: must raise VdfParseError, never
    # an uncaught RecursionError (which would escape discover_installed's
    # VdfParseError-only catch and crash the agent).
    with pytest.raises(VdfParseError):
        parse_vdf(_nested_text(150))


def test_extreme_nesting_raises_parse_error_not_recursion_error() -> None:
    # A hostile/corrupt file with thousands of nested braces must still
    # degrade to a clean VdfParseError rather than crashing the process.
    with pytest.raises(VdfParseError):
        parse_vdf(_nested_text(5000))


def test_stray_top_level_brace_after_conditional_still_detected() -> None:
    # Sanity check that the conditional-tag skip (see below) doesn't
    # accidentally swallow a genuine structural error.
    with pytest.raises(VdfParseError):
        parse_vdf('"Root" { "key" "value" [$WIN32] } }')


def test_conditional_tag_after_value_is_skipped() -> None:
    # KeyValues platform conditional suffix: tolerated and stripped, the
    # pair is kept regardless of platform (see _parse_object docstring for
    # the tolerate-and-skip decision and its justification).
    text = '"Root" { "key" "value" [$WIN32] "other" "2" }'
    result = parse_vdf(text)
    assert result == {"Root": {"key": "value", "other": "2"}}


def test_conditional_tag_on_nested_block_is_skipped() -> None:
    text = '"Root" { "block" { "inner" "1" } [$LINUX] "after" "yes" }'
    result = parse_vdf(text)
    assert result == {"Root": {"block": {"inner": "1"}, "after": "yes"}}


def test_negated_conditional_tag_is_skipped() -> None:
    text = '"Root" { "key" "value" [!$WIN32] }'
    result = parse_vdf(text)
    assert result == {"Root": {"key": "value"}}


def test_utf8_bom_prefix_is_stripped_before_tokenizing() -> None:
    # A leading BOM must not corrupt the first token (see parse_vdf's
    # docstring: without stripping, the bareword reader would swallow the
    # BOM together with the following quoted key).
    text = "﻿" + '"Root" { "key" "value" }'
    result = parse_vdf(text)
    assert result == {"Root": {"key": "value"}}
