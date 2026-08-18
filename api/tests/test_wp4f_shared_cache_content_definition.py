"""WP 4f: one definition of "which apps hold cache content", used by both
`scheduler.cached_appids` (the WP 4d keep-current sweep's target-set
widening) and `routers/jobs.py`'s `POST /v1/prefill/cached` selection helper.

Before this package the two surfaces computed the predicate independently
and disagreed on exactly the case that matters most (a game the operator
just deleted, whose only surviving content is a shared, still-protected
depot) -- see `docs/PROJECT_PLAN.md` Phase 4f and `vault_api.deletion.
appids_with_cache_content`'s docstring for the full story.

This module proves the fix STRUCTURALLY, not just behaviourally: both call
sites are thin wrappers around the exact same function object, with no extra
filtering layered on top at either call site. Swapping the shared function
for a distinguishable fake and asserting both callers hand back exactly its
result (not a superset, not a subset) is what a purely behavioural pin
(seed some fixtures, check the output) cannot prove on its own -- two
independently-written predicates that happen to agree on today's fixtures
would pass those just as well. `tests/test_cache_delete.py`'s
`appids_with_cache_content` unit tests and `tests/test_scheduler.py`'s
`cached_appids` tests are what catch a regression IN the shared predicate
itself; this file is what catches a caller quietly growing a second one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from vault_api import deletion
from vault_api import scheduler as scheduler_module
from vault_api.db import get_connection, init_db
from vault_api.routers.jobs import _select_appids_with_cache_content


@pytest.fixture
def conn(tmp_path: Path):
    db_path = str(tmp_path / "vault.db")
    init_db(db_path)
    connection = get_connection(db_path)
    try:
        yield connection
    finally:
        connection.close()


def test_scheduler_and_route_both_return_exactly_the_shared_definitions_result(
    tmp_path: Path, conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[int, int]] = []

    def fake_shared_definition(passed_conn, depot_bytes):
        assert passed_conn is conn
        calls.append(dict(depot_bytes))
        # A sentinel appid that appears in NO real mapping row anywhere in
        # this test -- if either caller derived its own answer instead of
        # returning this fake's result verbatim, this id would never appear.
        return {999_999}

    monkeypatch.setattr(deletion, "appids_with_cache_content", fake_shared_definition)

    # -- scheduler.cached_appids -------------------------------------------
    # Real bytes on disk so the wrapper's own early-exit ("empty cache ->
    # set()") does not short-circuit before ever reaching the shared call.
    cache_root = tmp_path / "cache"
    depot_dir = cache_root / "depot" / "441" / "chunk"
    depot_dir.mkdir(parents=True)
    (depot_dir / "a.bin").write_bytes(b"x")

    scheduler_result = scheduler_module.cached_appids(conn, str(cache_root))
    assert scheduler_result == {999_999}

    # -- routers/jobs.py's selection helper ---------------------------------
    route_result = _select_appids_with_cache_content(conn, {441: 1})
    assert route_result == [999_999]  # sorted() of the fake's set

    # Both callers actually reached the shared function -- exactly once each.
    assert len(calls) == 2


def test_mutating_the_shared_definition_breaks_both_callers(
    tmp_path: Path, conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mutation-kill counterpart of the test above: a shared definition
    that always reports "nothing is cached" must make BOTH the sweep and the
    route report nothing, for the SAME real fixture -- proving there is no
    second, independent path either one could fall back on."""
    from vault_api.mapping import upsert_mapping

    cache_root = tmp_path / "cache"
    depot_dir = cache_root / "depot" / "441" / "chunk"
    depot_dir.mkdir(parents=True)
    (depot_dir / "a.bin").write_bytes(b"x")
    upsert_mapping(conn, depotid=441, appid=440, name="TF2")

    # Sanity: the REAL shared definition finds it, from both callers.
    assert scheduler_module.cached_appids(conn, str(cache_root)) == {440}
    assert _select_appids_with_cache_content(conn, {441: 1}) == [440]

    # Mutation: the shared definition now always says "nothing is cached".
    monkeypatch.setattr(
        deletion, "appids_with_cache_content", lambda conn, depot_bytes: set()
    )

    assert scheduler_module.cached_appids(conn, str(cache_root)) == set()
    assert _select_appids_with_cache_content(conn, {441: 1}) == []
