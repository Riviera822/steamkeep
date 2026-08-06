"""Window parsing and containment (WP 3.5) — pure, no clock, no database."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from vault_api.schedule_window import (
    MINUTES_PER_DAY,
    ScheduleWindowError,
    format_minute_of_day,
    next_open,
    parse_window,
)

#: A fixed non-UTC offset, so nothing here can accidentally pass only because
#: the machine running the tests happens to sit in UTC.
TZ = timezone(timedelta(hours=2))


def at(hour: int, minute: int = 0, second: int = 0) -> datetime:
    return datetime(2026, 8, 6, hour, minute, second, tzinfo=TZ)


# -- parsing: valid --------------------------------------------------------


def test_parses_a_plain_daytime_window() -> None:
    window = parse_window("09:00-17:00")

    assert window.start_minute == 9 * 60
    assert window.end_minute == 17 * 60
    assert window.overnight is False
    assert window.raw == "09:00-17:00"
    assert window.normalized == "09:00-17:00"


def test_tolerates_surrounding_and_inner_whitespace() -> None:
    window = parse_window("  09:30 - 17:45  ")

    assert (window.start_minute, window.end_minute) == (9 * 60 + 30, 17 * 60 + 45)
    # raw keeps the operator's value minus the outer padding; normalized is
    # what the parser actually understood.
    assert window.raw == "09:30 - 17:45"
    assert window.normalized == "09:30-17:45"


def test_parses_an_overnight_window() -> None:
    """22:00-06:00 wraps past midnight — the natural homelab window."""
    window = parse_window("22:00-06:00")

    assert window.overnight is True
    assert (window.start_minute, window.end_minute) == (22 * 60, 6 * 60)


def test_end_of_day_spelling_gives_an_always_open_window() -> None:
    window = parse_window("00:00-24:00")

    assert window.end_minute == MINUTES_PER_DAY
    assert window.overnight is False
    for hour in range(24):
        assert window.contains(at(hour, 59)) is True


def test_window_ending_at_midnight_is_read_as_overnight_and_covers_the_evening() -> None:
    """'18:00-00:00' — end 00:00 is earlier than start, so [18:00, 24:00)."""
    window = parse_window("18:00-00:00")

    assert window.overnight is True
    assert window.contains(at(18, 0)) is True
    assert window.contains(at(23, 59)) is True
    assert window.contains(at(0, 0)) is False
    assert window.contains(at(17, 59)) is False


# -- parsing: rejected -----------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "09:00",  # no separator
        "09:00-17:00-19:00",  # two separators
        "9:00-17:00",  # not zero padded
        "09:0-17:00",
        "0900-1700",  # no colon
        "09:00–17:00",  # en dash, not a hyphen
        "09:60-17:00",  # invalid minute
        "25:00-17:00",  # invalid hour
        "24:00-17:00",  # 24:00 is an END-only spelling
        "09:00-24:30",  # the only 24 spelling is exactly 24:00
        "09:00-25:00",
        "nine-five",
        "09:00-",
        "-17:00",
        "09:00-17:00 every 3h",
    ],
)
def test_malformed_windows_are_rejected(raw: str) -> None:
    with pytest.raises(ScheduleWindowError):
        parse_window(raw)


def test_equal_start_and_end_is_rejected_with_a_pointer_to_the_full_day_form() -> None:
    with pytest.raises(ScheduleWindowError, match="00:00-24:00"):
        parse_window("09:00-09:00")


def test_non_ascii_digits_are_rejected() -> None:
    """LEARNINGS.md: int() accepts non-ASCII digits — the regex must not.

    '٠٩' is ARABIC-INDIC NINE etc.; int() reads it as 9 quite happily, so a
    parser that split on ':' and called int() would silently accept it.
    """
    with pytest.raises(ScheduleWindowError):
        parse_window("٠٩:٠٠-17:00")


@pytest.mark.parametrize("raw", [" 9:00-17:00", "+9:00-17:00", "0_9:00-17:00"])
def test_values_int_would_accept_are_still_rejected(raw: str) -> None:
    """The other half of the same LEARNINGS entry: ' 4 ', '+4', '1_0'."""
    with pytest.raises(ScheduleWindowError):
        parse_window(raw)


# -- containment -----------------------------------------------------------


def test_daytime_window_is_start_inclusive_and_end_exclusive() -> None:
    window = parse_window("09:00-17:00")

    assert window.contains(at(8, 59)) is False
    assert window.contains(at(9, 0)) is True
    assert window.contains(at(9, 0, 30)) is True  # seconds are ignored
    assert window.contains(at(12, 30)) is True
    assert window.contains(at(16, 59, 59)) is True
    assert window.contains(at(17, 0)) is False
    assert window.contains(at(17, 0, 1)) is False
    assert window.contains(at(23, 59)) is False


def test_overnight_window_covers_both_sides_of_midnight() -> None:
    window = parse_window("22:00-06:00")

    assert window.contains(at(21, 59)) is False
    assert window.contains(at(22, 0)) is True
    assert window.contains(at(23, 59)) is True
    assert window.contains(at(0, 0)) is True
    assert window.contains(at(3, 0)) is True
    assert window.contains(at(5, 59)) is True
    assert window.contains(at(6, 0)) is False
    assert window.contains(at(12, 0)) is False


def test_containment_reads_local_wall_clock_not_utc() -> None:
    """The same instant is inside in one zone and outside in another."""
    window = parse_window("09:00-17:00")
    instant_utc = datetime(2026, 8, 6, 8, 0, tzinfo=timezone.utc)

    assert window.contains(instant_utc.astimezone(TZ)) is True  # 10:00 local
    assert window.contains(instant_utc) is False  # 08:00 UTC


# -- next_open -------------------------------------------------------------


def test_next_open_returns_the_moment_itself_when_already_inside() -> None:
    window = parse_window("09:00-17:00")
    moment = at(10, 15)

    assert next_open(window, moment) == moment


def test_next_open_before_the_window_is_today() -> None:
    window = parse_window("09:00-17:00")

    assert next_open(window, at(6, 30)) == at(9, 0)


def test_next_open_after_the_window_is_tomorrow() -> None:
    window = parse_window("09:00-17:00")

    assert next_open(window, at(18, 0)) == at(9, 0) + timedelta(days=1)


def test_next_open_for_an_overnight_window_is_tonight() -> None:
    window = parse_window("22:00-06:00")

    # 12:00 sits in the daytime gap that PRECEDES tonight's opening.
    assert next_open(window, at(12, 0)) == at(22, 0)


def test_format_minute_of_day_round_trips_the_end_of_day_spelling() -> None:
    assert format_minute_of_day(0) == "00:00"
    assert format_minute_of_day(9 * 60 + 5) == "09:05"
    assert format_minute_of_day(MINUTES_PER_DAY) == "24:00"
