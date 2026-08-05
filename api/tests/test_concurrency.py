from __future__ import annotations

import asyncio

import httpx

from tests import stub_prefill
from tests.conftest import TEST_API_KEY
from vault_api.config import Settings
from vault_api.main import create_app
from vault_api.worker import PrefillWorker

AUTH = {"X-Api-Key": TEST_API_KEY}


def test_concurrent_mixed_requests_do_not_500(tmp_path) -> None:
    """Concurrent requests must not 500 — and must not crash the interpreter.

    History this pins (both measured, not theorised):

    - WP 1.3: with a shared per-request connection, FastAPI's sync generator
      dependency and the sync endpoint body land in *different* anyio worker
      threads, so the connection crossed threads and sqlite3's default
      ``check_same_thread=True`` raised ``ProgrammingError`` (60/60 requests
      500'd). WP 1.3 papered over it with ``check_same_thread=False``.
    - WP 1.4: that paper-over turned the 500 into a **native access
      violation** — the dependency's ``conn.close()`` runs while the body is
      still inside ``execute()`` on the same connection. Fixed structurally by
      ``deps.db_opener``: the connection is opened and closed inside the
      endpoint body, so it never leaves one thread (see deps.py).

    FastAPI's synchronous ``TestClient`` doesn't exercise real thread
    concurrency the way a live server under load does, so this test drives
    the ASGI app directly through ``httpx.ASGITransport`` with
    ``asyncio.gather`` over a mix of read and write endpoints, all asserted
    to return a non-5xx status.
    """
    cache_root = tmp_path / "cache"
    # A real depot tree to scan and (WP 1.6) to delete from: the mapping PUTs
    # below map depot 1000+i to app 440+i, so these are the directories the
    # concurrent DELETEs actually remove while other requests walk them.
    for depotid in range(1000, 1010):
        chunk = cache_root / "depot" / str(depotid) / "chunk"
        chunk.mkdir(parents=True)
        (chunk / "aa").write_bytes(b"1" * 64)

    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
    )
    app = create_app(settings)

    async def run() -> list[int]:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:

            async def games_list() -> int:
                response = await client.get("/v1/games", headers=AUTH)
                return response.status_code

            async def games_detail(appid: int) -> int:
                response = await client.get(f"/v1/games/{appid}", headers=AUTH)
                return response.status_code

            async def mapping_put(depotid: int, appid: int) -> int:
                response = await client.put(
                    f"/v1/mapping/{depotid}",
                    json={"appid": appid, "app_name": None},
                    headers=AUTH,
                )
                return response.status_code

            async def jobs_list() -> int:
                response = await client.get("/v1/jobs?limit=20", headers=AUTH)
                return response.status_code

            async def prefill_post(appid: int) -> int:
                response = await client.post(
                    "/v1/prefill", json={"appids": [appid]}, headers=AUTH
                )
                return response.status_code

            async def cache_summary() -> int:
                response = await client.get("/v1/cache/summary", headers=AUTH)
                return response.status_code

            async def cache_delete(appid: int) -> int:
                response = await client.delete(f"/v1/cache/{appid}", headers=AUTH)
                return response.status_code

            tasks = []
            for i in range(10):
                tasks.append(games_list())
                tasks.append(games_detail(440 + i))
                tasks.append(mapping_put(1000 + i, 440 + i))
                # WP 1.4: the queue's read + write paths join the mix. The
                # enqueue path takes SQLite's write lock (BEGIN IMMEDIATE), so
                # this also checks concurrent enqueues don't surface as 500s
                # ("database is locked") — several of these target the same
                # appid, so they race on the dedupe transaction too.
                tasks.append(jobs_list())
                tasks.append(prefill_post(440 + (i % 3)))
                # WP 1.5: SizeCache.get() takes its own lock and, on a miss,
                # walks the depot tree — make sure that races cleanly against
                # the same-request games_list()/games_detail() calls that also
                # hit it, instead of each other, without a 500.
                tasks.append(cache_summary())
                # WP 1.6: DELETE joins the mix — it reads the mapping, walks
                # and removes depot directories, invalidates the size cache and
                # writes apps.status, all while the mapping PUTs above are
                # creating rows for the very same app ids. Two DELETEs of the
                # same app can therefore race (i % 3 collides across the loop):
                # the second must come back as a clean 404/empty result, never
                # a 500 from a vanished directory or a double removal.
                tasks.append(cache_delete(440 + (i % 3)))

            return await asyncio.gather(*tasks)

    statuses = asyncio.run(run())

    assert len(statuses) == 70
    assert all(status_code < 500 for status_code in statuses), statuses


def test_http_reads_survive_a_worker_writing_in_the_background(tmp_path) -> None:
    """WP 1.4: the worker writes (jobs, apps, depot_app_map) while HTTP reads.

    The worker is a real background thread with its own long-lived connection,
    committing several times per job, while request handlers open fresh
    connections and read. WAL + busy_timeout is what makes that safe; this test
    is the regression guard for it (a "database is locked" would surface as a
    500 here).
    """
    bindir = tmp_path / "bin"
    cache_root = tmp_path / "cache"
    executable = stub_prefill.make_stub(
        bindir,
        cache_root=str(cache_root),
        depots_by_app={appid: [appid * 10, appid * 10 + 1] for appid in range(1, 13)},
    )
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(cache_root),
        log_level="INFO",
        steamprefill_path=executable,
        prefill_timeout_seconds=60,
        worker_poll_seconds=0.01,
    )
    app = create_app(settings)

    worker = PrefillWorker(settings)
    worker.start()
    try:

        async def run() -> list[int]:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                seed = await client.post(
                    "/v1/prefill",
                    json={"appids": list(range(1, 13))},
                    headers=AUTH,
                )
                assert seed.status_code == 202, seed.text

                async def get(path: str) -> int:
                    response = await client.get(path, headers=AUTH)
                    return response.status_code

                tasks: list = []
                for index in range(1, 13):
                    tasks.append(get("/v1/jobs?limit=50"))
                    tasks.append(get(f"/v1/jobs/{index}"))
                    tasks.append(get("/v1/games"))
                    tasks.append(get(f"/v1/games/{index}"))
                    tasks.append(get("/v1/mapping"))
                    # WP 1.5: reads through the size cache while the worker
                    # writes chunk files into the SAME cache_root tree the
                    # cache scans — a disk-level analogue of the sqlite
                    # WAL/busy_timeout regression this test already pins.
                    tasks.append(get("/v1/cache/summary"))
                return await asyncio.gather(*tasks)

        # Hammer it repeatedly so reads overlap several job boundaries.
        for _ in range(3):
            statuses = asyncio.run(run())
            assert all(status_code < 500 for status_code in statuses), statuses
    finally:
        worker.stop(timeout=30)

    # Sanity: the worker really was doing work while being hammered.
    assert stub_prefill.read_runs(bindir), "worker executed no jobs"
