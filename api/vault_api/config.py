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


#: Default wall-clock budget for one SteamPrefill subprocess run. A large
#: game legitimately takes hours on a slow line, so the default is
#: deliberately generous — this is a runaway/hang backstop, not a
#: performance knob (WP 1.4).
DEFAULT_PREFILL_TIMEOUT_SECONDS = 14400  # 4 hours

#: How long the job worker sleeps between polls of an empty queue.
DEFAULT_WORKER_POLL_SECONDS = 1.0

#: Default TTL for the in-process per-game size cache (plan §3: "du over
#: depot folders, cached", WP 1.5). A disk walk over the whole depot/ tree is
#: not free, so repeated GET /v1/games / /v1/cache/summary calls within this
#: window reuse the last scan instead of re-walking.
DEFAULT_SIZE_CACHE_TTL_SECONDS = 60.0

#: How many agent report snapshots to keep per client_id (WP 2.4). The agent
#: reports its full installed list every ~30 min (plan §3), so without a
#: retention policy ``agent_reports`` grows forever. 20 keeps roughly the last
#: 10 hours of a default reporting interval — enough to look back at what a
#: machine installed/removed during a day, and still tiny on disk.
#: The floor is 2 (mirrored as ``agent_reports.MIN_REPORTS_KEPT``, which clamps
#: defensively): the diff needs the previous snapshot next to the one being
#: written.
DEFAULT_AGENT_REPORT_KEEP = 20

#: Hard floor for VAULT_AGENT_REPORT_KEEP — see above.
MIN_AGENT_REPORT_KEEP = 2


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read an integer env var >= ``minimum``, falling back to ``default``."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer, got {raw!r}") from exc
    if value < minimum:
        # The default floor is the plain "positive integer" case; phrase it the
        # way it has always been phrased so existing messages don't change.
        limit = "> 0" if minimum == 1 else f">= {minimum}"
        raise RuntimeError(f"{name} must be {limit}, got {value}")
    return value


def _env_float(name: str, default: float) -> float:
    """Read a positive float env var, falling back to ``default``."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise RuntimeError(f"{name} must be > 0, got {value}")
    return value


@dataclass(frozen=True)
class Settings:
    """Immutable snapshot of the environment at startup."""

    vault_api_key: str
    db_path: str
    cache_root: str
    log_level: str
    # WP 1.4. Path to the SteamPrefill executable. Deliberately NOT required
    # at startup: vault-api must still serve /v1/games, /v1/mapping and
    # /v1/health on a box where SteamPrefill hasn't been set up yet. A prefill
    # job then fails with a clear per-job error instead of taking the whole
    # app down (see vault_api/prefill.py).
    steamprefill_path: str = ""
    prefill_timeout_seconds: int = DEFAULT_PREFILL_TIMEOUT_SECONDS
    worker_poll_seconds: float = DEFAULT_WORKER_POLL_SECONDS
    # WP 1.5. TTL (seconds) for the in-process per-game size cache.
    size_cache_ttl_seconds: float = DEFAULT_SIZE_CACHE_TTL_SECONDS
    # WP 2.4. Snapshots kept per client in agent_reports (retention).
    agent_report_keep: int = DEFAULT_AGENT_REPORT_KEEP

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
            steamprefill_path=os.environ.get("VAULT_STEAMPREFILL_PATH", "").strip(),
            prefill_timeout_seconds=_env_int(
                "VAULT_PREFILL_TIMEOUT_SECONDS", DEFAULT_PREFILL_TIMEOUT_SECONDS
            ),
            worker_poll_seconds=_env_float(
                "VAULT_WORKER_POLL_SECONDS", DEFAULT_WORKER_POLL_SECONDS
            ),
            size_cache_ttl_seconds=_env_float(
                "VAULT_SIZE_CACHE_TTL", DEFAULT_SIZE_CACHE_TTL_SECONDS
            ),
            agent_report_keep=_env_int(
                "VAULT_AGENT_REPORT_KEEP",
                DEFAULT_AGENT_REPORT_KEEP,
                minimum=MIN_AGENT_REPORT_KEEP,
            ),
        )
