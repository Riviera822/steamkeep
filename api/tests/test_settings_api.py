"""Persisted settings (ADR-0009): ``GET``/``PATCH /v1/settings`` and the
``settings_store`` module underneath them.

Layout, mirroring ``docs/LEARNINGS.md``'s testing-discipline entries:

1. **Precedence** — db > env > default, with an explicit test per direction
   so flipping the priority order in ``settings_store._source_without_override``
   or ``effective_settings`` kills a NAMED test, not just "something in the
   suite".
2. **PATCH validation** — one bad-value case per grammar family (schedule
   window, strict int, auto-GC enum, webhook URL scheme), each asserted
   ``422`` AND not persisted (a second GET/`get_override` call proves it).
3. **Env-only / readonly / auth** — the operator hard-locks.
4. **Redaction** — a STRING-LITERAL pin (docs/LEARNINGS.md "Security-constant
   pins must assert STRING LITERALS"): the secret substring must be provably
   ABSENT from the raw response text, not merely "not equal to what
   ``redact_url`` would produce if called again" (that would only prove the
   test and the code agree with each other, not that the secret is gone).
5. **Wiring** — ``effective_settings`` fed straight to the scheduler's own
   ``maybe_sweep``/``interval_elapsed`` (next_sweep) and through the real
   worker thread for ``auto_gc`` (immediately), so "the override actually
   takes effect" is demonstrated, not just "the accessor returns the right
   Python value".
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from vault_api import scheduler as scheduler_module
from vault_api import settings_store
from vault_api.config import Settings
from vault_api.db import get_connection, init_db
from vault_api.main import create_app
from vault_api.schedule_window import parse_window

AUTH = {"X-Api-Key": TEST_API_KEY}


def find(body: dict, key: str) -> dict:
    for row in body["settings"]:
        if row["key"] == key:
            return row
    raise AssertionError(f"{key!r} missing from response: {body}")


def keys_of(body: dict) -> set[str]:
    return {row["key"] for row in body["settings"]}


# ==========================================================================
# Auth
# ==========================================================================


def test_get_settings_requires_api_key(client: TestClient) -> None:
    response = client.get("/v1/settings")
    assert response.status_code == 401


def test_patch_settings_requires_api_key(client: TestClient) -> None:
    response = client.patch("/v1/settings", json={"vault_name": "x"})
    assert response.status_code == 401


# ==========================================================================
# GET shape: defaults, env-only rows, the excluded secret
# ==========================================================================


def test_get_reports_defaults_on_a_fresh_install(client: TestClient) -> None:
    response = client.get("/v1/settings", headers=AUTH)
    assert response.status_code == 200
    body = response.json()

    assert body["readonly"] is False

    vault_name = find(body, "vault_name")
    assert vault_name == {
        "key": "vault_name",
        "effective": "",
        "source": "default",
        "fallback": "",
        "applies": "restart-required",
        "env_only": False,
    }

    auto_gc = find(body, "auto_gc")
    assert auto_gc["effective"] == "off"
    assert auto_gc["source"] == "default"
    assert auto_gc["applies"] == "immediately"

    window = find(body, "schedule_window")
    assert window["effective"] is None
    assert window["applies"] == "next_sweep"


def test_get_includes_informational_env_only_rows(client: TestClient) -> None:
    body = client.get("/v1/settings", headers=AUTH).json()

    db_path_row = find(body, "db_path")
    assert db_path_row["env_only"] is True
    assert db_path_row["applies"] == "restart-required"

    settings_readonly_row = find(body, "settings_readonly")
    assert settings_readonly_row["env_only"] is True
    assert settings_readonly_row["effective"] is False


def test_vault_api_key_never_appears_in_the_response(client: TestClient) -> None:
    """Not even redacted — the actual auth secret, never listed at all."""
    response = client.get("/v1/settings", headers=AUTH)
    assert "vault_api_key" not in keys_of(response.json())
    assert TEST_API_KEY not in response.text


# ==========================================================================
# Precedence (ADR-0009 decision 1) — db > env > default
# ==========================================================================


def test_precedence_default_when_nothing_is_set(client: TestClient) -> None:
    body = client.get("/v1/settings", headers=AUTH).json()
    row = find(body, "auto_gc")
    assert row["source"] == "default"
    assert row["effective"] == "off"
    assert row["fallback"] == "off"


def test_precedence_env_wins_over_default(tmp_path: Path) -> None:
    """A base ``Settings`` whose ``auto_gc`` differs from the built-in
    default simulates "the operator set VAULT_AUTO_GC" — no DB override
    exists, so the env value must be reported, not the default.
    """
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        auto_gc="dry-run",
    )
    with TestClient(create_app(settings)) as client:
        body = client.get("/v1/settings", headers=AUTH).json()

    row = find(body, "auto_gc")
    assert row["effective"] == "dry-run"
    assert row["source"] == "env"
    assert row["fallback"] == "dry-run"


def test_precedence_db_wins_over_env(tmp_path: Path) -> None:
    """The named mutation-pinning test (docs/LEARNINGS.md "Testing
    discipline"): base/env says ``dry-run``, a DB override says ``execute``.
    If ``effective_settings``' precedence were ever flipped (env checked
    AFTER db, or the override simply ignored), this assertion would report
    ``dry-run`` and fail — the whole point of pinning db > env here rather
    than only testing db > default or env > default individually.
    """
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        auto_gc="dry-run",
    )
    with TestClient(create_app(settings)) as client:
        patch_response = client.patch(
            "/v1/settings", json={"auto_gc": "execute"}, headers=AUTH
        )
        assert patch_response.status_code == 200
        body = client.get("/v1/settings", headers=AUTH).json()

    row = find(body, "auto_gc")
    assert row["effective"] == "execute"
    assert row["source"] == "db"
    # The fallback is what CLEARING the override reverts to -- the env value
    # (dry-run), never the hardcoded default (off). Getting this backwards
    # would tell an operator the wrong thing about what "revert" does.
    assert row["fallback"] == "dry-run"


# ==========================================================================
# null clears the override (ADR-0009 decision 2)
# ==========================================================================


def test_patch_null_clears_the_override(client: TestClient) -> None:
    set_response = client.patch(
        "/v1/settings", json={"vault_name": "homelab"}, headers=AUTH
    )
    assert set_response.status_code == 200
    assert find(set_response.json(), "vault_name")["effective"] == "homelab"

    clear_response = client.patch(
        "/v1/settings", json={"vault_name": None}, headers=AUTH
    )
    assert clear_response.status_code == 200
    row = find(clear_response.json(), "vault_name")
    assert row["effective"] == ""
    assert row["source"] == "default"

    # The row itself is gone, not merely blanked -- verified at the storage
    # layer directly, one level below the HTTP response.
    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "vault_name") is None
    finally:
        conn.close()


# ==========================================================================
# PATCH validation — one grammar family per key type
# ==========================================================================


@pytest.mark.parametrize(
    "bad",
    ["not-a-window", "25:00-26:00", "09:00-09:00", "24:00-06:00"],
)
def test_patch_bad_schedule_window_is_422_and_not_persisted(
    client: TestClient, bad: str
) -> None:
    response = client.patch(
        "/v1/settings", json={"schedule_window": bad}, headers=AUTH
    )
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "schedule_window") is None
    finally:
        conn.close()


@pytest.mark.parametrize("bad", ["abc", "0", "-3", " 7 ", "1_0", "٧", ""])
def test_patch_bad_schedule_interval_is_422_and_not_persisted(
    client: TestClient, bad: str
) -> None:
    response = client.patch(
        "/v1/settings", json={"schedule_interval_minutes": bad}, headers=AUTH
    )
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "schedule_interval_minutes") is None
    finally:
        conn.close()


@pytest.mark.parametrize("bad", ["exectue", "on", "true", "delete", ""])
def test_patch_bad_auto_gc_is_422_and_not_persisted(
    client: TestClient, bad: str
) -> None:
    response = client.patch("/v1/settings", json={"auto_gc": bad}, headers=AUTH)
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "auto_gc") is None
    finally:
        conn.close()


@pytest.mark.parametrize(
    "bad", ["not a url", "ftp://example.invalid/hook", "example.invalid/hook"]
)
def test_patch_bad_webhook_url_is_422_and_not_persisted(
    client: TestClient, bad: str
) -> None:
    response = client.patch("/v1/settings", json={"webhook_url": bad}, headers=AUTH)
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "webhook_url") is None
    finally:
        conn.close()


def test_patch_webhook_url_blank_is_accepted_and_disables(client: TestClient) -> None:
    """Blank is the documented "off" spelling (mirrors ``VAULT_WEBHOOK_URL``
    at startup) — accepted, not a validation failure."""
    response = client.patch("/v1/settings", json={"webhook_url": ""}, headers=AUTH)
    assert response.status_code == 200
    row = find(response.json(), "webhook_url")
    assert row["effective"] == ""
    assert row["source"] == "db"


def test_patch_webhook_events_accepts_a_json_list(client: TestClient) -> None:
    response = client.patch(
        "/v1/settings",
        json={"webhook_events": ["job.done", "job.error"]},
        headers=AUTH,
    )
    assert response.status_code == 200
    row = find(response.json(), "webhook_events")
    assert row["effective"] == ["job.done", "job.error"]


def test_patch_webhook_events_rejects_an_unknown_name(client: TestClient) -> None:
    response = client.patch(
        "/v1/settings",
        json={"webhook_events": "job.done,job.finished"},
        headers=AUTH,
    )
    assert response.status_code == 422


def test_patch_rejects_a_json_boolean(client: TestClient) -> None:
    """docs/LEARNINGS.md "Parsers": a JSON bool must never be silently
    stringified into "True"/"False"."""
    response = client.patch("/v1/settings", json={"vault_name": True}, headers=AUTH)
    assert response.status_code == 422


def test_apply_updates_is_one_transaction_all_or_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reviewer should-fix S2: ``settings_store.apply_updates`` must persist a
    whole batch as ONE committed transaction, not one commit per key. Proven
    by forcing the SECOND of two writes to raise mid-batch and then re-opening
    a FRESH connection (not the one the transaction ran on) to confirm even
    the FIRST, individually-valid write was rolled back -- the exact failure
    the old per-key-commit loop in ``routers/settings.py`` could not prevent.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        original_write = settings_store._write_override
        calls = {"n": 0}

        def flaky_write(conn_: sqlite3.Connection, key: str, raw_value: str) -> None:
            calls["n"] += 1
            if calls["n"] == 2:
                raise sqlite3.OperationalError("simulated failure on the 2nd key")
            original_write(conn_, key, raw_value)

        monkeypatch.setattr(settings_store, "_write_override", flaky_write)

        with pytest.raises(sqlite3.OperationalError):
            settings_store.apply_updates(
                conn,
                to_set=[("vault_name", "homelab"), ("auto_gc", "execute")],
                to_clear=[],
            )
    finally:
        conn.close()

    fresh_conn = get_connection(db_path)
    try:
        assert settings_store.get_override(fresh_conn, "vault_name") is None
        assert settings_store.get_override(fresh_conn, "auto_gc") is None
    finally:
        fresh_conn.close()


def test_patch_500_on_a_mid_batch_db_error_persists_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same guarantee as the unit test above, exercised through the real
    ``PATCH /v1/settings`` HTTP path (the reviewer's pin explicitly named
    ``routers/settings.py``, not just ``settings_store.py``)."""
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
    )
    app = create_app(settings)
    test_client = TestClient(app, raise_server_exceptions=False)

    original_write = settings_store._write_override
    calls = {"n": 0}

    def flaky_write(conn_: sqlite3.Connection, key: str, raw_value: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("simulated failure on the 2nd key")
        original_write(conn_, key, raw_value)

    monkeypatch.setattr(settings_store, "_write_override", flaky_write)

    with test_client as client:
        response = client.patch(
            "/v1/settings",
            json={"vault_name": "homelab", "auto_gc": "execute"},
            headers=AUTH,
        )
        assert response.status_code == 500

        conn = get_connection(settings.db_path)
        try:
            assert settings_store.get_override(conn, "vault_name") is None
            assert settings_store.get_override(conn, "auto_gc") is None
        finally:
            conn.close()


def test_patch_multi_key_failure_persists_nothing(client: TestClient) -> None:
    """One bad value in a multi-key PATCH must fail the WHOLE request and
    persist NEITHER key -- not just skip the bad one."""
    response = client.patch(
        "/v1/settings",
        json={"vault_name": "homelab", "auto_gc": "bogus-mode"},
        headers=AUTH,
    )
    assert response.status_code == 422

    conn = get_connection(client.app.state.settings.db_path)
    try:
        assert settings_store.get_override(conn, "vault_name") is None
        assert settings_store.get_override(conn, "auto_gc") is None
    finally:
        conn.close()


def test_patch_unknown_key_is_422(client: TestClient) -> None:
    response = client.patch(
        "/v1/settings", json={"totally_bogus_key": "x"}, headers=AUTH
    )
    assert response.status_code == 422
    assert "not a recognised setting" in response.json()["detail"]


@pytest.mark.parametrize(
    "key",
    [
        "vault_api_key",
        "db_path",
        "cache_root",
        "steamprefill_path",
        "steamprefill_cache_dir",
        "manifest_archive_dir",
        "web_dir",
        "settings_readonly",
    ],
)
def test_patch_env_only_key_is_422_with_a_distinct_detail(
    client: TestClient, key: str
) -> None:
    response = client.patch("/v1/settings", json={key: "x"}, headers=AUTH)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "environment-only" in detail
    # The two 422 reasons must be tellable apart by a caller/UI -- an
    # env-only key must NEVER produce the "unknown setting" wording.
    assert "not a recognised setting" not in detail


# ==========================================================================
# Readonly hard-lock (ADR-0009 decision 3)
# ==========================================================================


def test_readonly_blocks_patch_but_not_get(tmp_path: Path) -> None:
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        settings_readonly=True,
    )
    with TestClient(create_app(settings)) as client:
        get_response = client.get("/v1/settings", headers=AUTH)
        assert get_response.status_code == 200
        assert get_response.json()["readonly"] is True

        patch_response = client.patch(
            "/v1/settings", json={"vault_name": "x"}, headers=AUTH
        )
        assert patch_response.status_code == 403
        assert "read-only" in patch_response.json()["detail"]

        # Nothing was written despite the attempt.
        conn = get_connection(settings.db_path)
        try:
            assert settings_store.get_override(conn, "vault_name") is None
        finally:
            conn.close()


def test_readonly_checked_before_body_validation(tmp_path: Path) -> None:
    """403, not 422 -- the lock wins even over a body that would also have
    failed on its own merits."""
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        settings_readonly=True,
    )
    with TestClient(create_app(settings)) as client:
        response = client.patch(
            "/v1/settings", json={"auto_gc": "not-a-real-mode"}, headers=AUTH
        )
    assert response.status_code == 403


# ==========================================================================
# Redaction (ADR-0009 decision 7) — string-literal pins
# ==========================================================================


def test_webhook_url_userinfo_is_redacted_in_the_patch_response(
    client: TestClient,
) -> None:
    secret_url = "https://opsbot:hunter2@example.invalid/hook"
    response = client.patch(
        "/v1/settings", json={"webhook_url": secret_url}, headers=AUTH
    )
    assert response.status_code == 200

    # STRING-LITERAL pin (docs/LEARNINGS.md): the secret must be provably
    # absent from the raw text, not merely "different from what redact_url
    # would produce if the test called it again".
    assert "hunter2" not in response.text
    assert "opsbot" not in response.text
    row = find(response.json(), "webhook_url")
    assert row["effective"] == "https://***@example.invalid/hook"


def test_webhook_url_userinfo_is_redacted_in_get_after_patch(
    client: TestClient,
) -> None:
    client.patch(
        "/v1/settings",
        json={"webhook_url": "https://opsbot:hunter2@example.invalid/hook"},
        headers=AUTH,
    )
    response = client.get("/v1/settings", headers=AUTH)
    assert "hunter2" not in response.text


def test_webhook_url_userinfo_is_redacted_in_the_fallback_field(
    tmp_path: Path,
) -> None:
    """The FALLBACK value (what clearing the override reverts to) must be
    redacted too, not only the effective one -- it carries the same
    credential when the env-configured URL itself has userinfo."""
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        webhook_url="https://envuser:envsecret@example.invalid/hook",
    )
    with TestClient(create_app(settings)) as client:
        response = client.get("/v1/settings", headers=AUTH)

    assert "envsecret" not in response.text
    row = find(response.json(), "webhook_url")
    assert row["fallback"] == "https://***@example.invalid/hook"


def test_effective_settings_precedence_directly(tmp_path: Path) -> None:
    """Unit-level pin on ``settings_store.effective_settings`` itself — the
    function every LIVE consumer (scheduler tick, worker job-finish) actually
    calls — separate from the ``describe_settings``/GET-response precedence
    tests above, which exercise a DIFFERENT code path
    (``routers/settings.py`` -> ``describe_settings``) that happens to
    duplicate the same db/env/default decision for the read model. A
    precedence bug in ONE of the two would not necessarily show up in the
    other, so both get their own direct pin.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        base = Settings(
            vault_api_key=TEST_API_KEY,
            db_path=db_path,
            cache_root=str(tmp_path / "cache"),
            log_level="INFO",
            auto_gc="dry-run",  # simulates VAULT_AUTO_GC=dry-run
        )

        # No override yet: env value wins over the built-in default.
        assert settings_store.effective_settings(conn, base).auto_gc == "dry-run"

        # An override now must win over that env value.
        settings_store.set_override(conn, "auto_gc", "execute")
        assert settings_store.effective_settings(conn, base).auto_gc == "execute"

        # Clearing it must revert to the env value, not the default.
        settings_store.delete_override(conn, "auto_gc")
        assert settings_store.effective_settings(conn, base).auto_gc == "dry-run"
    finally:
        conn.close()


def test_b1_scheduler_thread_exists_on_a_bare_boot_and_a_patched_window_sweeps(
    tmp_path: Path,
) -> None:
    """Reviewer blocker B1, required pin: boot with NO ``VAULT_SCHEDULE_WINDOW``
    and NO event log (the stock/default deployment), then ``PATCH`` a window
    in and prove a REAL sweep runs — end to end through the real FastAPI
    lifespan, not by calling ``scheduler_module.maybe_sweep`` directly.

    Before the fix, ``main.py`` only called ``scheduler.start()`` when
    ``scheduler.thread_needed`` was true at BOOT. On this exact boot
    configuration that property is ``False`` (no window, no event log), so
    the thread never existed and no ``PATCH`` could ever make a sweep run —
    ``GET /v1/schedule`` would report a ``next_eligible_at`` that could never
    arrive. This test fails against that old gate and passes now that
    ``main.py`` starts the thread unconditionally.
    """
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
        log_level="INFO",
        # Deliberately bare: this is the exact boot shape the blocker named.
    )
    app = create_app(settings)
    # Fast tick so the test does not have to wait a real minute for the first
    # one; everything else (the real lifespan, the real thread, the real
    # scheduler/settings-store code) is exercised unmodified.
    app.state.scheduler = scheduler_module.PrefillScheduler(settings, tick_seconds=0.05)

    with TestClient(app) as client:
        thread = client.app.state.scheduler._thread
        assert thread is not None and thread.is_alive(), (
            "the scheduler thread must exist even though nothing was "
            "configured at boot -- PATCH /v1/settings needs it to tick"
        )

        patch_response = client.patch(
            "/v1/settings",
            json={"schedule_window": "00:00-24:00"},
            headers=AUTH,
        )
        assert patch_response.status_code == 200
        assert find(patch_response.json(), "schedule_window")["effective"] == (
            "00:00-24:00"
        )

        deadline = time.monotonic() + 10.0
        last_sweep_at = None
        schedule_body: dict = {}
        while time.monotonic() < deadline:
            schedule_body = client.get("/v1/schedule", headers=AUTH).json()
            if schedule_body.get("enabled") and schedule_body.get("last_sweep_at"):
                last_sweep_at = schedule_body["last_sweep_at"]
                break
            time.sleep(0.05)

        assert last_sweep_at is not None, (
            f"a sweep never ran after PATCHing schedule_window: {schedule_body}"
        )
        assert schedule_body["enabled"] is True


# ==========================================================================
# Wiring: the override actually takes effect
# ==========================================================================


def test_effective_settings_ignores_a_corrupt_stored_override(tmp_path: Path) -> None:
    """A row written outside the validated PATCH path (the documented sqlite3
    escape hatch, or simply an older/incompatible value) must not crash a
    live request or the scheduler tick -- it is logged and treated as if the
    key had no override at all.
    """
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
            ("auto_gc", "not-a-real-mode", "2026-08-10T00:00:00Z"),
        )
        conn.commit()

        base = Settings(
            vault_api_key=TEST_API_KEY,
            db_path=db_path,
            cache_root=str(tmp_path / "cache"),
            log_level="INFO",
        )
        effective = settings_store.effective_settings(conn, base)
    finally:
        conn.close()

    assert effective.auto_gc == "off"  # fell back to the built-in default


def test_scheduler_tick_picks_up_an_interval_override_next_sweep(
    tmp_path: Path,
) -> None:
    """Unit-level proof of the "next_sweep" apply semantics documented in
    ``vault_api/settings_store.py``: a sweep claimed under the ORIGINAL
    180-minute interval is not due again a minute later, but IS due once a
    DB override shortens the interval -- exactly what
    ``vault_api/scheduler.py``'s tick loop now resolves every tick via
    ``effective_settings``.
    """
    from datetime import timedelta, timezone

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        base = Settings(
            vault_api_key=TEST_API_KEY,
            db_path=db_path,
            cache_root=str(tmp_path / "cache"),
            log_level="INFO",
            schedule_window=parse_window("00:00-24:00"),
            schedule_interval_minutes=180,
        )
        now = scheduler_module.local_now().astimezone(timezone.utc)

        first = scheduler_module.maybe_sweep(conn, base, now)
        assert first.swept is True

        one_minute_later = now + timedelta(minutes=1)
        still_base = scheduler_module.maybe_sweep(conn, base, one_minute_later)
        assert still_base.skipped_reason == "interval-not-elapsed"

        settings_store.set_override(conn, "schedule_interval_minutes", "1")
        effective = settings_store.effective_settings(conn, base)
        assert effective.schedule_interval_minutes == 1

        two_minutes_later = now + timedelta(minutes=2)
        overridden = scheduler_module.maybe_sweep(conn, effective, two_minutes_later)
        assert overridden.swept is True
    finally:
        conn.close()


def test_worker_auto_gc_override_applies_to_the_next_completed_job(
    tmp_path: Path,
) -> None:
    """End-to-end through the REAL worker thread (not a unit call): base
    ``Settings`` says ``VAULT_AUTO_GC=off``, a ``PATCH`` overrides it to
    ``execute`` BEFORE the job is enqueued, and the prefill the worker
    actually runs must queue an executing GC job -- proving the
    ``settings_store.effective_settings`` call inside
    ``worker._maybe_queue_auto_gc`` is real, not just unit-testable.
    """
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir,
        mode="success",
        cache_root=str(cache_root),
        depots_by_app={440: [441]},
        summary_text=(
            "  Prefilled 1 apps totaling 12 MiB in 05.0000 \n"
            "   Updated | Up To Date\n"
            "  ---------+------------\n"
            "      1    |     0\n"
        ),
    )
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
        steamprefill_path=executable,
        prefill_timeout_seconds=60,
        worker_poll_seconds=0.02,
        steamprefill_cache_dir=str(tmp_path / "unused-steamprefill-cache"),
        manifest_archive_dir=str(tmp_path / "manifest-archive"),
        auto_gc="off",
    )
    with TestClient(create_app(settings)) as client:
        patch_response = client.patch(
            "/v1/settings", json={"auto_gc": "execute"}, headers=AUTH
        )
        assert patch_response.status_code == 200

        enqueue_response = client.post(
            "/v1/prefill", json={"appids": [440]}, headers=AUTH
        )
        assert enqueue_response.status_code == 202
        job_id = int(enqueue_response.json()[0]["job_id"])

        deadline = time.monotonic() + 30.0
        job: dict = {}
        while time.monotonic() < deadline:
            job = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
            if job["status"] in ("done", "error", "cancelled"):
                break
            time.sleep(0.05)
        assert job.get("status") == "done", job

        conn = get_connection(settings.db_path)
        try:
            gc_rows = conn.execute(
                "SELECT gc_execute FROM jobs WHERE appid = 440 AND type = 'gc'"
            ).fetchall()
        finally:
            conn.close()

    assert len(gc_rows) == 1
    assert gc_rows[0]["gc_execute"] == 1
