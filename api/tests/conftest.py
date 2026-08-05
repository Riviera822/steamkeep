from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from vault_api.config import Settings
from vault_api.main import create_app

TEST_API_KEY = "test-api-key-do-not-use-in-prod"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    return TestClient(app)
