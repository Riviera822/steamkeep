"""Tests for vault_api.prefill_summary (WP 3.3).

Two of the fixtures below are copied VERBATIM (not retyped/cleaned up) from
real captured job ``log_excerpt`` values in this repo:

- ``REAL_UNOWNED_APP`` <- ``core/tests/mvp/RESULTS-20260805-222046.md``,
  the ``log_excerpt`` JSON field of job 1 (app 480, Spacewar, not owned by
  the account that ran it — SteamPrefill exits 0 with "Prefilled 0 apps").
- ``REAL_NORMAL_PREFILL`` <- ``core/tests/mvp/RESULTS-20260805-223328.md``,
  job 1 (app 3419430, "Bongo Cat" — a real, successful, non-empty prefill).

Both show the exact mojibake this project has actually observed: every
box-drawing glyph in SteamPrefill's Spectre.Console summary table replaced by
a three-character "ï¿½" run (see ``vault_api/prefill.py``'s ``_read_text``
docstring for the decode story). The "clean" and "missing" fixtures are
synthetic — no bug-free capture of this table exists in this repo (the two
real runs above are the only two on record, and both are corrupted) — but are
modeled directly on the real fixtures' structure (same column layout, same
spacing), per this project's fixtures rule (synthetic only, modeled on real
structure).
"""

from __future__ import annotations

from vault_api.prefill_summary import parse_summary

# -- real captures -----------------------------------------------------------

REAL_UNOWNED_APP = (
    "[10:20:51 PM] Starting login!\nConnecting to Steam...\n[10:20:52 PM] Connected to Steam!\n"
    "Logging in to Steam...\n[10:20:53 PM] Logged into Steam\nRetrieving owned apps...\n"
    "[10:20:53 PM] Loaded account licenses                            00.5163\n"
    "[10:20:53 PM] Steam session initialization complete!             01.9778\n\n"
    "ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½\n"
    "Retrieving latest App metadata...\n\n"
    "[10:20:54 PM] Prefill complete!\n"
    "ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½\n"
    "  Prefilled 0 apps totaling 0 b in 03.2491 \n"
    "                                           \n"
    "   Updated ï¿½ Up To Date                    \n"
    "  ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½                   \n"
    "      0    ï¿½     0                         \n"
    "                                           \n"
    "Disconnecting\n[10:20:54 PM] Disconnected from Steam!\n\n"
    "[vault-api] No new or changed depot directories were observed during this run "
    "(everything requested was already cached), so the existing depot mapping for "
    "this app was left unchanged."
)

REAL_NORMAL_PREFILL = (
    "[10:33:31 PM] Starting login!\nConnecting to Steam...\n[10:33:32 PM] Connected to Steam!\n"
    "Logging in to Steam...\n[10:33:33 PM] Logged into Steam\nRetrieving owned apps...\n"
    "[10:33:33 PM] Loaded account licenses                            00.4607\n"
    "[10:33:33 PM] Steam session initialization complete!             01.9571\n\n"
    "ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½\n"
    "Retrieving latest App metadata...\n\n"
    "[10:33:34 PM] Starting Bongo Cat\n Getting available CDN Servers... \n"
    "Fetching depot manifests...\nDetecting Lancache server...\n"
    "[10:33:34 PM] Detected Lancache server at lancache.steamcontent.com [127.0.0.1]\n"
    "Downloading..: 0%\nDownloading..: 29%\nDownloading..: 50%\nDownloading..: 76%\n"
    "Downloading..: 95%\n[10:33:48 PM] Finished downloading 75.97 MiB in 13.4216 - 47.48 Mbit/s\n\n"
    "[10:33:48 PM] Prefill complete!\n"
    "ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½\n"
    "  Prefilled 1 apps totaling 75.97 MiB in 16.5553 \n"
    "                                                 \n"
    "   Updated ï¿½ Up To Date                          \n"
    "  ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½ï¿½                         \n"
    "      1    ï¿½     0                               \n"
    "                                                 \n"
    "Disconnecting\n[10:33:48 PM] Disconnected from Steam!\n\n"
    "[vault-api] Depot mapping updated (replace-semantics for this app): "
    "added=[3419431] removed=[] unchanged=[]"
)


def test_real_capture_unowned_app_parses_as_zero_zero() -> None:
    """ADR-0006 decision 1's exact trigger case, from the real blocked run."""
    result = parse_summary(REAL_UNOWNED_APP)
    assert result.parse_ok is True
    assert result.updated == 0
    assert result.up_to_date == 0
    assert result.total_bytes_text == "0 b"


def test_real_capture_normal_prefill_parses_updated_one() -> None:
    result = parse_summary(REAL_NORMAL_PREFILL)
    assert result.parse_ok is True
    assert result.updated == 1
    assert result.up_to_date == 0
    assert result.total_bytes_text == "75.97 MiB"


# -- synthetic: clean (no corruption) -----------------------------------------
# Modeled on the real fixtures' structure (same column widths/spacing), using
# proper Unicode box-drawing glyphs instead of the mojibake actually captured
# — this project has no bug-free capture of this table on record.

CLEAN_UP_TO_DATE = (
    "[8:00:00 PM] Starting login!\n[8:00:01 PM] Logged into Steam\n\n"
    "[8:00:03 PM] Prefill complete!\n"
    "  Prefilled 1 apps totaling 0 b in 02.9012 \n"
    "                                           \n"
    "   Updated │ Up To Date                    \n"
    "  ─────────┼───────────────────                   \n"
    "      0    │     1                         \n"
    "                                           \n"
    "Disconnecting\n[8:00:03 PM] Disconnected from Steam!\n"
)


def test_clean_up_to_date_table_parses_updated_zero_up_to_date_one() -> None:
    """ADR-0006's other named case: nothing changed, but SteamPrefill DID
    confirm the app is current (the ``last_manifest_check`` trigger)."""
    result = parse_summary(CLEAN_UP_TO_DATE)
    assert result.parse_ok is True
    assert result.updated == 0
    assert result.up_to_date == 1
    assert result.total_bytes_text == "0 b"


# -- edge cases ----------------------------------------------------------------


def test_empty_string_is_unparseable() -> None:
    result = parse_summary("")
    assert result.parse_ok is False
    assert result.updated is None
    assert result.up_to_date is None
    assert result.total_bytes_text is None


def test_missing_table_entirely_is_unparseable_not_zero() -> None:
    """A failed/aborted run's output with no summary table at all must never
    be reported as Updated=0/Up To Date=0 -- that has the specific ADR-0006
    meaning "app not owned", which this text never claimed."""
    text = (
        "[8:00:00 PM] Starting login!\n"
        "System.Net.Sockets.SocketException: Connection timed out\n"
        "[8:00:30 PM] Disconnected from Steam!\n"
    )
    result = parse_summary(text)
    assert result.parse_ok is False
    assert result.updated is None
    assert result.up_to_date is None
    assert result.total_bytes_text is None


def test_header_present_but_data_row_missing_is_unparseable() -> None:
    """Simulates a capture truncated mid-table (e.g. the 4 KiB tail-excerpt
    cut it off) -- the header survived, the digits did not."""
    text = (
        "[8:00:03 PM] Prefill complete!\n"
        "  Prefilled 1 apps totaling 12 MiB in 05.0000 \n"
        "   Updated │ Up To Date\n"
        "  ─────────┼───────────────────\n"
        # (data row never arrives -- capture cut off here)
    )
    result = parse_summary(text)
    assert result.parse_ok is False


def test_header_split_across_a_boundary_is_unparseable_not_a_false_match() -> None:
    """A truncation that cuts the header itself in half must not accidentally
    match on a fragment (e.g. just "Up To Date" without "Updated" before it)."""
    text = "Up To Date\n  1    |    2\n"
    result = parse_summary(text)
    assert result.parse_ok is False


def test_only_one_integer_in_the_data_row_is_unparseable() -> None:
    text = (
        "   Updated │ Up To Date\n"
        "  ─────────┼───────────────────\n"
        "      7\n"
    )
    result = parse_summary(text)
    assert result.parse_ok is False


def test_sgr_remnant_border_with_embedded_digits_is_not_the_data_row() -> None:
    """Review S1, the dangerous-direction reproduction: an unstripped SGR
    escape on the border/separator row (`--no-ansi` is not sufficient, see
    prefill.py) contains digits of its own ("38", "5", "226"). Read naively
    as the data row, a TRUE 0/0 run (the exact WP 1.7 unowned-app trap)
    would parse as updated=38, up_to_date=5 -- a real failure reported as a
    false success. The real (all-zero) data row follows it."""
    text = (
        "   Updated │ Up To Date\n"
        "\x1b[38;5;226m─────────┼────────────\x1b[0m\n"
        "      0    │     0\n"
    )
    result = parse_summary(text)
    assert result.parse_ok is True
    assert result.updated == 0
    assert result.up_to_date == 0


def test_timestamped_log_line_between_header_and_data_row_is_skipped() -> None:
    """Review S1, second reproduction: a real timestamped log line (digits
    AND letters) sitting between the header and the real data row must not
    be mistaken for it."""
    text = (
        "   Updated │ Up To Date\n"
        "[10:20:53 PM] Loaded account licenses                            00.5163\n"
        "  ─────────┼───────────────────\n"
        "      2    │     7\n"
    )
    result = parse_summary(text)
    assert result.parse_ok is True
    assert result.updated == 2
    assert result.up_to_date == 7


def test_only_invalid_candidate_rows_after_header_is_unparseable() -> None:
    """If every digit-bearing line after the header is disqualified (SGR
    remnant or a log line) and no genuine data row ever shows up, this must
    stay unparseable -- not fall through to some other digit-bearing line
    incorrectly."""
    text = (
        "   Updated │ Up To Date\n"
        "\x1b[38;5;226m─────────┼────────────\x1b[0m\n"
        "[10:20:54 PM] Disconnected from Steam! 00.1234\n"
    )
    result = parse_summary(text)
    assert result.parse_ok is False
    assert result.updated is None
    assert result.up_to_date is None


def test_missing_totals_line_still_parses_the_counters() -> None:
    """total_bytes_text is supplementary -- its absence must not fail the
    parse when the counters themselves (the ADR-0006-critical part) are
    readable."""
    text = (
        "   Updated │ Up To Date\n"
        "  ─────────┼───────────────────\n"
        "      2    │     3\n"
    )
    result = parse_summary(text)
    assert result.parse_ok is True
    assert result.updated == 2
    assert result.up_to_date == 3
    assert result.total_bytes_text is None
