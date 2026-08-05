"""DELETE /v1/cache/{appid} — per-game deletion with shared-depot protection
(plan §4/§6, WP 1.6).

Split in three, deliberately: the **path guards** and the **plan** are unit
tests over pure functions (no HTTP, no database, no filesystem where possible),
the **link/removal mechanics** are tested against real symlinks/junctions on the
real filesystem, and the **endpoint** is tested end to end through HTTP. Nothing
here mocks the filesystem: this code deletes user data, so the tests exercise
what actually happens on disk.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY, make_dir_link, make_junction
from vault_api import deletion
from vault_api.config import Settings
from vault_api.deletion import (
    DeletedDepot,
    FailedDepot,
    SharedDepot,
    UnsafeCacheRootError,
    UnsafeDepotTargetError,
    coerce_positive_id,
    delete_app_depots,
    depot_dir_bytes,
    depot_dir_path,
    is_filesystem_root,
    plan_deletion,
    remove_depot_dir,
    resolve_depot_root,
)
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _write(path: Path, content: bytes = b"x") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _seed_depot(cache_root: Path, depotid: int, size: int) -> Path:
    """One depot directory holding a single chunk file of ``size`` bytes."""
    depot = cache_root / "depot" / str(depotid)
    _write(depot / "chunk" / "aa", b"1" * size)
    return depot


def _make_app(
    tmp_path: Path, *, cache_root: str | None = None
) -> tuple[TestClient, Path, Settings]:
    """A fresh app whose VAULT_CACHE_ROOT is a real directory under tmp_path.

    Not the shared ``client`` fixture: every test here cares about the exact
    cache root on disk. No lifespan is started, so no prefill worker runs and
    queued jobs stay queued (which is what the 409 test needs).

    ``cache_root`` is passed through **verbatim** (as a ``str``, never via
    ``Path``) so the guard tests can hand the app a literally empty value —
    ``Path("")`` silently becomes ``"."``, which would test a different thing.
    """
    root = str(tmp_path / "cache") if cache_root is None else cache_root
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=root,
        log_level="INFO",
    )
    return TestClient(create_app(settings)), Path(root or "."), settings


def _seed_mapping(client: TestClient, depotid: int, appid: int, name: str | None = None) -> None:
    response = client.put(
        f"/v1/mapping/{depotid}", json={"appid": appid, "app_name": name}, headers=AUTH
    )
    assert response.status_code == 200, response.text


def _app_row(settings: Settings, appid: int) -> sqlite3.Row:
    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT appid, status, last_prefill_at FROM apps WHERE appid = ?", (appid,)
        ).fetchone()
    finally:
        conn.close()


def _set_app_prefilled(settings: Settings, appid: int) -> None:
    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute(
            "UPDATE apps SET status = 'done', last_prefill_at = ? WHERE appid = ?",
            ("2026-08-05T10:00:00Z", appid),
        )
        conn.commit()
    finally:
        conn.close()


# --------------------------------------------------------------------------
# path guards (pure functions)
# --------------------------------------------------------------------------


def test_is_filesystem_root_recognises_roots_and_normal_dirs() -> None:
    assert is_filesystem_root(os.path.realpath(os.sep)) is True
    assert is_filesystem_root(os.path.abspath(os.sep)) is True
    # A UNC share root has no parent either (Windows semantics; the check is
    # pure ntpath/posixpath string work, so it is meaningful on both).
    if os.name == "nt":
        assert is_filesystem_root("\\\\server\\share") is True
        assert is_filesystem_root("\\\\server\\share\\sub") is False
    assert is_filesystem_root(os.path.join(os.path.abspath(os.sep), "cache")) is False


def test_resolve_depot_root_refuses_an_empty_cache_root() -> None:
    # abspath("") is the current working directory — an unset VAULT_CACHE_ROOT
    # must never turn into "rm -rf $(pwd)/depot".
    for value in ["", "   ", None]:  # type: ignore[list-item]
        with pytest.raises(UnsafeCacheRootError) as excinfo:
            resolve_depot_root(value)  # type: ignore[arg-type]
        assert "empty" in str(excinfo.value).lower()


def test_resolve_depot_root_refuses_a_filesystem_root() -> None:
    # On Windows realpath("/") is "C:\\" — a Docker-style path pasted onto a
    # Windows host lands exactly here.
    for value in ["/", os.path.abspath(os.sep)]:
        with pytest.raises(UnsafeCacheRootError) as excinfo:
            resolve_depot_root(value)
        assert "filesystem root" in str(excinfo.value)


def test_resolve_depot_root_refuses_a_cache_root_without_a_depot_dir(
    tmp_path: Path,
) -> None:
    (tmp_path / "cache").mkdir()
    with pytest.raises(UnsafeCacheRootError) as excinfo:
        resolve_depot_root(str(tmp_path / "cache"))
    assert "depot" in str(excinfo.value)

    # ...and refuses a depot/ that is a FILE rather than a directory.
    _write(tmp_path / "cache" / "depot", b"not a directory")
    with pytest.raises(UnsafeCacheRootError):
        resolve_depot_root(str(tmp_path / "cache"))


def test_resolve_depot_root_refuses_an_unc_style_path() -> None:
    # No such share exists, so this must refuse rather than "succeed" against
    # something unresolvable (the concrete refusal reason differs per platform:
    # missing depot/ dir on Windows, missing path on POSIX).
    with pytest.raises(UnsafeCacheRootError):
        resolve_depot_root("\\\\no-such-server\\no-such-share")


def test_resolve_depot_root_accepts_a_relative_cache_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The default VAULT_CACHE_ROOT ("./cache") is relative, so this is the
    # normal case, not an edge case.
    (tmp_path / "cache" / "depot").mkdir(parents=True)
    monkeypatch.chdir(tmp_path)

    assert resolve_depot_root("cache") == os.path.realpath(
        str(tmp_path / "cache" / "depot")
    )


def test_resolve_depot_root_resolves_a_linked_cache_root(tmp_path: Path) -> None:
    # Putting the cache on another volume via a link is a legitimate homelab
    # setup: the RESOLVED directory becomes the deletion base, which is what
    # makes depot_dir_path's strict-child check meaningful.
    real = tmp_path / "real-cache"
    (real / "depot").mkdir(parents=True)
    make_dir_link(tmp_path / "linked-cache", real)

    assert resolve_depot_root(str(tmp_path / "linked-cache")) == os.path.realpath(
        str(real / "depot")
    )


@pytest.mark.parametrize(
    "poisoned",
    [
        "../../etc",
        "441/../..",
        "..",
        ".",
        "",
        "   ",
        "0",
        "-1",
        0,
        -5,
        None,
        True,
        1.5,
        "441; rm -rf /",
        "0x1c1",
        "C:\\Windows",
    ],
)
def test_depot_dir_path_refuses_anything_that_is_not_a_positive_int(
    tmp_path: Path, poisoned: object
) -> None:
    """SQLite's INTEGER affinity does not enforce integers, so a poisoned or
    hand-edited depot_app_map row really can contain these."""
    depot_root = os.path.realpath(str(tmp_path))
    with pytest.raises(UnsafeDepotTargetError):
        depot_dir_path(depot_root, poisoned)


def test_depot_dir_path_builds_a_direct_child_from_the_integer_only(
    tmp_path: Path,
) -> None:
    depot_root = os.path.realpath(str(tmp_path))
    assert depot_dir_path(depot_root, 441) == os.path.join(depot_root, "441")
    # A plain numeric string from the DB is accepted (INTEGER affinity normally
    # stores an int, but a TEXT row is possible).
    assert depot_dir_path(depot_root, "441") == os.path.join(depot_root, "441")


def test_coerce_positive_id_rejects_bool_and_non_positive() -> None:
    assert coerce_positive_id(441) == 441
    assert coerce_positive_id("441") == 441
    # True == 1 in Python; without the explicit bool check it would become
    # "delete depot 1".
    assert coerce_positive_id(True) is None
    assert coerce_positive_id(0) is None
    assert coerce_positive_id(-1) is None
    assert coerce_positive_id(None) is None
    assert coerce_positive_id(b"441") is None


def test_coerce_positive_id_accepts_only_exact_ascii_digit_strings() -> None:
    """WP 1.6 review nitpick: exactness beats what ``int()`` happens to accept.

    All three of these parse fine with ``int()`` but are not depot ids vault-api
    ever writes — on the path that decides which directory gets destroyed, a
    value this odd means the row is wrong and the operator should hear about it
    rather than have it silently normalised.
    """
    assert int(" 441 ") == 441 and coerce_positive_id(" 441 ") is None
    assert int("1_0") == 10 and coerce_positive_id("1_0") is None
    assert int("٤٤١") == 441  # Arabic-Indic digits
    assert coerce_positive_id("٤٤١") is None
    assert coerce_positive_id("+441") is None
    # isdigit() alone would accept a superscript; isascii() rules it out.
    assert "²".isdigit() and coerce_positive_id("²") is None


def test_plan_deletion_rejects_a_non_exact_depotid_string() -> None:
    # Same rule inside the plan, so a weird row is reported rather than
    # normalised into a deletion target.
    plan = plan_deletion([(441, 440), (" 442 ", 440)], appid=440)

    assert plan.exclusive == [441]
    assert len(plan.unusable) == 1
    assert "' 442 '" in plan.unusable[0].error


# --------------------------------------------------------------------------
# the plan: exclusive vs shared (plan §4)
# --------------------------------------------------------------------------


def test_plan_deletion_splits_exclusive_and_shared() -> None:
    rows = [
        (441, 440),  # only 440 -> exclusive
        (442, 440),  # only 440 -> exclusive
        (900, 440),  # also 730 and 570 -> shared, kept
        (900, 730),
        (900, 570),
    ]
    plan = plan_deletion(rows, appid=440)

    assert plan.exclusive == [441, 442]
    assert [(s.depotid, s.shared_with) for s in plan.shared] == [(900, [570, 730])]
    assert plan.unusable == []


def test_plan_deletion_is_symmetric_for_the_other_app() -> None:
    rows = [(441, 440), (900, 440), (900, 730)]
    plan = plan_deletion(rows, appid=730)

    assert plan.exclusive == []
    assert [(s.depotid, s.shared_with) for s in plan.shared] == [(900, [440])]


def test_plan_deletion_reports_an_unusable_depotid_row_instead_of_deleting() -> None:
    plan = plan_deletion([(441, 440), ("../../etc", 440)], appid=440)

    assert plan.exclusive == [441]
    assert len(plan.unusable) == 1
    assert plan.unusable[0].depotid == 0
    assert "../../etc" in plan.unusable[0].error


def test_plan_deletion_treats_an_unreadable_co_owner_as_shared() -> None:
    # Conservative direction: something else references this depot, and the
    # reference is unreadable -> never delete it.
    plan = plan_deletion([(900, 440), (900, None)], appid=440)

    assert plan.exclusive == []
    assert [(s.depotid, s.shared_with) for s in plan.shared] == [(900, [])]


# --------------------------------------------------------------------------
# link safety on the real filesystem
# --------------------------------------------------------------------------


def test_shutil_rmtree_refuses_a_linked_dir_which_is_why_we_unlink_it(
    tmp_path: Path,
) -> None:
    """Pins the measured platform behavior ``remove_depot_dir`` is built on.

    Windows 11 / CPython 3.12.10, junction created with ``mklink /J``:
    ``shutil.rmtree(<junction>)`` raises
    ``OSError("Cannot call rmtree on a symbolic link")`` and the junction's
    target survives. Same refusal for a POSIX/Windows directory symlink. So
    rmtree does not destroy foreign data — but it cannot delete a legitimately
    linked depot directory either, which is why ``remove_depot_dir`` handles
    the link case itself.
    """
    outside = tmp_path / "outside"
    _write(outside / "precious.bin", b"keep me")
    link = tmp_path / "441"
    make_dir_link(link, outside)

    with pytest.raises(OSError):
        shutil.rmtree(link)

    assert (outside / "precious.bin").read_bytes() == b"keep me"


def test_remove_depot_dir_unlinks_a_linked_depot_and_spares_the_target(
    tmp_path: Path,
) -> None:
    outside = tmp_path / "outside"
    _write(outside / "precious.bin", b"keep me")
    link = tmp_path / "depot" / "441"
    link.parent.mkdir(parents=True)
    kind = make_dir_link(link, outside)

    remove_depot_dir(str(link))

    assert not os.path.lexists(link), f"the {kind} itself must be gone"
    assert (outside / "precious.bin").read_bytes() == b"keep me"


def test_remove_depot_dir_does_not_follow_a_link_nested_in_the_tree(
    tmp_path: Path,
) -> None:
    """A link *inside* a depot tree must be removed as a link, not followed.

    Measured: ``shutil.rmtree`` on a real directory containing a junction
    completes, removes the junction as a link, and leaves the target intact
    (Windows 11 / CPython 3.12.10). This test is the regression guard — if a
    future CPython followed nested junctions, it would fail here instead of
    eating a user's data.
    """
    outside = tmp_path / "outside"
    _write(outside / "precious.bin", b"keep me")
    depot = tmp_path / "depot" / "441"
    _write(depot / "chunk" / "aa", b"1" * 10)
    make_dir_link(depot / "chunk" / "sneaky", outside)

    remove_depot_dir(str(depot))

    assert not os.path.lexists(depot)
    assert (outside / "precious.bin").read_bytes() == b"keep me"


def test_depot_dir_bytes_counts_real_files_but_not_a_link_target(
    tmp_path: Path,
) -> None:
    real = tmp_path / "depot" / "441"
    _write(real / "chunk" / "aa", b"1" * 12)
    assert depot_dir_bytes(str(real)) == 12

    outside = tmp_path / "outside"
    _write(outside / "big.bin", b"1" * 5000)
    link = tmp_path / "depot" / "442"
    make_dir_link(link, outside)
    # Only the link is removed, so no target byte is freed — reporting 5000
    # freed bytes here would be a lie.
    assert depot_dir_bytes(str(link)) == 0


def test_delete_app_depots_reports_an_absent_depot_as_deleted_with_zero_bytes(
    tmp_path: Path,
) -> None:
    depot_root = tmp_path / "depot"
    depot_root.mkdir()

    # Explicit no-op recheck: co_owners is a required keyword argument, so a
    # caller that means "no shared-depot recheck here" has to say so.
    deleted, failed, late_shared = delete_app_depots(
        str(depot_root), 440, [441], co_owners=lambda _depotid: []
    )

    assert deleted == [DeletedDepot(depotid=441, size_bytes_freed=0)]
    assert failed == []
    assert late_shared == []


def test_delete_app_depots_isolates_a_path_guard_failure(tmp_path: Path) -> None:
    depot_root = tmp_path / "depot"
    depot_root.mkdir()
    _write(depot_root / "441" / "chunk" / "aa", b"1" * 7)

    deleted, failed, _late = delete_app_depots(
        str(depot_root), 440, ["../../etc", 441], co_owners=lambda _depotid: []
    )

    assert deleted == [DeletedDepot(depotid=441, size_bytes_freed=7)]
    assert len(failed) == 1
    assert isinstance(failed[0], FailedDepot)
    assert not (depot_root / "441").exists()


def test_delete_app_depots_keeps_a_depot_that_became_shared_at_recheck_time(
    tmp_path: Path,
) -> None:
    depot_root = tmp_path / "depot"
    depot_root.mkdir()
    _write(depot_root / "441" / "chunk" / "aa", b"1" * 7)
    _write(depot_root / "442" / "chunk" / "aa", b"1" * 9)

    deleted, failed, late_shared = delete_app_depots(
        str(depot_root), 440, [441, 442], co_owners=lambda d: [730] if d == 441 else []
    )

    assert late_shared == [SharedDepot(depotid=441, shared_with=[730])]
    assert deleted == [DeletedDepot(depotid=442, size_bytes_freed=9)]
    assert failed == []
    assert (depot_root / "441").exists(), "a newly-shared depot was deleted"


def test_delete_app_depots_never_deletes_when_the_recheck_itself_fails(
    tmp_path: Path,
) -> None:
    """"Unknown ownership" must resolve to "keep", not to "delete"."""
    depot_root = tmp_path / "depot"
    depot_root.mkdir()
    _write(depot_root / "441" / "chunk" / "aa", b"1" * 7)

    def broken(depotid: int) -> list[int]:
        raise sqlite3.OperationalError("database is locked")

    deleted, failed, late_shared = delete_app_depots(
        str(depot_root), 440, [441], co_owners=broken
    )

    assert deleted == []
    assert late_shared == []
    assert len(failed) == 1 and failed[0].depotid == 441
    assert "re-check" in failed[0].error
    assert (depot_root / "441").exists()


def test_load_co_owners_returns_only_the_other_apps(tmp_path: Path) -> None:
    from vault_api.db import get_connection, init_db
    from vault_api.mapping import upsert_mapping

    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    conn = get_connection(db_path)
    try:
        upsert_mapping(conn, depotid=900, appid=440, name=None)
        upsert_mapping(conn, depotid=900, appid=730, name=None)
        upsert_mapping(conn, depotid=900, appid=570, name=None)
        upsert_mapping(conn, depotid=441, appid=440, name=None)

        assert deletion.load_co_owners(conn, 900, 440) == [570, 730]
        assert deletion.load_co_owners(conn, 441, 440) == []
    finally:
        conn.close()


# --------------------------------------------------------------------------
# the endpoint
# --------------------------------------------------------------------------


def test_delete_without_key_is_rejected(client: TestClient) -> None:
    response = client.delete("/v1/cache/440")
    assert response.status_code == 401


def test_delete_unknown_appid_is_404(client: TestClient) -> None:
    response = client.delete("/v1/cache/999999", headers=AUTH)
    assert response.status_code == 404
    assert "999999" in response.json()["detail"]


def test_delete_non_positive_appid_is_422(client: TestClient) -> None:
    assert client.delete("/v1/cache/0", headers=AUTH).status_code == 422
    assert client.delete("/v1/cache/-3", headers=AUTH).status_code == 422


def test_delete_app_without_any_mapping_is_404(client: TestClient) -> None:
    # POST /v1/prefill creates the apps row without any mapping.
    assert client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH).status_code == 202

    response = client.delete("/v1/cache/440", headers=AUTH)
    assert response.status_code == 404
    assert "no depot mappings" in response.json()["detail"]


def test_delete_removes_exclusive_depots_and_reports_freed_bytes(tmp_path: Path) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, name="Team Fortress 2")
    _seed_mapping(client, depotid=442, appid=440)
    depot441 = _seed_depot(cache_root, 441, 10)
    depot442 = _seed_depot(cache_root, 442, 25)
    _seed_depot(cache_root, 555, 99)  # unrelated, unmapped -> must survive

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body == {
        "appid": 440,
        "deleted_depots": [
            {"depotid": 441, "size_bytes_freed": 10},
            {"depotid": 442, "size_bytes_freed": 25},
        ],
        "skipped_shared": [],
        "failed": [],
        "total_bytes_freed": 35,
    }
    assert not depot441.exists()
    assert not depot442.exists()
    assert (cache_root / "depot" / "555").exists(), "an unrelated depot was deleted"
    assert (cache_root / "depot").is_dir(), "the depot root itself must survive"


def test_delete_keeps_a_shared_depot_and_reports_the_other_appids(
    tmp_path: Path,
) -> None:
    """Plan §4: "2 depots shared with game Y, not deleted" — in both directions."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, name="Team Fortress 2")
    _seed_mapping(client, depotid=900, appid=440)
    _seed_mapping(client, depotid=900, appid=730, name="Counter-Strike 2")
    _seed_mapping(client, depotid=900, appid=570, name="Dota 2")
    _seed_depot(cache_root, 441, 10)
    _seed_depot(cache_root, 900, 50)

    first = client.delete("/v1/cache/440", headers=AUTH).json()
    assert first["deleted_depots"] == [{"depotid": 441, "size_bytes_freed": 10}]
    assert first["skipped_shared"] == [{"depotid": 900, "shared_with": [570, 730]}]
    assert first["total_bytes_freed"] == 10
    assert (cache_root / "depot" / "900").exists(), "shared depot was deleted!"

    # Other direction: 730 still shares 900 with 440 and 570 (the mapping rows
    # of the just-deleted app are kept on purpose), so it is protected again.
    second = client.delete("/v1/cache/730", headers=AUTH).json()
    assert second["deleted_depots"] == []
    assert second["skipped_shared"] == [{"depotid": 900, "shared_with": [440, 570]}]
    assert second["total_bytes_freed"] == 0
    assert (cache_root / "depot" / "900" / "chunk" / "aa").read_bytes() == b"1" * 50


def test_delete_is_refused_with_409_while_a_prefill_job_is_queued(
    tmp_path: Path,
) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)

    assert client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH).status_code == 202

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "queued" in detail
    assert (cache_root / "depot" / "441").exists(), "409 must not delete anything"

    # A job for a DIFFERENT app does not block this one.
    _seed_mapping(client, depotid=800, appid=570)
    _seed_depot(cache_root, 800, 5)
    assert client.delete("/v1/cache/570", headers=AUTH).status_code == 200


def test_delete_resets_status_to_idle_and_clears_last_prefill_at(
    tmp_path: Path,
) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)
    _set_app_prefilled(settings, 440)
    assert _app_row(settings, 440)["status"] == "done"

    assert client.delete("/v1/cache/440", headers=AUTH).status_code == 200

    row = _app_row(settings, 440)
    assert row["status"] == "idle"
    assert row["last_prefill_at"] is None


def test_delete_keeps_the_mapping_rows(tmp_path: Path) -> None:
    """Documented decision: the mapping is knowledge, not cache state."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440, name="Team Fortress 2")
    _seed_depot(cache_root, 441, 10)

    assert client.delete("/v1/cache/440", headers=AUTH).status_code == 200

    assert client.get("/v1/mapping", headers=AUTH).json() == [
        {"depotid": 441, "appid": 440}
    ]
    detail = client.get("/v1/games/440", headers=AUTH).json()
    assert detail["depots"] == [{"depotid": 441, "shared": False, "size_bytes": None}]
    assert detail["size_bytes"] is None
    assert detail["name"] == "Team Fortress 2"


def test_delete_invalidates_the_size_cache_immediately(tmp_path: Path) -> None:
    """The DEFAULT 60s TTL is used on purpose: only an explicit
    ``SizeCache.invalidate()`` can make the size drop inside the test's runtime.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)

    assert settings.size_cache_ttl_seconds == 60.0
    before = client.get("/v1/games", headers=AUTH).json()
    assert before[0]["size_bytes"] == 10
    assert client.get("/v1/cache/summary", headers=AUTH).json()["total_bytes"] == 10

    assert client.delete("/v1/cache/440", headers=AUTH).status_code == 200

    after = client.get("/v1/games", headers=AUTH).json()
    assert after[0]["size_bytes"] is None, "stale pre-deletion size served"
    assert after[0]["depot_count"] == 1, "the mapping row must still be there"
    assert client.get("/v1/cache/summary", headers=AUTH).json()["total_bytes"] == 0


def test_deleting_twice_is_a_clean_no_op(tmp_path: Path) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)

    first = client.delete("/v1/cache/440", headers=AUTH).json()
    assert first["total_bytes_freed"] == 10

    second = client.delete("/v1/cache/440", headers=AUTH)
    assert second.status_code == 200
    body = second.json()
    # The mapping row is kept, so the second call still targets depot 441 —
    # it is simply already gone: reported as deleted with 0 bytes freed, no
    # error, no exception.
    assert body["deleted_depots"] == [{"depotid": 441, "size_bytes_freed": 0}]
    assert body["failed"] == []
    assert body["total_bytes_freed"] == 0


def test_two_racing_deletes_do_not_5xx_or_double_delete(tmp_path: Path) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_mapping(client, depotid=442, appid=440)
    _seed_depot(cache_root, 441, 10)
    _seed_depot(cache_root, 442, 25)
    app = client.app

    async def run() -> list[httpx.Response]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as async_client:
            return await asyncio.gather(
                *[async_client.delete("/v1/cache/440", headers=AUTH) for _ in range(4)]
            )

    responses = asyncio.run(run())

    assert [r.status_code for r in responses] == [200, 200, 200, 200]
    # Whoever got there first freed the bytes; the losers report a clean empty
    # result. In total each depot's bytes are freed exactly once.
    assert sum(r.json()["total_bytes_freed"] for r in responses) == 35
    for response in responses:
        assert response.json()["failed"] == []
    assert not (cache_root / "depot" / "441").exists()
    assert not (cache_root / "depot" / "442").exists()


def test_delete_reports_a_partial_failure_without_claiming_success(
    tmp_path: Path,
) -> None:
    """One depot is made undeletable; the other must still be deleted and the
    failure must be reported per depot (never a half-reported success).

    Windows: an open file handle inside the depot makes removal fail with a
    sharing violation. POSIX: a read-only+execute directory mode makes the
    unlink inside it fail (skipped as root, where mode bits don't apply).
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_mapping(client, depotid=442, appid=440)
    _seed_depot(cache_root, 441, 10)
    depot442 = _seed_depot(cache_root, 442, 25)

    if os.name == "nt":
        blocker = open(depot442 / "chunk" / "aa", "rb")
        restore = None
    else:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("running as root: directory mode bits do not block unlink")
        blocker = None
        chunk_dir = depot442 / "chunk"
        os.chmod(chunk_dir, 0o500)
        restore = chunk_dir

    try:
        response = client.delete("/v1/cache/440", headers=AUTH)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["deleted_depots"] == [{"depotid": 441, "size_bytes_freed": 10}]
        assert body["total_bytes_freed"] == 10, "failed depot must not be counted"
        assert len(body["failed"]) == 1
        assert body["failed"][0]["depotid"] == 442
        assert body["failed"][0]["error"]
        assert depot442.exists(), "the failing depot must still be reported as present"
    finally:
        if blocker is not None:
            blocker.close()
        if restore is not None:
            os.chmod(restore, 0o700)


def test_a_partial_failure_leaves_the_app_in_error_not_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP 1.6 review blocker: "failed" does not mean "untouched".

    ``shutil.rmtree`` deletes files as it walks and only then raises, so a
    failed depot is typically *half* deleted. Leaving the app at ``done`` with
    its ``last_prefill_at`` intact would put a green badge on a half-destroyed
    game, and Phase 3's staleness check (manifest ids, not file counts) would
    never re-fill it. Any failure therefore ends at ``status='error'`` with
    ``last_prefill_at`` cleared.

    The block is applied to a file in the MIDDLE of the only mapped depot, so
    rmtree really does remove part of the tree before failing.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    depot = cache_root / "depot" / "441"
    for name in ["aa", "bb", "cc", "dd", "ee"]:
        _write(depot / "chunk" / name, b"1" * 100)
    _set_app_prefilled(settings, 440)
    assert _app_row(settings, 440)["status"] == "done"

    if os.name == "nt":
        blocker = open(depot / "chunk" / "cc", "rb")
        restore = None
    else:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            pytest.skip("running as root: directory mode bits do not block unlink")
        blocker = None
        restore = depot / "chunk"
        os.chmod(restore, 0o500)

    try:
        response = client.delete("/v1/cache/440", headers=AUTH)
        assert response.status_code == 200, response.text
        body = response.json()
        assert len(body["failed"]) == 1 and body["failed"][0]["depotid"] == 441
        assert body["total_bytes_freed"] == 0

        row = _app_row(settings, 440)
        assert row["status"] == "error", "half-deleted game must not stay 'done'"
        assert row["last_prefill_at"] is None
    finally:
        if blocker is not None:
            blocker.close()
        if restore is not None:
            os.chmod(restore, 0o700)


def test_a_depot_that_becomes_shared_between_plan_and_removal_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """WP 1.6 review should-fix: the shared-depot TOCTOU window.

    The interleaving under test: ``plan_deletion`` classifies depot 900 as
    exclusive, and *then* — before the directory is removed — another app maps
    it (a `PUT /v1/mapping/900` in the real world). Without the execute-time
    recheck, deletion would proceed on the stale plan and destroy content that
    now belongs to a second game, breaking plan §4's guarantee.

    The mapping write is injected by wrapping ``deletion.plan_deletion`` — the
    plan is computed by the real function first, so what the endpoint carries
    into the deletion loop is a genuinely stale plan, and nothing inside that
    loop is patched.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_mapping(client, depotid=900, appid=440)
    _seed_depot(cache_root, 441, 10)
    _seed_depot(cache_root, 900, 50)

    real_plan_deletion = deletion.plan_deletion

    def plan_then_someone_else_maps_900(rows, appid):  # type: ignore[no-untyped-def]
        plan = real_plan_deletion(rows, appid)
        assert 900 in plan.exclusive, "precondition: 900 is exclusive at plan time"
        # The racing write lands here: after the plan, before any removal.
        conn = sqlite3.connect(settings.db_path)
        try:
            conn.execute(
                "INSERT OR IGNORE INTO depot_app_map (depotid, appid) VALUES (900, 730)"
            )
            conn.execute(
                "INSERT OR IGNORE INTO apps (appid, name, status) "
                "VALUES (730, 'Counter-Strike 2', 'idle')"
            )
            conn.commit()
        finally:
            conn.close()
        return plan

    monkeypatch.setattr(deletion, "plan_deletion", plan_then_someone_else_maps_900)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [{"depotid": 441, "size_bytes_freed": 10}]
    assert body["skipped_shared"] == [{"depotid": 900, "shared_with": [730]}], (
        "the depot that became shared must be reported as skipped, with the "
        "fresh owner list"
    )
    assert body["total_bytes_freed"] == 10
    assert (cache_root / "depot" / "900" / "chunk" / "aa").read_bytes() == b"1" * 50, (
        "another game's content was deleted through a stale plan"
    )
    # A clean run despite the late skip -> idle, not error.
    assert _app_row(settings, 440)["status"] == "idle"


def test_delete_unlinks_a_linked_depot_dir_and_the_outside_target_survives(
    tmp_path: Path,
) -> None:
    """End to end: a depot directory that is a symlink/junction pointing OUTSIDE
    the cache root. The link must go, the foreign data must stay, and the freed
    bytes must not be overstated."""
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)

    outside = tmp_path / "somebody-elses-data"
    _write(outside / "precious.bin", b"1" * 4096)
    (cache_root / "depot").mkdir(parents=True)
    kind = make_dir_link(cache_root / "depot" / "441", outside)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [{"depotid": 441, "size_bytes_freed": 0}], (
        f"a {kind} depot dir must report 0 freed bytes — its target is not freed"
    )
    assert body["failed"] == []
    assert not os.path.lexists(cache_root / "depot" / "441"), "the link must be gone"
    assert (outside / "precious.bin").read_bytes() == b"1" * 4096, (
        "data OUTSIDE the cache root was deleted through the link"
    )


def test_delete_unlinks_a_junction_depot_dir_and_the_outside_target_survives(
    tmp_path: Path,
) -> None:
    """The junction-specific version of the test above (Windows only).

    Required as its own test because ``make_dir_link`` prefers a symlink where
    it can create one, and the two differ exactly where it matters:
    ``os.path.islink()`` is ``False`` for a junction and
    ``shutil.rmtree`` refuses it. This is the case the work package asked to be
    verified against a target OUTSIDE the cache root.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)

    outside = tmp_path / "somebody-elses-data"
    _write(outside / "precious.bin", b"1" * 4096)
    _write(outside / "nested" / "more.bin", b"1" * 128)
    (cache_root / "depot").mkdir(parents=True)
    make_junction(cache_root / "depot" / "441", outside)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [{"depotid": 441, "size_bytes_freed": 0}]
    assert body["failed"] == []
    assert not os.path.lexists(cache_root / "depot" / "441"), "the junction must be gone"
    assert (outside / "precious.bin").read_bytes() == b"1" * 4096
    assert (outside / "nested" / "more.bin").read_bytes() == b"1" * 128


def test_delete_does_not_follow_a_junction_nested_inside_the_depot_tree(
    tmp_path: Path,
) -> None:
    """Junction-specific: ``shutil.rmtree`` must not walk *into* it.

    Measured on Windows 11 / CPython 3.12.10: rmtree removes the junction as a
    link and the target survives. Pinned here so a future CPython change is
    caught by this suite rather than by a user losing data.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    depot = _seed_depot(cache_root, 441, 10)

    outside = tmp_path / "somebody-elses-data"
    _write(outside / "precious.bin", b"1" * 2048)
    make_junction(depot / "chunk" / "sneaky", outside)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    assert response.json()["failed"] == []
    assert not depot.exists()
    assert (outside / "precious.bin").read_bytes() == b"1" * 2048


def test_delete_does_not_follow_a_link_nested_inside_the_depot_tree(
    tmp_path: Path,
) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    depot = _seed_depot(cache_root, 441, 10)

    outside = tmp_path / "somebody-elses-data"
    _write(outside / "precious.bin", b"1" * 2048)
    make_dir_link(depot / "chunk" / "sneaky", outside)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    assert response.json()["failed"] == []
    assert not depot.exists()
    assert (outside / "precious.bin").read_bytes() == b"1" * 2048


def test_delete_reports_a_poisoned_mapping_row_and_still_deletes_the_good_depot(
    tmp_path: Path,
) -> None:
    """End to end through a *real* poisoned database row.

    SQLite's INTEGER affinity keeps a non-numeric string as TEXT, so this row
    can genuinely exist in ``depot_app_map.depotid`` (hand-edited database,
    corruption, a future buggy writer). It must be reported, never turned into
    a path, and must not stop the healthy depot from being deleted.
    """
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    _seed_depot(cache_root, 441, 10)
    outside_marker = tmp_path / "outside-the-cache.txt"
    outside_marker.write_bytes(b"must survive")

    conn = sqlite3.connect(settings.db_path)
    try:
        conn.execute(
            "INSERT INTO depot_app_map (depotid, appid) VALUES (?, ?)",
            ("../../outside-the-cache.txt", 440),
        )
        conn.commit()
        stored = conn.execute(
            "SELECT typeof(depotid) FROM depot_app_map WHERE appid = 440 "
            "AND typeof(depotid) = 'text'"
        ).fetchone()
        assert stored is not None, "sqlite coerced the poisoned value away"
    finally:
        conn.close()

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["deleted_depots"] == [{"depotid": 441, "size_bytes_freed": 10}]
    assert len(body["failed"]) == 1
    assert body["failed"][0]["depotid"] == 0
    assert "outside-the-cache.txt" in body["failed"][0]["error"]
    assert outside_marker.read_bytes() == b"must survive"


def test_delete_refuses_when_the_cache_root_has_no_depot_dir(tmp_path: Path) -> None:
    client, cache_root, settings = _make_app(tmp_path)
    _seed_mapping(client, depotid=441, appid=440)
    assert not cache_root.exists()

    response = client.delete("/v1/cache/440", headers=AUTH, follow_redirects=False)

    assert response.status_code == 500
    assert "depot" in response.json()["detail"]


def test_delete_refuses_an_empty_cache_root(tmp_path: Path) -> None:
    # Nothing must be deleted relative to the current working directory.
    client, _cache_root, settings = _make_app(tmp_path, cache_root="")
    _seed_mapping(client, depotid=441, appid=440)

    response = client.delete("/v1/cache/440", headers=AUTH)

    assert response.status_code == 500
    assert "VAULT_CACHE_ROOT is empty" in response.json()["detail"]


def test_delete_route_is_registered_under_the_authenticated_cache_router() -> None:
    # Belt to tests/test_security.py's route walk: the DELETE route must live on
    # the router that carries require_api_key, not be added ad hoc elsewhere.
    from vault_api.routers import cache as cache_router

    paths = {
        route.path  # type: ignore[attr-defined]
        for route in cache_router.router.routes
    }
    assert "/v1/cache/{appid}" in paths
    assert deletion.DEPOT_DIRNAME == "depot"
