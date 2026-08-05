"""Application configuration, read once from environment variables at startup.

No config framework is used on purpose (plan §9: keep vault-api simple). A
plain frozen dataclass plus a small ``.env`` loader is enough for the four
settings this project needs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

try:
    # Optional convenience for local/native dev: load a .env file if present.
    # Never overrides variables already set in the real environment.
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover - python-dotenv is a pinned dependency,
    # but we don't want a missing optional import to break the app.
    pass


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the environment at startup."""

    vault_api_key: str
    db_path: str
    cache_root: str
    log_level: str

    @staticmethod
    def from_env() -> "Settings":
        """Build Settings from the current environment.

        Raises RuntimeError if VAULT_API_KEY is missing or empty — there is
        deliberately no default (plan §9: no unauthenticated endpoints beyond
        the documented /v1/health exception).
        """
        api_key = os.environ.get("VAULT_API_KEY", "").strip()
        if not api_key:
            raise RuntimeError(
                "VAULT_API_KEY is required and must not be empty. "
                "Set it in the environment or in a .env file "
                "(copy api/.env.example to api/.env and fill it in)."
            )

        return Settings(
            vault_api_key=api_key,
            db_path=os.environ.get("VAULT_DB_PATH", "./vault.db"),
            cache_root=os.environ.get("VAULT_CACHE_ROOT", "./cache"),
            log_level=os.environ.get("VAULT_LOG_LEVEL", "INFO"),
        )
