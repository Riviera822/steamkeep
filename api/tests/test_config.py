from __future__ import annotations

import pytest

from vault_api.config import Settings


def test_from_env_raises_when_api_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("VAULT_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="VAULT_API_KEY"):
        Settings.from_env()


def test_from_env_raises_when_api_key_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "   ")
    with pytest.raises(RuntimeError, match="VAULT_API_KEY"):
        Settings.from_env()


def test_from_env_uses_defaults_when_optional_vars_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_DB_PATH", raising=False)
    monkeypatch.delenv("VAULT_CACHE_ROOT", raising=False)
    monkeypatch.delenv("VAULT_LOG_LEVEL", raising=False)

    settings = Settings.from_env()

    assert settings.vault_api_key == "some-key"
    assert settings.db_path == "./vault.db"
    assert settings.cache_root == "./cache"
    assert settings.log_level == "INFO"


def test_from_env_reads_all_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_DB_PATH", "/tmp/custom.db")
    monkeypatch.setenv("VAULT_CACHE_ROOT", "/tmp/cache")
    monkeypatch.setenv("VAULT_LOG_LEVEL", "DEBUG")

    settings = Settings.from_env()

    assert settings.db_path == "/tmp/custom.db"
    assert settings.cache_root == "/tmp/cache"
    assert settings.log_level == "DEBUG"


def test_prefill_settings_have_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    for name in (
        "VAULT_STEAMPREFILL_PATH",
        "VAULT_PREFILL_TIMEOUT_SECONDS",
        "VAULT_WORKER_POLL_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    # No default path on purpose: a missing SteamPrefill must fail JOBS with a
    # clear message, not stop vault-api from starting (WP 1.4).
    assert settings.steamprefill_path == ""
    assert settings.prefill_timeout_seconds == 14400
    assert settings.worker_poll_seconds == 1.0


def test_prefill_settings_read_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_STEAMPREFILL_PATH", r"C:\tools\SteamPrefill.exe")
    monkeypatch.setenv("VAULT_PREFILL_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("VAULT_WORKER_POLL_SECONDS", "0.25")

    settings = Settings.from_env()

    assert settings.steamprefill_path == r"C:\tools\SteamPrefill.exe"
    assert settings.prefill_timeout_seconds == 60
    assert settings.worker_poll_seconds == 0.25


def test_bad_numeric_settings_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    monkeypatch.setenv("VAULT_PREFILL_TIMEOUT_SECONDS", "soon")
    with pytest.raises(RuntimeError, match="VAULT_PREFILL_TIMEOUT_SECONDS"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_PREFILL_TIMEOUT_SECONDS", "0")
    with pytest.raises(RuntimeError, match="must be > 0"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_PREFILL_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("VAULT_WORKER_POLL_SECONDS", "-1")
    with pytest.raises(RuntimeError, match="VAULT_WORKER_POLL_SECONDS"):
        Settings.from_env()


def test_agent_report_keep_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_AGENT_REPORT_KEEP", raising=False)
    assert Settings.from_env().agent_report_keep == 20

    monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", "5")
    assert Settings.from_env().agent_report_keep == 5


def test_manifest_archive_dir_defaults_next_to_the_db_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    import os

    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_MANIFEST_ARCHIVE_DIR", raising=False)
    db_path = str(tmp_path / "sub" / "vault.db")
    monkeypatch.setenv("VAULT_DB_PATH", db_path)

    settings = Settings.from_env()

    assert settings.manifest_archive_dir == os.path.join(
        os.path.dirname(os.path.abspath(db_path)), "manifests"
    )


def test_manifest_archive_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    override = str(tmp_path / "custom-manifests")
    monkeypatch.setenv("VAULT_MANIFEST_ARCHIVE_DIR", override)

    assert Settings.from_env().manifest_archive_dir == override


def test_manifest_keep_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_MANIFEST_KEEP", raising=False)
    assert Settings.from_env().manifest_keep == 3

    monkeypatch.setenv("VAULT_MANIFEST_KEEP", "5")
    assert Settings.from_env().manifest_keep == 5


def test_manifest_keep_below_one_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    # minimum=1 phrases as "> 0" (_env_int's existing wording rule, same as
    # VAULT_PREFILL_TIMEOUT_SECONDS/VAULT_WORKER_POLL_SECONDS above).
    monkeypatch.setenv("VAULT_MANIFEST_KEEP", "0")
    with pytest.raises(RuntimeError, match=r"must be > 0"):
        Settings.from_env()

    # WP 3.12: a NEGATIVE value is now refused one step earlier, by the
    # digits-only syntax rule, and its message names the smallest accepted
    # value instead of the ">" phrasing. Still a loud startup RuntimeError —
    # only the wording moved.
    monkeypatch.setenv("VAULT_MANIFEST_KEEP", "-1")
    with pytest.raises(RuntimeError, match=r"ASCII digits only"):
        Settings.from_env()


def test_steamprefill_cache_dir_has_a_platform_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_STEAMPREFILL_CACHE_DIR", raising=False)

    settings = Settings.from_env()

    assert settings.steamprefill_cache_dir  # never blank
    assert settings.steamprefill_cache_dir.endswith(
        os.path.join("SteamPrefill", "v1")
    )


def test_steamprefill_cache_dir_override(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    override = str(tmp_path / "custom-cache")
    monkeypatch.setenv("VAULT_STEAMPREFILL_CACHE_DIR", override)

    assert Settings.from_env().steamprefill_cache_dir == override


def test_scheduler_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The safe default (WP 3.5): no window = vault-api schedules nothing.

    A fresh install must not start Steam logins and downloads on its own just
    because nobody read the docs yet.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    for name in (
        "VAULT_SCHEDULE_WINDOW",
        "VAULT_SCHEDULE_INTERVAL_MINUTES",
        "VAULT_SCHEDULE_CLIENT_STALE_DAYS",
    ):
        monkeypatch.delenv(name, raising=False)

    settings = Settings.from_env()

    assert settings.schedule_window is None
    assert settings.scheduler_enabled is False
    # Plan §7 Phase 3's "every 3 h", and the documented staleness bound.
    assert settings.schedule_interval_minutes == 180
    assert settings.schedule_client_stale_days == 7


def test_schedule_window_is_parsed_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SCHEDULE_WINDOW", "09:00-17:00")
    monkeypatch.setenv("VAULT_SCHEDULE_INTERVAL_MINUTES", "60")
    monkeypatch.setenv("VAULT_SCHEDULE_CLIENT_STALE_DAYS", "3")

    settings = Settings.from_env()

    assert settings.scheduler_enabled is True
    assert settings.schedule_window is not None
    assert settings.schedule_window.raw == "09:00-17:00"
    assert settings.schedule_window.overnight is False
    assert settings.schedule_interval_minutes == 60
    assert settings.schedule_client_stale_days == 3


def test_an_overnight_window_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SCHEDULE_WINDOW", "22:00-06:00")

    window = Settings.from_env().schedule_window

    assert window is not None and window.overnight is True


def test_a_blank_window_disables_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """'unset' and 'set to spaces' must mean the same thing (a commented-out
    line in .env that kept a trailing space is not a config error)."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SCHEDULE_WINDOW", "   ")

    assert Settings.from_env().scheduler_enabled is False


def test_a_malformed_window_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Not on the first tick, hours later, inside a background thread."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    for bad in ("9-5", "09:00", "09:00-09:00", "24:00-06:00", "09:00-25:00"):
        monkeypatch.setenv("VAULT_SCHEDULE_WINDOW", bad)
        with pytest.raises(RuntimeError, match="VAULT_SCHEDULE_WINDOW is invalid"):
            Settings.from_env()


def test_bad_schedule_numbers_fail_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validated even with no window set, so a typo surfaces on the day it is
    made rather than the day the operator enables the scheduler."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_SCHEDULE_WINDOW", raising=False)

    monkeypatch.setenv("VAULT_SCHEDULE_INTERVAL_MINUTES", "0")
    with pytest.raises(RuntimeError, match="VAULT_SCHEDULE_INTERVAL_MINUTES"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_SCHEDULE_INTERVAL_MINUTES", "three hours")
    with pytest.raises(RuntimeError, match="ASCII digits only"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_SCHEDULE_INTERVAL_MINUTES", "180")
    monkeypatch.setenv("VAULT_SCHEDULE_CLIENT_STALE_DAYS", "-1")
    with pytest.raises(RuntimeError, match="VAULT_SCHEDULE_CLIENT_STALE_DAYS"):
        Settings.from_env()


def test_gc_grace_days_defaults_to_fourteen(monkeypatch: pytest.MonkeyPatch) -> None:
    """WP 3.8b / ADR-0007 addendum A: the grace window is ON by default.

    This is the one setting in this file whose default is a *protection*, so
    the default itself is the feature: an operator who never reads the docs
    still keeps beta-branch and other store-on-miss content for a fortnight.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_GC_GRACE_DAYS", raising=False)

    assert Settings.from_env().gc_grace_days == 14

    monkeypatch.setenv("VAULT_GC_GRACE_DAYS", "30")
    assert Settings.from_env().gc_grace_days == 30


def test_gc_grace_days_zero_is_a_valid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike VAULT_MANIFEST_KEEP, 0 means something here: no window at all.

    It must therefore be *accepted*, not rejected as "below the floor" — and
    the executor turns it into "no predicate is constructed"
    (``gc_execute.grace_window_exclusions``).
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_GC_GRACE_DAYS", "0")

    assert Settings.from_env().gc_grace_days == 0


def test_a_bad_gc_grace_days_fails_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """A typo in a deletion-path setting must not silently become "protect
    nothing". Rejected at startup, where somebody is looking."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    # WP 3.12: a negative value is refused by the digits-only syntax rule
    # before the floor check ever runs, and its message names the smallest
    # accepted value (0) rather than using the ">=" phrasing. Still a startup
    # RuntimeError — the protection this test exists for is unchanged.
    for bad in ("-1", "-14"):
        monkeypatch.setenv("VAULT_GC_GRACE_DAYS", bad)
        with pytest.raises(
            RuntimeError, match=r"smallest accepted value is 0"
        ):
            Settings.from_env()

    for garbage in ("fourteen", "14 days", "", " ", "1.5"):
        monkeypatch.setenv("VAULT_GC_GRACE_DAYS", garbage)
        if garbage.strip() == "":
            # Blank is "unset" everywhere in this module, not an error.
            assert Settings.from_env().gc_grace_days == 14
            continue
        with pytest.raises(RuntimeError, match="ASCII digits only"):
            Settings.from_env()


def test_agent_report_keep_below_two_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The diff needs the previous snapshot AND the new one — 1 is not a value.

    With keep=1 the prune inside the insert transaction would delete the
    predecessor, so every report would come back as a first report.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    for bad in ("1", "0"):
        monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", bad)
        with pytest.raises(RuntimeError, match="must be >= 2"):
            Settings.from_env()

    # WP 3.12: negatives now fail the digits-only syntax rule first (the
    # message names the floor, so it is still actionable).
    monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", "-3")
    with pytest.raises(RuntimeError, match=r"smallest accepted value is 2"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", "many")
    with pytest.raises(RuntimeError, match="ASCII digits only"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", "2")
    assert Settings.from_env().agent_report_keep == 2


# ==========================================================================
# WP 3.12: strict integer parsing for EVERY integer setting
# ==========================================================================

#: Every ``_env_int``-backed setting, with the attribute it lands on and a
#: valid value. Parameterizing over the whole list is the point: the hardening
#: is a property of ``_env_int``, so a future setting that bypassed it (or a
#: caller that stopped using it) shows up as a failing row here rather than as
#: one un-hardened variable nobody checked.
INTEGER_SETTINGS = [
    ("VAULT_PREFILL_TIMEOUT_SECONDS", "prefill_timeout_seconds", "7"),
    ("VAULT_AGENT_REPORT_KEEP", "agent_report_keep", "7"),
    ("VAULT_MANIFEST_KEEP", "manifest_keep", "7"),
    ("VAULT_GC_GRACE_DAYS", "gc_grace_days", "7"),
    ("VAULT_SCHEDULE_INTERVAL_MINUTES", "schedule_interval_minutes", "7"),
    ("VAULT_SCHEDULE_CLIENT_STALE_DAYS", "schedule_client_stale_days", "7"),
]

#: The four shapes Python's own ``int()`` accepts and an operator never means.
#: ``"1_0"`` is the nastiest: ``int("1_0")`` is **ten**, so it fails silently
#: rather than loudly. ``"٧"`` is ARABIC-INDIC DIGIT SEVEN — ``str.isdigit()``
#: is True for it, which is why the ASCII check has to come first.
SLOPPY_INTEGERS = [" 7 ", "+7", "-7", "1_0", "٧", "7\n", "7 ", " 7", "0x7"]


@pytest.mark.parametrize(("name", "attribute", "good"), INTEGER_SETTINGS)
def test_a_plain_integer_is_still_accepted(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str, good: str
) -> None:
    """The hardening must not have broken any legitimate value."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(name, good)

    assert getattr(Settings.from_env(), attribute) == int(good)


@pytest.mark.parametrize(("name", "attribute", "good"), INTEGER_SETTINGS)
@pytest.mark.parametrize("sloppy", SLOPPY_INTEGERS)
def test_sloppy_integers_are_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str, good: str, sloppy: str
) -> None:
    """docs/LEARNINGS.md's ``int()`` rule, applied to every integer setting.

    Each of these would otherwise start the service with a number nobody wrote
    down — ``"1_0"`` most of all, which ``int()`` reads as ten.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(name, sloppy)

    with pytest.raises(RuntimeError, match=name):
        Settings.from_env()


@pytest.mark.parametrize(("name", "attribute", "good"), INTEGER_SETTINGS)
def test_a_blank_integer_setting_still_means_unset(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str, good: str
) -> None:
    """A stray space after ``=`` in a .env file must not fail startup — blank
    is "not configured" for every setting in this module, and always has been.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    # The dataclass field default IS the documented default for every one of
    # these settings, so compare against it rather than restating numbers here.
    default = getattr(
        Settings(vault_api_key="k", db_path="x", cache_root="y", log_level="INFO"),
        attribute,
    )

    for blank in ("", "   ", "\t"):
        monkeypatch.setenv(name, blank)
        assert getattr(Settings.from_env(), attribute) == default


def test_every_env_example_value_still_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    """The shipped .env.example is the file operators copy — after tightening
    the parser, every value in it must still be accepted (WP 3.12).

    Read from the real file rather than a copy of its contents, so a future
    edit that introduces a value this parser rejects fails here.
    """
    import os

    env_example = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env.example"
    )
    with open(env_example, encoding="utf-8") as handle:
        lines = [
            line.strip()
            for line in handle
            if line.strip() and not line.strip().startswith("#") and "=" in line
        ]

    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    seen: dict[str, str] = {}
    for line in lines:
        name, _, value = line.partition("=")
        if name == "VAULT_API_KEY":
            continue
        monkeypatch.setenv(name, value)
        seen[name] = value

    # Nothing raises: every documented value is accepted as written.
    settings = Settings.from_env()

    # Both hardened families really were exercised — otherwise a future
    # .env.example that stopped shipping the numeric settings would make this
    # test pass without testing anything.
    assert seen.keys() & {name for name, _attr, _good in INTEGER_SETTINGS}
    assert seen.keys() & {name for name, _attr in FLOAT_SETTINGS}
    assert settings.worker_poll_seconds == float(seen["VAULT_WORKER_POLL_SECONDS"])
    assert settings.size_cache_ttl_seconds == float(seen["VAULT_SIZE_CACHE_TTL"])
    assert settings.gc_grace_days == int(seen["VAULT_GC_GRACE_DAYS"])
    assert len(seen) >= 8, "the .env.example parsing above found suspiciously little"


# ==========================================================================
# WP 3.12: VAULT_AUTO_GC
# ==========================================================================


def test_auto_gc_defaults_to_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """A feature that can delete files does not switch itself on."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_AUTO_GC", raising=False)

    settings = Settings.from_env()

    assert settings.auto_gc == "off"
    assert settings.auto_gc_enabled is False
    assert settings.auto_gc_executes is False


@pytest.mark.parametrize(
    ("value", "enabled", "executes"),
    [
        ("off", False, False),
        ("dry-run", True, False),
        ("execute", True, True),
        ("EXECUTE", True, True),
        ("  Dry-Run  ", True, False),
    ],
)
def test_auto_gc_accepts_the_three_modes(
    monkeypatch: pytest.MonkeyPatch, value: str, enabled: bool, executes: bool
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_AUTO_GC", value)

    settings = Settings.from_env()

    assert settings.auto_gc_enabled is enabled
    assert settings.auto_gc_executes is executes


@pytest.mark.parametrize("bad", ["exectue", "on", "true", "1", "dry run", "delete"])
def test_a_bad_auto_gc_value_fails_at_startup(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    """A typo must not silently mean "off": an operator who set this believes
    automatic collection is running."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_AUTO_GC", bad)

    with pytest.raises(RuntimeError, match="VAULT_AUTO_GC must be one of"):
        Settings.from_env()


# ==========================================================================
# WP 3.12 review carry-over: the SAME strictness for the float settings
# ==========================================================================

#: Every ``_env_float``-backed setting. Same parameterize-over-the-list device
#: as INTEGER_SETTINGS above, and for the same reason.
FLOAT_SETTINGS = [
    ("VAULT_WORKER_POLL_SECONDS", "worker_poll_seconds"),
    ("VAULT_SIZE_CACHE_TTL", "size_cache_ttl_seconds"),
]

#: The accepted grammar: ASCII digits, optionally one '.' with digits on both
#: sides. Nothing else.
GOOD_FLOATS = ["60", "1.0", "0.25", "3.5", "0.5", "120"]

#: Everything ``float()`` would have swallowed. ``"nan"`` is the reason this
#: exists: ``nan <= 0`` is False, so the old range check passed it through.
SLOPPY_FLOATS = [
    " 1.5 ", "+1.5", "-1.5", "1_0", "٧", "nan", "NaN", "inf", "-inf",
    "Infinity", "abc", "1e3", "1E3", ".5", "5.", "1.2.3", "1,5", "0x1",
]


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
@pytest.mark.parametrize("good", GOOD_FLOATS)
def test_a_plain_decimal_is_still_accepted(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str, good: str
) -> None:
    """Fractions are the whole point of a float setting — '3.5' must work."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(name, good)

    assert getattr(Settings.from_env(), attribute) == float(good)


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
@pytest.mark.parametrize("sloppy", SLOPPY_FLOATS)
def test_sloppy_floats_are_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str, sloppy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(name, sloppy)

    with pytest.raises(RuntimeError, match=name):
        Settings.from_env()


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
def test_nan_can_no_longer_slip_past_the_positive_check(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str
) -> None:
    """The specific hole this carry-over closes, named on its own.

    ``float("nan") <= 0`` is ``False``, so the pre-hardening guard accepted it.
    Downstream that is not harmless: a nan ``VAULT_SIZE_CACHE_TTL`` makes
    ``SizeCache``'s ``(now - computed_at) < ttl`` always false, so every request
    re-walks the whole depot tree; a nan ``VAULT_WORKER_POLL_SECONDS`` is fed
    straight to ``threading.Event.wait``.
    """
    import math

    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv(name, "nan")

    with pytest.raises(RuntimeError, match="not 'nan' or 'inf'"):
        Settings.from_env()

    # ...and the property that made it dangerous, stated so the test explains
    # itself: the old `value <= 0` guard genuinely does not catch this.
    assert (float("nan") <= 0) is False
    assert not math.isfinite(float("nan"))


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
def test_a_digit_string_that_overflows_to_inf_is_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str
) -> None:
    """The one way ``inf`` can still get past the literal grammar: 400 digits
    is a valid decimal literal that ``float()`` rounds to infinity."""
    import math

    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    huge = "9" * 400
    assert math.isinf(float(huge))  # the premise, not an assumption
    monkeypatch.setenv(name, huge)

    with pytest.raises(RuntimeError, match="too large"):
        Settings.from_env()


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
def test_a_blank_float_setting_still_means_unset(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    default = getattr(
        Settings(vault_api_key="k", db_path="x", cache_root="y", log_level="INFO"),
        attribute,
    )

    for blank in ("", "   ", "\t"):
        monkeypatch.setenv(name, blank)
        assert getattr(Settings.from_env(), attribute) == default


@pytest.mark.parametrize(("name", "attribute"), FLOAT_SETTINGS)
def test_zero_is_still_rejected_by_the_range_check(
    monkeypatch: pytest.MonkeyPatch, name: str, attribute: str
) -> None:
    """The pre-existing rule survives the new grammar: '0' and '0.0' are
    syntactically fine and still refused, because VAULT_SIZE_CACHE_TTL=0 would
    mean a full depot-tree walk on every request (there is deliberately no
    "disable the cache" setting)."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    for zero in ("0", "0.0", "0.000"):
        monkeypatch.setenv(name, zero)
        with pytest.raises(RuntimeError, match="must be > 0"):
            Settings.from_env()


# ---------------------------------------------------------------------------
# WP 3.11 (ADR-0008): the cache-event sweep settings
# ---------------------------------------------------------------------------


def _sweep_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Base environment: API key set, every sweep variable unset."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    for name in (
        "VAULT_EVENT_LOG_PATH",
        "VAULT_EVENT_SWEEP_INTERVAL_MINUTES",
        "VAULT_MISS_TRIGGER_COOLDOWN_MINUTES",
        "VAULT_MISS_TRIGGER_MAX_PER_SWEEP",
        "VAULT_BYPASS_WINDOW_DAYS",
        "VAULT_CLIENT_STATS_KEEP",
        "VAULT_EVENT_LOG_MAX_BYTES",
    ):
        monkeypatch.delenv(name, raising=False)


def test_the_event_sweep_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole feature hangs off one path, and it is empty by default."""
    _sweep_env(monkeypatch)

    settings = Settings.from_env()

    assert settings.event_log_path == ""
    assert settings.event_sweep_enabled is False
    assert settings.miss_trigger_enabled is False
    # The other values are still populated so an operator can see what WOULD
    # happen before switching it on.
    assert settings.event_sweep_interval_minutes == 5
    assert settings.miss_trigger_cooldown_minutes == 60
    assert settings.miss_trigger_max_per_sweep == 5
    assert settings.bypass_window_days == 3
    assert settings.client_stats_keep == 48
    assert settings.event_log_max_bytes == 64 * 1024 * 1024


def test_a_blank_event_log_path_disables_rather_than_failing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sweep_env(monkeypatch)
    monkeypatch.setenv("VAULT_EVENT_LOG_PATH", "   ")

    assert Settings.from_env().event_sweep_enabled is False


def test_the_miss_trigger_is_on_by_default_once_the_sweep_is_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PINNED DECISION: pointing at the log IS the opt-in (ADR-0001 hybrid)."""
    _sweep_env(monkeypatch)
    monkeypatch.setenv("VAULT_EVENT_LOG_PATH", "/vault/logs/event.log")

    settings = Settings.from_env()

    assert settings.event_sweep_enabled is True
    assert settings.miss_trigger_enabled is True


def test_a_zero_cooldown_is_the_triggers_off_switch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0 means OFF, deliberately -- never "no cooldown"."""
    _sweep_env(monkeypatch)
    monkeypatch.setenv("VAULT_EVENT_LOG_PATH", "/vault/logs/event.log")
    monkeypatch.setenv("VAULT_MISS_TRIGGER_COOLDOWN_MINUTES", "0")

    settings = Settings.from_env()

    assert settings.miss_trigger_cooldown_minutes == 0
    assert settings.event_sweep_enabled is True, "statistics keep running"
    assert settings.miss_trigger_enabled is False


def test_event_log_max_bytes_zero_disables_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sweep_env(monkeypatch)
    monkeypatch.setenv("VAULT_EVENT_LOG_MAX_BYTES", "0")

    assert Settings.from_env().event_log_max_bytes == 0


@pytest.mark.parametrize(
    "name",
    [
        "VAULT_EVENT_SWEEP_INTERVAL_MINUTES",
        "VAULT_MISS_TRIGGER_MAX_PER_SWEEP",
        "VAULT_BYPASS_WINDOW_DAYS",
        "VAULT_CLIENT_STATS_KEEP",
    ],
)
def test_the_sweep_settings_that_must_be_positive_reject_zero(
    monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Two of the seven accept 0 (as an off switch); these four must not."""
    _sweep_env(monkeypatch)
    monkeypatch.setenv(name, "0")

    with pytest.raises(RuntimeError, match=name):
        Settings.from_env()


@pytest.mark.parametrize(
    "name",
    [
        "VAULT_EVENT_SWEEP_INTERVAL_MINUTES",
        "VAULT_MISS_TRIGGER_COOLDOWN_MINUTES",
        "VAULT_MISS_TRIGGER_MAX_PER_SWEEP",
        "VAULT_BYPASS_WINDOW_DAYS",
        "VAULT_CLIENT_STATS_KEEP",
        "VAULT_EVENT_LOG_MAX_BYTES",
    ],
)
@pytest.mark.parametrize("value", [" 5 ", "+5", "-5", "1_0", "٥", "1.5"])
def test_sloppy_sweep_numbers_are_refused_at_startup(
    monkeypatch: pytest.MonkeyPatch, name: str, value: str
) -> None:
    """The same house rule as every other numeric setting (WP 3.12)."""
    _sweep_env(monkeypatch)
    monkeypatch.setenv(name, value)

    with pytest.raises(RuntimeError, match=name):
        Settings.from_env()


# ---------------------------------------------------------------------------
# WP 3.13: generic webhook notifications
# ---------------------------------------------------------------------------


def _webhook_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    for name in (
        "VAULT_WEBHOOK_URL",
        "VAULT_WEBHOOK_EVENTS",
        "VAULT_WEBHOOK_TIMEOUT_SECONDS",
        "VAULT_NAME",
    ):
        monkeypatch.delenv(name, raising=False)


def test_webhooks_are_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from vault_api.config import WEBHOOK_EVENTS_ALL

    _webhook_env(monkeypatch)

    settings = Settings.from_env()

    assert settings.webhook_url == ""
    assert settings.webhook_enabled is False
    # Still populated with the "everything" default, so turning the URL on
    # alone (no VAULT_WEBHOOK_EVENTS) sends all four events.
    assert settings.webhook_events == frozenset(WEBHOOK_EVENTS_ALL)
    assert settings.webhook_timeout_seconds == 5.0
    assert settings.vault_name == ""


def test_webhook_url_alone_enables_the_feature(monkeypatch: pytest.MonkeyPatch) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_URL", "https://example.invalid/hook")

    settings = Settings.from_env()

    assert settings.webhook_enabled is True


def test_webhook_events_accepts_a_subset(monkeypatch: pytest.MonkeyPatch) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_EVENTS", "job.done, job.error")

    settings = Settings.from_env()

    assert settings.webhook_events == {"job.done", "job.error"}


def test_webhook_events_rejects_an_unknown_name(monkeypatch: pytest.MonkeyPatch) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_EVENTS", "job.done,job.finished")

    with pytest.raises(RuntimeError, match="VAULT_WEBHOOK_EVENTS"):
        Settings.from_env()


def test_webhook_events_rejects_an_empty_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stray comma ('job.done,,job.error') must not silently become two
    events — it is refused loudly, the same house rule as every other
    list/enum setting in this module."""
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_EVENTS", "job.done,,job.error")

    with pytest.raises(RuntimeError, match="VAULT_WEBHOOK_EVENTS"):
        Settings.from_env()


def test_webhook_timeout_default_and_override(monkeypatch: pytest.MonkeyPatch) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_TIMEOUT_SECONDS", "2.5")

    assert Settings.from_env().webhook_timeout_seconds == 2.5


def test_webhook_timeout_rejects_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_TIMEOUT_SECONDS", "0")

    with pytest.raises(RuntimeError, match="must be > 0"):
        Settings.from_env()


@pytest.mark.parametrize("sloppy", [" 5 ", "+5", "-5", "1_0", "٥", "nan", "inf"])
def test_webhook_timeout_rejects_sloppy_values(
    monkeypatch: pytest.MonkeyPatch, sloppy: str
) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_WEBHOOK_TIMEOUT_SECONDS", sloppy)

    with pytest.raises(RuntimeError, match="VAULT_WEBHOOK_TIMEOUT_SECONDS"):
        Settings.from_env()


def test_vault_name_defaults_to_empty_and_is_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _webhook_env(monkeypatch)
    monkeypatch.setenv("VAULT_NAME", "  homelab  ")

    assert Settings.from_env().vault_name == "homelab"


# ==========================================================================
# Settings-API work package (ADR-0009): VAULT_SETTINGS_READONLY
# ==========================================================================


def test_settings_readonly_defaults_to_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    assert Settings.from_env().settings_readonly is False


@pytest.mark.parametrize("truthy", ["1", "true", "True", "YES", "on", " on "])
def test_settings_readonly_accepts_true_spellings(
    monkeypatch: pytest.MonkeyPatch, truthy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SETTINGS_READONLY", truthy)

    assert Settings.from_env().settings_readonly is True


@pytest.mark.parametrize("falsy", ["0", "false", "False", "NO", "off", ""])
def test_settings_readonly_accepts_false_spellings(
    monkeypatch: pytest.MonkeyPatch, falsy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SETTINGS_READONLY", falsy)

    assert Settings.from_env().settings_readonly is False


@pytest.mark.parametrize("bad", ["yeah", "1.0", "enabled", "2"])
def test_settings_readonly_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SETTINGS_READONLY", bad)

    with pytest.raises(RuntimeError, match="VAULT_SETTINGS_READONLY"):
        Settings.from_env()


# ==========================================================================
# WP 4d (plan §7 Phase 4d): VAULT_SWEEP_INCLUDE_CACHED
# ==========================================================================


def test_sweep_include_cached_defaults_to_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation pin: flip ``DEFAULT_SWEEP_INCLUDE_CACHED`` to ``True`` and
    this test dies -- a feature that spends bandwidth/disk on games nobody
    asked for must be an explicit opt-in (plan §7 Phase 4d), never the
    out-of-the-box behaviour of a fresh install."""
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.delenv("VAULT_SWEEP_INCLUDE_CACHED", raising=False)

    assert Settings.from_env().sweep_include_cached is False


@pytest.mark.parametrize("truthy", ["1", "true", "True", "YES", "on", " on "])
def test_sweep_include_cached_accepts_true_spellings(
    monkeypatch: pytest.MonkeyPatch, truthy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SWEEP_INCLUDE_CACHED", truthy)

    assert Settings.from_env().sweep_include_cached is True


@pytest.mark.parametrize("falsy", ["0", "false", "False", "NO", "off", ""])
def test_sweep_include_cached_accepts_false_spellings(
    monkeypatch: pytest.MonkeyPatch, falsy: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SWEEP_INCLUDE_CACHED", falsy)

    assert Settings.from_env().sweep_include_cached is False


@pytest.mark.parametrize("bad", ["yeah", "1.0", "enabled", "2"])
def test_sweep_include_cached_rejects_anything_else(
    monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SWEEP_INCLUDE_CACHED", bad)

    with pytest.raises(RuntimeError, match="VAULT_SWEEP_INCLUDE_CACHED"):
        Settings.from_env()


def test_env_bool_error_names_the_original_unstripped_value_and_the_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """N4 (reviewer nitpick, 2026-08-18 review round): the refactored
    ``_env_bool`` had started reporting the STRIPPED value in its error
    (losing the surrounding whitespace that made the typo visible) and had
    dropped the "or leave it blank for the default" hint every earlier
    version had. Both restored -- pinned here via
    ``VAULT_SETTINGS_READONLY`` (default ``False``), the field this helper
    backs, exactly as the reviewer measured it.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")
    monkeypatch.setenv("VAULT_SETTINGS_READONLY", " bogus ")

    with pytest.raises(RuntimeError) as excinfo:
        Settings.from_env()

    message = str(excinfo.value)
    assert "' bogus '" in message  # the ORIGINAL value, whitespace and all
    assert "blank" in message.lower()
    assert "False" in message  # the default it falls back to


# ==========================================================================
# Settings-API work package: validate_webhook_url (used only by
# PATCH /v1/settings, NOT by Settings.from_env — see the function's own
# docstring for why no startup grammar exists for this field to reuse).
# ==========================================================================


def test_validate_webhook_url_accepts_blank_as_disabled() -> None:
    from vault_api.config import validate_webhook_url

    assert validate_webhook_url("") == ""
    assert validate_webhook_url("   ") == ""


@pytest.mark.parametrize(
    "good",
    [
        "http://example.invalid/hook",
        "https://example.invalid/hook",
        "https://user:pass@example.invalid:8443/hook?x=1",
    ],
)
def test_validate_webhook_url_accepts_http_and_https(good: str) -> None:
    from vault_api.config import validate_webhook_url

    assert validate_webhook_url(good) == good


@pytest.mark.parametrize(
    "bad",
    [
        "not a url",
        "ftp://example.invalid/hook",
        "example.invalid/hook",
        "file:///etc/passwd",
    ],
)
def test_validate_webhook_url_rejects_non_http_schemes(bad: str) -> None:
    from vault_api.config import validate_webhook_url

    with pytest.raises(ValueError):
        validate_webhook_url(bad)
