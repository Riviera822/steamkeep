"""Application configuration, read once from environment variables at startup.

No config framework is used on purpose (plan §9: keep vault-api simple). A
plain frozen dataclass plus a small ``.env`` loader is enough for the four
settings this project needs.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

from vault_api.schedule_window import (
    ScheduleWindow,
    ScheduleWindowError,
    parse_window,
)

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

#: How many archived manifest ``.bin`` files ``prune_archive`` keeps per
#: depot (WP 3.2, ADR-0006 decision 3 / research risk 4: "the archive needs
#: its own retention"). **Decision, stated plainly:** this is the TOTAL count
#: kept per depot (the current manifest plus its predecessors), the same
#: "keep the last N" semantics as ``VAULT_AGENT_REPORT_KEEP`` above — not "N
#: previous in addition to the current one". 3 is enough to look back across
#: a couple of game updates without the archive growing unbounded.
DEFAULT_MANIFEST_KEEP = 3

#: Hard floor for VAULT_MANIFEST_KEEP — 0 would mean "keep nothing", which
#: defeats the point of archiving at all.
MIN_MANIFEST_KEEP = 1

#: How long chunks are protected from garbage collection after they were
#: stored (WP 3.8b, ADR-0007's beta-branch addendum, decision A).
#:
#: **Why this exists.** Opt-in Steam beta branches reach the cache only via
#: store-on-miss — SteamPrefill has no branch selection — and their chunks
#: appear in no ``public`` manifest, so plain manifest-diff GC classifies every
#: one of them as an orphan and collects it. The same is true of anything else
#: a real client pulled through vault-core against a manifest vault-api never
#: recorded. The window buys those chunks time: whatever was stored in the last
#: N days is held back, so the beta tester who downloaded a build on Monday
#: still has it after Tuesday night's GC.
#:
#: 14 days is chosen to cover a normal beta/demo test cycle plus a weekend,
#: while still letting a stale one-off download age out within a fortnight.
#: ``0`` disables the window entirely (the predicate is then not even
#: constructed) — the pre-WP-3.8b behaviour, i.e. every orphan the plan names
#: is deleted.
DEFAULT_GC_GRACE_DAYS = 14

#: How long the scheduler waits between sweeps of the installed list (WP 3.5).
#: Plan §7 Phase 3 spells the cron window out as "e.g. 09:00-17:00, every 3 h"
#: — 180 minutes is that "every 3 h", verbatim.
DEFAULT_SCHEDULE_INTERVAL_MINUTES = 180

#: A client whose most recent agent report is older than this is left OUT of
#: the scheduler's target set (WP 3.5). Rationale: the target set is "what is
#: installed on the gaming machines right now" (plan A8). A machine that has
#: been off for a week is not reporting *anything* right now, and its last
#: snapshot is a guess about the past — continuing to prefill (and keep
#: current) a library nobody has confirmed in days quietly burns bandwidth and
#: disk on a decommissioned PC. 7 days is generous enough to cover a holiday
#: without the Steam Deck dropping out of the set.
DEFAULT_SCHEDULE_CLIENT_STALE_DAYS = 7


#: WP 3.12. ``VAULT_AUTO_GC`` — should a successful prefill that actually
#: updated something queue a garbage-collection job for that app?
#:
#: ``off`` (the default) keeps GC entirely operator-driven, which is the safe
#: shape for a feature that can delete files. ``dry-run`` queues a reporting-only
#: GC job, so an operator can watch what automatic collection *would* reclaim
#: before trusting it. ``execute`` queues a deleting one.
#:
#: Deliberately a three-value string rather than a boolean: the dry-run rung is
#: the whole point of an opt-in ladder for a destructive feature, and a boolean
#: would have forced the operator to choose between "no information" and "it
#: deletes now".
AUTO_GC_OFF = "off"
AUTO_GC_DRY_RUN = "dry-run"
AUTO_GC_EXECUTE = "execute"
AUTO_GC_MODES = (AUTO_GC_OFF, AUTO_GC_DRY_RUN, AUTO_GC_EXECUTE)

DEFAULT_AUTO_GC = AUTO_GC_OFF


def _default_steamprefill_cache_dir() -> str:
    """Platform default for SteamPrefill's manifest temp-cache directory
    (docs/research/phase3-manifests.md §1a): ``%LOCALAPPDATA%\\SteamPrefill\\v1``
    on Windows, ``$HOME/.cache/SteamPrefill/v1`` everywhere else (the path
    inside the container, volume-backed — wiring that volume into deploy/'s
    compose file is explicitly NOT this work package's scope, see
    api/README.md's WP 3.2 note).

    Falls back to ``~/AppData/Local`` if ``LOCALAPPDATA`` is unset on Windows
    (matches how Windows itself derives the variable), so this never raises —
    a wrong guess here only means a job's manifest ingestion finds nothing to
    ingest (warn-and-skip, never a startup failure), not a crash.
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if not base:
            base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "SteamPrefill", "v1")
    return os.path.join(os.path.expanduser("~"), ".cache", "SteamPrefill", "v1")


def _default_manifest_archive_dir(db_path: str) -> str:
    """Default archive location: a ``manifests`` sibling of the database file.

    WP 3.2 scope note: consistent with a single-volume deployment (db +
    archive on the same persistent volume) is the intent, but wiring a
    dedicated volume/mount for it in ``deploy/`` is explicitly NOT this work
    package's scope — a follow-up on top of WP 1.9's Compose file.

    **``db_path`` is resolved as a plain filesystem path (WP 3.2 review
    note), not with any sqlite-specific awareness.** ``os.path.abspath``
    doesn't know sqlite's special ``":memory:"`` URI, so
    ``VAULT_DB_PATH=":memory:"`` would resolve to a literal (and harmless,
    if slightly odd-looking) sibling directory ``.../:memory:/manifests``
    under the current working directory — this project never sets
    ``VAULT_DB_PATH`` that way (every test and deployment uses a real file
    path), so this is a documented quirk, not a bug worth guarding against. A
    relative ``db_path`` (the ``"./vault.db"`` default) resolves against the
    process's current working directory, same as ``db_path`` itself already
    does everywhere else in this codebase (``db.init_db``,
    ``deletion.resolve_depot_root``).
    """
    parent = os.path.dirname(os.path.abspath(db_path)) or "."
    return os.path.join(parent, "manifests")


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """Read an integer env var >= ``minimum``, falling back to ``default``.

    **Strict by house rule (WP 3.12, docs/LEARNINGS.md "Parsers"):** Python's
    ``int()`` is far more permissive than anybody configuring a service
    expects. It accepts ``" 7 "`` (surrounding whitespace), ``"+7"`` and
    ``"-7"`` (signs), ``"1_0"`` — which is **ten**, not one-zero — and non-ASCII
    digits such as ``"٧"``. Each of those is a typo that would start the
    service with a number nobody wrote down, on settings that decide how long a
    download may run and how long deleted-file protection lasts. So a non-blank
    value must be ASCII digits and nothing else:

    ``raw.isascii() and raw.isdigit()`` rejects all four cases in one check
    (``"1_0".isdigit()`` is ``False``; ``"+7".isdigit()`` is ``False``;
    ``" 7 ".isdigit()`` is ``False``; ``"٧".isascii()`` is ``False``), and
    requires at least one character.

    **Blank still means "unset".** ``VAULT_GC_GRACE_DAYS=`` (or a value that is
    only whitespace, which is what a stray space after ``=`` in a ``.env`` file
    produces) falls back to ``default``, exactly as it always has and exactly as
    every non-numeric setting in this module treats a blank. The strict rule
    applies to values that actually carry content: an operator who typed
    something meant something, and ``" 7 "`` is then a typo worth reporting
    rather than one worth guessing about.

    Consequence worth stating: because digits-only excludes a leading ``-``,
    this function cannot express a negative ``minimum``. No setting needs one
    (the floors in use are 0 and 1), and the assert below makes that a loud
    programming error rather than a silently unreachable branch if one ever
    does.
    """
    assert minimum >= 0, "_env_int cannot validate a negative minimum"
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    if not (raw.isascii() and raw.isdigit()):
        raise RuntimeError(
            f"{name} must be a plain integer written in ASCII digits only — no "
            "signs, spaces, underscores or thousands separators, and no "
            f"negative values (the smallest accepted value is {minimum}). "
            f"Got {raw!r}."
        )
    try:
        value = int(raw)
    except ValueError as exc:  # pragma: no cover - only for absurdly long input
        # Digits-only input can still be refused by CPython's
        # int_max_str_digits limit (4300 digits by default), so this stays a
        # guarded conversion rather than a bare int().
        raise RuntimeError(f"{name} is not a usable integer: {raw!r} ({exc})") from exc
    if value < minimum:
        # The default floor is the plain "positive integer" case; phrase it the
        # way it has always been phrased so existing messages don't change.
        limit = "> 0" if minimum == 1 else f">= {minimum}"
        raise RuntimeError(f"{name} must be {limit}, got {value}")
    return value


def _env_auto_gc(name: str = "VAULT_AUTO_GC") -> str:
    """Read ``VAULT_AUTO_GC`` (WP 3.12). Blank/unset = ``off``.

    Validated at STARTUP rather than at the call site in the worker, for the
    same reason ``VAULT_SCHEDULE_WINDOW`` is: a typo must not surface hours
    later inside a background thread, and ``VAULT_AUTO_GC=exectue`` silently
    falling back to ``off`` would leave an operator believing automatic
    collection is running when nothing is. Case-insensitive and
    whitespace-tolerant (this one IS a word, not a number, and ``Execute`` is
    unambiguous), but a value that is not one of the three is refused with all
    three named.
    """
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return DEFAULT_AUTO_GC
    if raw not in AUTO_GC_MODES:
        raise RuntimeError(
            f"{name} must be one of {', '.join(AUTO_GC_MODES)}, got "
            f"{os.environ.get(name, '')!r}."
        )
    return raw


def _is_plain_decimal(raw: str) -> bool:
    """Is ``raw`` a plain ASCII decimal literal — ``digits`` or ``digits.digits``?

    The float counterpart of ``_env_int``'s ``isascii() and isdigit()`` check,
    and written the same way (``str.isdigit`` on ASCII text is exactly
    ``[0-9]+``) rather than with a regex, because ``re``'s ``\\d`` matches
    Unicode digits unless ``re.ASCII`` is passed — the very trap the
    ``isascii()`` gate exists to close.

    Accepts: ``"60"``, ``"1.0"``, ``"0.25"``, ``"3.5"``.
    Rejects everything else, including ``".5"`` and ``"5."`` (write ``0.5`` and
    ``5``), signs, whitespace, underscores, exponents, and — the reason this
    function exists at all — ``"nan"``/``"inf"``.
    """
    if not raw.isascii():
        return False
    whole, dot, fraction = raw.partition(".")
    if dot:
        return whole.isdigit() and fraction.isdigit()
    return whole.isdigit()


def _env_float(name: str, default: float) -> float:
    """Read a positive float env var, falling back to ``default``.

    **Strict by the same house rule as ``_env_int`` (WP 3.12 review), and for a
    sharper reason.** ``float()`` accepts everything ``int()`` does — ``" 1.5 "``,
    ``"+1.5"``, ``"1_0"`` (**ten**), non-ASCII digits — *plus* ``"nan"`` and
    ``"inf"``, and ``nan`` is the one input that gets past a range check
    silently: ``nan <= 0`` is ``False``, so the old guard below let it through.
    The two settings this reads are not harmless if that happens:

    - ``VAULT_SIZE_CACHE_TTL=nan`` makes ``SizeCache``'s freshness test
      ``(now - computed_at) < ttl`` **always false**, so every single request
      re-walks the whole ``depot/`` tree — precisely the footgun the
      "``0`` is rejected, there is no disable switch" rule was written to
      prevent, reintroduced through the back door.
    - ``VAULT_WORKER_POLL_SECONDS=nan`` is handed straight to
      ``threading.Event.wait(nan)`` on every idle tick.

    So the value must be a plain ASCII decimal literal (see
    ``_is_plain_decimal``). **Blank still means "unset"**, same as everywhere
    else in this module.
    """
    raw = os.environ.get(name, "")
    if not raw.strip():
        return default
    if not _is_plain_decimal(raw):
        raise RuntimeError(
            f"{name} must be a plain positive number written in ASCII digits "
            "with at most one decimal point (e.g. '60' or '0.25') — no signs, "
            "spaces, underscores, exponents, and not 'nan' or 'inf'. "
            f"Got {raw!r}."
        )
    try:
        value = float(raw)
    except ValueError as exc:  # pragma: no cover - the grammar above precludes it
        raise RuntimeError(f"{name} is not a usable number: {raw!r} ({exc})") from exc
    # A grammatically valid but absurdly long digit string (400 digits, say)
    # still overflows to `inf` in float() — the one way `inf` can survive the
    # literal check above, so it is refused here rather than assumed away.
    if not math.isfinite(value):
        raise RuntimeError(
            f"{name} is too large to represent as a number, got {raw!r}."
        )
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
    # WP 3.2. Where archived manifest .bin files are copied durably (they do
    # NOT survive SteamPrefill's own `clear-temp`). Literal default here is
    # deliberately dumb ("./manifests", mirroring db_path/cache_root's own
    # plain literal defaults) — `from_env()` computes the smarter
    # db-relative default (see `_default_manifest_archive_dir`) when the env
    # var is unset, since only `from_env()` knows the real `db_path`.
    manifest_archive_dir: str = "./manifests"
    # WP 3.2. How many archived manifests `prune_archive` keeps per depot.
    manifest_keep: int = DEFAULT_MANIFEST_KEEP
    # WP 3.2. SteamPrefill's own manifest temp-cache directory to scan after a
    # successful prefill job (docs/research/phase3-manifests.md §1a).
    # `default_factory` (not a class-body literal) so each direct
    # construction re-reads LOCALAPPDATA/HOME at call time, same as
    # `from_env()` does when the env var is unset.
    steamprefill_cache_dir: str = field(default_factory=_default_steamprefill_cache_dir)
    # WP 3.8b. Days a stored chunk is protected from GC (ADR-0007 beta-branch
    # addendum, decision A). 0 = no window, every planned orphan is deleted.
    gc_grace_days: int = DEFAULT_GC_GRACE_DAYS
    # WP 3.5. The daytime window the scheduler sweeps in (plan §7 Phase 3).
    # ``None`` = scheduler disabled, and that is the DEFAULT on purpose: the
    # scheduler starts Steam logins and downloads on its own schedule, which
    # is not something a fresh install should begin doing because the operator
    # never got around to reading the docs. Opt in by setting
    # VAULT_SCHEDULE_WINDOW.
    schedule_window: ScheduleWindow | None = None
    # WP 3.5. Minimum spacing between two sweeps.
    schedule_interval_minutes: int = DEFAULT_SCHEDULE_INTERVAL_MINUTES
    # WP 3.5. Clients whose newest agent report is older than this are
    # excluded from the sweep's target set.
    schedule_client_stale_days: int = DEFAULT_SCHEDULE_CLIENT_STALE_DAYS
    # WP 3.12. 'off' | 'dry-run' | 'execute' — see AUTO_GC_MODES above.
    auto_gc: str = DEFAULT_AUTO_GC

    @property
    def scheduler_enabled(self) -> bool:
        """True iff a window is configured (WP 3.5) — the one enable switch."""
        return self.schedule_window is not None

    @property
    def auto_gc_enabled(self) -> bool:
        """True iff a successful, updating prefill should queue a GC job."""
        return self.auto_gc != AUTO_GC_OFF

    @property
    def auto_gc_executes(self) -> bool:
        """True iff auto-queued GC jobs are allowed to DELETE.

        Spelled as its own property so the one place that turns configuration
        into ``enqueue_gc(execute=...)`` reads as a single named fact rather
        than a string comparison inlined in the worker.
        """
        return self.auto_gc == AUTO_GC_EXECUTE

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

        db_path = os.environ.get("VAULT_DB_PATH", "./vault.db")
        manifest_archive_dir = os.environ.get("VAULT_MANIFEST_ARCHIVE_DIR", "").strip()
        steamprefill_cache_dir = os.environ.get("VAULT_STEAMPREFILL_CACHE_DIR", "").strip()

        # WP 3.5. Unset/blank = the scheduler stays off (the safe default);
        # anything else must parse NOW, at startup, rather than failing on the
        # first tick hours later inside a background thread where nobody is
        # looking. The interval and staleness bound are validated even when no
        # window is set, so a typo in them surfaces immediately too rather
        # than the day the operator enables the window.
        raw_window = os.environ.get("VAULT_SCHEDULE_WINDOW", "").strip()
        schedule_window: ScheduleWindow | None = None
        if raw_window:
            try:
                schedule_window = parse_window(raw_window)
            except ScheduleWindowError as exc:
                raise RuntimeError(f"VAULT_SCHEDULE_WINDOW is invalid: {exc}") from exc

        return Settings(
            vault_api_key=api_key,
            db_path=db_path,
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
            manifest_archive_dir=manifest_archive_dir
            or _default_manifest_archive_dir(db_path),
            manifest_keep=_env_int(
                "VAULT_MANIFEST_KEEP", DEFAULT_MANIFEST_KEEP, minimum=MIN_MANIFEST_KEEP
            ),
            steamprefill_cache_dir=steamprefill_cache_dir
            or _default_steamprefill_cache_dir(),
            # minimum=0 because 0 is a meaningful value here ("no grace
            # window"), unlike VAULT_MANIFEST_KEEP where 0 would defeat the
            # feature. A negative value or anything non-integer is still
            # refused at startup — a typo must not silently become "protect
            # nothing" on a deletion path.
            gc_grace_days=_env_int("VAULT_GC_GRACE_DAYS", DEFAULT_GC_GRACE_DAYS, minimum=0),
            schedule_window=schedule_window,
            schedule_interval_minutes=_env_int(
                "VAULT_SCHEDULE_INTERVAL_MINUTES", DEFAULT_SCHEDULE_INTERVAL_MINUTES
            ),
            schedule_client_stale_days=_env_int(
                "VAULT_SCHEDULE_CLIENT_STALE_DAYS", DEFAULT_SCHEDULE_CLIENT_STALE_DAYS
            ),
            auto_gc=_env_auto_gc(),
        )
