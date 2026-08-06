"""HTTP layer of the prefill queue: POST /v1/prefill, GET /v1/jobs[/{id}].

No worker runs in these tests: ``TestClient`` only executes the lifespan when
used as a context manager, and the ``client`` fixture doesn't, so jobs stay
'queued' and the queue semantics can be asserted deterministically. Worker
behavior lives in test_worker.py.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.conftest import TEST_API_KEY

AUTH = {"X-Api-Key": TEST_API_KEY}


# -- auth ------------------------------------------------------------------


def test_new_routes_require_api_key(client: TestClient) -> None:
    assert client.post("/v1/prefill", json={"appids": [440]}).status_code == 401
    assert client.get("/v1/jobs").status_code == 401
    assert client.get("/v1/jobs/1").status_code == 401


# -- enqueue ---------------------------------------------------------------


def test_prefill_creates_one_queued_job_per_appid(client: TestClient) -> None:
    response = client.post("/v1/prefill", json={"appids": [440, 730]}, headers=AUTH)

    assert response.status_code == 202, response.text
    body = response.json()
    assert [entry["appid"] for entry in body] == [440, 730]
    assert all(entry["status"] == "queued" for entry in body)
    assert all(entry["deduplicated"] is False for entry in body)
    assert body[0]["job_id"] != body[1]["job_id"]

    jobs = client.get("/v1/jobs", headers=AUTH).json()
    assert len(jobs) == 2
    assert {job["appid"] for job in jobs} == {440, 730}
    assert all(job["type"] == "prefill" for job in jobs)
    assert all(job["created_at"] for job in jobs)
    assert all(job["started_at"] is None and job["finished_at"] is None for job in jobs)


def test_prefill_creates_the_apps_row_so_the_app_can_poll_it(client: TestClient) -> None:
    # First prefill of a never-before-seen title: GET /v1/games/{appid} must
    # answer 200 immediately, not 404, or the app's detail view breaks while
    # the job is still queued.
    assert client.get("/v1/games/12345", headers=AUTH).status_code == 404

    client.post("/v1/prefill", json={"appids": [12345]}, headers=AUTH)

    detail = client.get("/v1/games/12345", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["status"] == "idle"
    assert detail.json()["depots"] == []


def test_repeated_prefill_of_the_same_app_is_deduplicated(client: TestClient) -> None:
    first = client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH).json()
    second = client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH).json()

    assert second[0]["job_id"] == first[0]["job_id"]
    assert first[0]["deduplicated"] is False
    assert second[0]["deduplicated"] is True

    # ...and no second job row was stacked.
    jobs = client.get("/v1/jobs", headers=AUTH).json()
    assert len(jobs) == 1


def test_duplicate_appids_in_one_request_collapse_to_one_job(client: TestClient) -> None:
    body = client.post(
        "/v1/prefill", json={"appids": [440, 440, 730]}, headers=AUTH
    ).json()

    assert [entry["appid"] for entry in body] == [440, 440, 730]
    assert body[0]["job_id"] == body[1]["job_id"]
    assert body[1]["deduplicated"] is True
    assert len(client.get("/v1/jobs", headers=AUTH).json()) == 2


def test_a_finished_job_does_not_block_a_new_one(client: TestClient) -> None:
    # Dedupe only covers queued/running jobs — re-prefilling an app whose
    # previous job already finished must create a fresh job.
    first = client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH).json()
    _force_status(client, first[0]["job_id"], "done")

    second = client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH).json()

    assert second[0]["job_id"] != first[0]["job_id"]
    assert second[0]["deduplicated"] is False


def _force_status(client: TestClient, job_id: int, status: str) -> None:
    """Move a job to a terminal status without a worker (test-only shortcut)."""
    from vault_api.db import get_connection

    conn = get_connection(client.app.state.settings.db_path)
    try:
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?", (status, job_id))
        conn.commit()
    finally:
        conn.close()


# -- validation ------------------------------------------------------------


def test_prefill_rejects_bad_bodies(client: TestClient) -> None:
    bad_bodies = [
        {"appids": []},            # non-empty list required
        {"appids": [0]},           # ge=1
        {"appids": [-5]},          # ge=1
        {"appids": [440, 0]},      # every item validated, not just the first
        {"appids": 440},           # must be a list
        {"appids": ["440x"]},      # must be ints
        {},                        # appids is required
        {"appids": [440], "appIds": [730]},  # extra="forbid"
    ]
    for body in bad_bodies:
        response = client.post("/v1/prefill", json=body, headers=AUTH)
        assert response.status_code == 422, f"{body!r} -> {response.status_code}"


def test_jobs_limit_is_validated(client: TestClient) -> None:
    assert client.get("/v1/jobs?limit=0", headers=AUTH).status_code == 422
    assert client.get("/v1/jobs?limit=201", headers=AUTH).status_code == 422
    assert client.get("/v1/jobs?limit=1", headers=AUTH).status_code == 200


# -- reads -----------------------------------------------------------------


def test_jobs_list_is_newest_first_and_honors_limit(client: TestClient) -> None:
    client.post("/v1/prefill", json={"appids": [10, 20, 30]}, headers=AUTH)

    jobs = client.get("/v1/jobs", headers=AUTH).json()
    assert [job["appid"] for job in jobs] == [30, 20, 10]
    # The list view deliberately omits log_excerpt (keeps polling responses small).
    assert "log_excerpt" not in jobs[0]

    limited = client.get("/v1/jobs?limit=2", headers=AUTH).json()
    assert [job["appid"] for job in limited] == [30, 20]


def test_job_detail_includes_log_excerpt_field_and_404s_for_unknown(
    client: TestClient,
) -> None:
    job_id = client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH).json()[0][
        "job_id"
    ]

    detail = client.get(f"/v1/jobs/{job_id}", headers=AUTH)
    assert detail.status_code == 200
    payload = detail.json()
    assert payload["id"] == job_id
    assert payload["appid"] == 440
    assert payload["status"] == "queued"
    assert payload["log_excerpt"] is None

    assert client.get("/v1/jobs/999999", headers=AUTH).status_code == 404


# -- summary fields (schema v4, WP 3.3) -------------------------------------


def test_queued_job_exposes_null_summary_fields_in_list_and_detail(
    client: TestClient,
) -> None:
    """No worker runs in this module (see the module docstring), so these
    stay exactly what a freshly queued job's DB row is: SQL NULL — never a
    guessed 0 (ADR-0006 decision 1's whole point extends to the API surface
    too). Pins that GET /v1/jobs (the list, not just the detail) also carries
    the three additive fields, per the work package."""
    job_id = client.post("/v1/prefill", json={"appids": [440]}, headers=AUTH).json()[0][
        "job_id"
    ]

    detail = client.get(f"/v1/jobs/{job_id}", headers=AUTH).json()
    assert detail["updated"] is None
    assert detail["up_to_date"] is None
    assert detail["summary_parse_ok"] is None

    listed = next(
        entry for entry in client.get("/v1/jobs", headers=AUTH).json()
        if entry["id"] == job_id
    )
    assert listed["updated"] is None
    assert listed["up_to_date"] is None
    assert listed["summary_parse_ok"] is None
