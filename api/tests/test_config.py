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
