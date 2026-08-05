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
