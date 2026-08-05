from __future__ import annotations

import asyncio

import httpx

from tests.conftest import TEST_API_KEY
from vault_api.config import Settings
from vault_api.main import create_app

AUTH = {"X-Api-Key": TEST_API_KEY}


def test_concurrent_mixed_requests_do_not_500(tmp_path) -> None:
    """B1 regression (WP 1.3 review): concurrent requests must not 500.

    FastAPI runs a sync generator dependency (``deps.get_db``) and the sync
    endpoint body that consumes its yielded connection via
    ``run_in_threadpool`` — under concurrency, the two can land in
    different anyio worker threads *within the same request*, so the same
    ``sqlite3.Connection`` legitimately crosses threads. sqlite3's default
    ``check_same_thread=True`` raised ``ProgrammingError`` for that (the
    reviewer measured 60/60 requests failing with a 500 before the
    ``db.py`` fix; 264/264 succeeded after).

    FastAPI's synchronous ``TestClient`` doesn't exercise real thread
    concurrency the way a live server under load does, so this test drives
    the ASGI app directly through ``httpx.ASGITransport`` with
    ``asyncio.gather`` over a mix of endpoints (games list, games detail,
    mapping PUT) — ~30 concurrent requests, all asserted to return a
    non-5xx status.
    """
    settings = Settings(
        vault_api_key=TEST_API_KEY,
        db_path=str(tmp_path / "vault.db"),
        cache_root=str(tmp_path / "cache"),
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

            tasks = []
            for i in range(10):
                tasks.append(games_list())
                tasks.append(games_detail(440 + i))
                tasks.append(mapping_put(1000 + i, 440 + i))

            return await asyncio.gather(*tasks)

    statuses = asyncio.run(run())

    assert len(statuses) == 30
    assert all(status_code < 500 for status_code in statuses), statuses
