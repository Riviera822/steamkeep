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

    for bad in ("0", "-1"):
        monkeypatch.setenv("VAULT_MANIFEST_KEEP", bad)
        # minimum=1 phrases as "> 0" (_env_int's existing wording rule, same
        # as VAULT_PREFILL_TIMEOUT_SECONDS/VAULT_WORKER_POLL_SECONDS above).
        with pytest.raises(RuntimeError, match=r"must be > 0"):
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
    with pytest.raises(RuntimeError, match="must be an integer"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_SCHEDULE_INTERVAL_MINUTES", "180")
    monkeypatch.setenv("VAULT_SCHEDULE_CLIENT_STALE_DAYS", "-1")
    with pytest.raises(RuntimeError, match="VAULT_SCHEDULE_CLIENT_STALE_DAYS"):
        Settings.from_env()


def test_agent_report_keep_below_two_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """The diff needs the previous snapshot AND the new one — 1 is not a value.

    With keep=1 the prune inside the insert transaction would delete the
    predecessor, so every report would come back as a first report.
    """
    monkeypatch.setenv("VAULT_API_KEY", "some-key")

    for bad in ("1", "0", "-3"):
        monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", bad)
        with pytest.raises(RuntimeError, match="must be >= 2"):
            Settings.from_env()

    monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", "many")
    with pytest.raises(RuntimeError, match="must be an integer"):
        Settings.from_env()

    monkeypatch.setenv("VAULT_AGENT_REPORT_KEEP", "2")
    assert Settings.from_env().agent_report_keep == 2
