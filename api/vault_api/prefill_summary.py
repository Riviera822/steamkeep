"""Parse SteamPrefill's end-of-run summary out of its captured console output.

WP 3.3 / ADR-0006 decision 1. Spectre.Console renders a small two-column
table at the end of every ``prefill`` run::

    Prefilled 1 apps totaling 75.97 MiB in 16.5553

     Updated | Up To Date
    ---------+------------
        1    |      0

(the border characters above are ASCII stand-ins; the real table uses Unicode
box-drawing glyphs). ``Updated``/``Up To Date`` are SteamPrefill's own counts
of how many of the requested apps fell into each bucket
(``lancache-prefill-common``'s ``PrefillSummaryResult``, per
``docs/research/phase3-manifests.md`` Q1). ADR-0006 decision 1 turns
``Updated == 0 AND Up To Date == 0`` into the signal that SteamPrefill never
actually considered the app (observed for real: an unowned app exits 0 with
this exact table — see the real-run evidence below), which the job-outcome
wiring in ``vault_api/worker.py`` uses to refuse to call that a successful
prefill even though the process exited 0.

**Why this parser has to be this tolerant.** The table above is real only in
the sense that its digits and structure are real — what actually reaches this
parser has been corrupted in a specific, evidenced way. This project's own
dev machine capture
(``core/tests/mvp/RESULTS-20260805-222046.md``, ``RESULTS-20260805-223328.md``)
shows every box-drawing glyph replaced by three-character mojibake runs
(``ï¿½``), because SteamPrefill's console output was written in the Windows
OEM codepage (verified elsewhere in this codebase to be 850 on a German
Windows install — see ``api/README.md``'s agent-report note and
``api/tests/conftest.py``'s ``mklink`` capture) and decoded as UTF-8 upstream
of this parser (``vault_api/prefill.py``'s ``_read_text``, fixed in this same
work package, still ships this parser defensively in case that fix is ever
wrong, bypassed, or the mojibake reaches here some other way — e.g. a stored
job row from before the decode fix). ``--no-ansi`` also does not stop
Spectre.Console's exception renderer from emitting SGR escapes (see
``prefill.py``'s ``strip_ansi``) that may not be fully stripped.

None of that surrounding noise is load-bearing here — only the digits are.
This parser NEVER falls back to guessing zero: any layout it does not
recognize returns ``parse_ok=False`` with every field ``None``, because
ADR-0006 decision 1 depends on being able to tell "confirmed zero" apart from
"could not tell".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# "Prefilled 1 apps totaling 75.97 MiB in 16.5553" / "Prefilled 0 apps
# totaling 0 b in 03.2491" (both verbatim from real captures). Capture the
# "totaling ..." text non-greedily up to the trailing " in <duration>" so the
# unit (MiB, b, ...) is preserved as-is rather than re-parsed/re-formatted.
_PREFILLED_LINE_RE = re.compile(
    r"Prefilled\s+\d+\s+apps?\s+totaling\s+(.+?)\s+in\s+[0-9.:]+\s*$",
    re.IGNORECASE | re.MULTILINE | re.ASCII,
)

# The header row survives corruption because "Updated" and "Up To Date" are
# plain ASCII; only the separating box-drawing glyph between them is at risk,
# and ".*" tolerates whatever that glyph became (a real box character, a
# stripped-blank, or a run of mojibake).
_HEADER_RE = re.compile(r"Updated.*Up\s*To\s*Date", re.IGNORECASE)

# The border/separator row between the header and the data row is BOX GLYPHS
# ONLY (or their corrupted equivalent) -- never a digit -- which is exactly
# what lets this skip past it to the real data row without needing to
# recognize the border characters themselves.
_ANY_DIGIT_RE = re.compile(r"[0-9]", re.ASCII)

# A genuine data row is digits, whitespace and separator glyphs ONLY (a clean
# "|"/box-drawing character, an SGR remnant that survived stripping, or a run
# of mojibake) -- never an ESC byte or an ASCII letter. This is what rules
# out two confirmed false positives (review S1):
#
# - An unstripped SGR escape on the border/separator row, e.g.
#   "\x1b[38;5;226m─────────┼────────────\x1b[0m", contains the digits
#   "38", "5", "226" -- read naively as the data row, a TRUE 0/0 run (the
#   exact WP 1.7 unowned-app trap) parses as updated=38, up_to_date=5 and
#   reports a false 'done' instead of the correct 'error'. The wrong
#   direction is the dangerous one: a real failure reads as success.
# - A timestamped log line, e.g. "[10:20:53 PM] Loaded account licenses
#   00.5163", contains digits too and would otherwise be mistaken for the
#   data row if it happened to sit between the header and the real one.
#
# Both contain an ESC byte or a Latin letter; a real data row never does.
_INVALID_DATA_ROW_RE = re.compile(r"[\x1b]|[A-Za-z]", re.ASCII)

# The data row: exactly two integers, in column order (Updated, Up To Date),
# with anything non-digit as the separator (a clean "|"/box glyph, an SGR
# remnant, or a run of mojibake -- all equally acceptable, none of it read).
_TWO_INTS_RE = re.compile(r"([0-9]+)[^0-9]+?([0-9]+)", re.ASCII)


@dataclass(frozen=True)
class PrefillSummary:
    """The outcome ADR-0006 decision 1 needs. All-``None`` iff ``parse_ok`` is False."""

    updated: int | None
    up_to_date: int | None
    total_bytes_text: str | None
    parse_ok: bool


def _unparsed() -> PrefillSummary:
    return PrefillSummary(updated=None, up_to_date=None, total_bytes_text=None, parse_ok=False)


def parse_summary(text: str) -> PrefillSummary:
    """Extract ``Updated``/``Up To Date`` (and the totals line) from captured output.

    Algorithm, deliberately structural rather than glyph-matching:

    1. Find the header line containing ``Updated`` ... ``Up To Date`` (in that
       order — the table's fixed column order). Not found -> unparseable.
    2. Walk forward and skip every line that is not a plausible data row: no
       ASCII digit at all (the border/separator row, blank padding lines), OR
       an ESC byte or an ASCII letter present (an unstripped SGR escape, or
       an unrelated timestamped log line -- see ``_INVALID_DATA_ROW_RE``,
       review S1). The first line that has a digit and neither of those is
       the data row. None found -> unparseable.
    3. Pull the first two integers out of that line, in order. Fewer than two
       -> unparseable. These are ``updated`` and ``up_to_date`` respectively.
    4. Separately (independent of 1-3 succeeding or not being needed further),
       look for the "Prefilled N apps totaling X in Y" line and capture ``X``
       as ``total_bytes_text``. Its absence does not fail the parse; the
       counters from step 3 are what ADR-0006 decision 1 actually needs.

    Never raises on malformed input -- worst case is ``parse_ok=False``.
    """
    if not text:
        return _unparsed()

    lines = text.splitlines()

    header_idx = None
    for i, line in enumerate(lines):
        if _HEADER_RE.search(line):
            header_idx = i
            break
    if header_idx is None:
        return _unparsed()

    data_line = None
    for line in lines[header_idx + 1 :]:
        if not _ANY_DIGIT_RE.search(line):
            continue  # border/separator/blank row -- keep scanning
        if _INVALID_DATA_ROW_RE.search(line):
            continue  # SGR remnant or an unrelated log line -- not a real data row
        data_line = line
        break
    if data_line is None:
        return _unparsed()

    match = _TWO_INTS_RE.search(data_line)
    if match is None:
        return _unparsed()

    updated = int(match.group(1))
    up_to_date = int(match.group(2))

    total_bytes_text = None
    totals_match = _PREFILLED_LINE_RE.search(text)
    if totals_match is not None:
        total_bytes_text = totals_match.group(1).strip()

    return PrefillSummary(
        updated=updated,
        up_to_date=up_to_date,
        total_bytes_text=total_bytes_text,
        parse_ok=True,
    )
