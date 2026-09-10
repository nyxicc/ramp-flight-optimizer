"""Fictional queue, process supervision, cancellation and result workflows."""

import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ramp_optimizer_api.app import create_app
from ramp_optimizer_api.job_services import JobService
from ramp_optimizer_api.local_worker import LocalWorker
from ramp_optimizer_api.schemas import OptimizationRequest
from ramp_optimizer_persistence.database import create_database_engine, make_session_factory
from ramp_optimizer_persistence.jobs import claim_job
from ramp_optimizer_persistence.models import Base, OptimizationRunRow
from tests.test_persistence_api import _payload


@pytest.fixture
def context(tmp_path):
    engine = create_database_engine(f"sqlite:///{(tmp_path / 'jobs.sqlite').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = make_session_factory(engine)
    with TestClient(create_app(session_factory=sessions)) as client:
        yield client, JobService(sessions)
    engine.dispose()


def queued(context):
    client, _ = context
    result = client.post("/api/v1/optimizations", json=_payload())
    assert result.status_code == 202, result.text
    return result.json()


def crash_child(connection, day, config, timeout):
    connection.close()


def hang_child(connection, day, config, timeout):
    connection.send(("checkpoint", {"phase": "SOLVING", "stage_number": 1}, None))
    time.sleep(30)


def partial_child(connection, day, config, timeout):
    from ramp_optimizer import optimize_flight_assignments
    from ramp_optimizer_api.mapping import optimization_result_to_response

    result = optimization_result_to_response(optimize_flight_assignments(day, config)).model_dump(
        mode="json"
    )
    connection.send(("checkpoint", {"phase": "SOLVING", "stage_number": 1}, result))
    time.sleep(30)


def test_submit_retry_progress_and_result(context, monkeypatch):
    client, service = context
    job = queued(context)
    assert job["status"] == "QUEUED"
    assert queued(context)["id"] == job["id"]
    assert client.get(f"/api/v1/optimization-jobs/{job['id']}/result").status_code == 409
    assert LocalWorker(service).run_once()
    finished = service.get(job["id"])
    assert finished.status == "SUCCEEDED"
    assert finished.result_run_id is not None
    assert finished.progress.stage_number > 0
    result = service.result(job["id"])
    assert not result.partial and result.result.attempts and result.result.objective_values
    assert (
        client.post(f"/api/v1/optimization-jobs/{job['id']}/cancel").json()["status"] == "SUCCEEDED"
    )
    assert not LocalWorker(service).run_once()


def test_queued_cancellation_is_idempotent(context):
    client, service = context
    job = queued(context)
    path = f"/api/v1/optimization-jobs/{job['id']}/cancel"
    assert client.post(path).json() == client.post(path).json()
    assert service.get(job["id"]).status == "CANCELLED"
    assert not LocalWorker(service).run_once()


@pytest.mark.parametrize("target,expected", [(crash_child, "FAILED"), (hang_child, "TIMED_OUT")])
def test_worker_crash_and_timeout(context, target, expected):
    _, service = context
    job = service.submit(
        request=OptimizationRequest.model_validate(_payload()),
        # Crash classification must allow Windows spawn/import startup; the
        # separate hanging-child case deliberately exercises the short deadline.
        timeout=30 if target is crash_child else 2,
    )
    assert LocalWorker(service, target=target).run_once()
    assert service.get(str(job.id)).status == expected
    assert service.get(str(job.id)).error_code


def test_running_cancellation_preserves_checkpoint(context):
    _, service = context
    job = queued(context)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(LocalWorker(service, target=partial_child).run_once)
        deadline = time.monotonic() + 15
        while not service.get(job["id"]).has_partial_result and time.monotonic() < deadline:
            time.sleep(0.05)
        assert service.get(job["id"]).has_partial_result
        assert service.cancel(job["id"]).status == "CANCELLING"
        assert future.result(timeout=10)
    result = service.result(job["id"])
    assert result.status == "CANCELLED" and result.partial
    assert result.result.flight_results and result.result_run_id


def test_atomic_claim_and_stale_worker_fencing(context):
    _, service = context
    job = queued(context)

    def claim():
        with service.sessions.begin() as session:
            return claim_job(session)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _: claim(), range(2)))
    assert sum(row is not None for row in claims) == 1
    row = next(row for row in claims if row)
    service.finish(job["id"], "wrong-token", "FAILED")
    assert service.get(job["id"]).status == "RUNNING"
    service.finish(job["id"], row.worker_token, "FAILED", error_code="FICTIONAL_FAILURE")
    service.finish(job["id"], row.worker_token, "SUCCEEDED")
    assert service.get(job["id"]).status == "FAILED"


def test_abandoned_job_is_failed_without_reexecution(context):
    _, service = context
    job = queued(context)
    with service.sessions.begin() as session:
        row = claim_job(session)
        row.started_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    worker = LocalWorker(service)
    worker.recover_expired()
    assert service.get(job["id"]).error_code == "WORKER_LOST"
    assert not worker.run_once()


def test_version_config_linkage_and_idempotency(context):
    client, service = context
    payload = _payload()
    day = payload["operational_day"]["operational_date"]
    root = client.post(
        f"/api/v1/operational-days/{day}/drafts", json={"idempotency_key": "draft"}
    ).json()
    version = client.post(
        f"/api/v1/operational-day-versions/{root['id']}/revisions",
        json={
            "idempotency_key": "revision",
            "expected_parent_hash": root["content_hash"],
            "input": payload,
        },
    ).json()
    request = {
        "operational_day_version_id": version["id"],
        "idempotency_key": "job",
        "config": {"solver_time_limit_seconds": 1},
    }
    first = client.post("/api/v1/optimization-jobs", json=request)
    assert first.status_code == 202, first.text
    assert first.json()["operational_day_version_id"] == version["id"]
    assert first.json()["operational_day_id"] != version["id"]
    assert client.post("/api/v1/optimization-jobs", json=request).json() == first.json()
    request["idempotency_key"] = "alias"
    assert client.post("/api/v1/optimization-jobs", json=request).json() == first.json()
    request["timeout_seconds"] = 9
    assert client.post("/api/v1/optimization-jobs", json=request).status_code == 409
    assert LocalWorker(service).run_once()
    request.pop("timeout_seconds")
    assert client.post("/api/v1/optimization-jobs", json=request).json()["id"] == first.json()["id"]
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OptimizationRunRow)) == 1


def test_timeout_preserves_partial_run(context):
    _, service = context
    job = service.submit(request=OptimizationRequest.model_validate(_payload()), timeout=8)
    assert LocalWorker(service, target=partial_child).run_once()
    result = service.result(str(job.id))
    assert result.status == "TIMED_OUT" and result.partial
    assert result.result_run_id and result.result.objective_values


def test_http_admission_never_calls_solver(context, monkeypatch):
    import ramp_optimizer

    def forbidden(*args, **kwargs):
        raise AssertionError("HTTP called solver")

    monkeypatch.setattr(ramp_optimizer, "optimize_flight_assignments", forbidden)
    assert queued(context)["status"] == "QUEUED"


def test_concurrent_admission_produces_one_snapshot_and_job(context):
    from ramp_optimizer_persistence.models import OperationalDayRow, OptimizationJobRow

    _, service = context
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(
            pool.map(
                lambda _: service.submit(request=OptimizationRequest.model_validate(_payload())),
                range(2),
            )
        )
    assert jobs[0].id == jobs[1].id
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OptimizationJobRow)) == 1
        assert session.scalar(select(func.count()).select_from(OperationalDayRow)) == 1


def test_public_request_validation_and_missing_resources(context):
    client, _ = context
    missing = "00000000-0000-0000-0000-000000000099"
    request = {"operational_day_version_id": missing, "idempotency_key": "missing"}
    assert client.post("/api/v1/optimization-jobs", json=request).status_code == 404
    for suffix in ("", "/result", "/progress"):
        assert client.get(f"/api/v1/optimization-jobs/{missing}{suffix}").status_code == 404
    for timeout in (0, -1, 3601, True, "60"):
        request["timeout_seconds"] = timeout
        assert client.post("/api/v1/optimization-jobs", json=request).status_code == 422


def test_result_publication_rolls_back_and_retries_once(context, monkeypatch):
    import ramp_optimizer_api.job_services as module
    from ramp_optimizer import optimize_flight_assignments
    from ramp_optimizer_api.mapping import map_optimization_request, optimization_result_to_response

    _, service = context
    job = queued(context)
    with service.sessions.begin() as session:
        claimed = claim_job(session)
    mapped = map_optimization_request(OptimizationRequest.model_validate(_payload()))
    result = optimization_result_to_response(
        optimize_flight_assignments(mapped.operational_day, mapped.config)
    ).model_dump(mode="json")
    original = module.create_optimization_run

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("Fictional failure after run insert")

    monkeypatch.setattr(module, "create_optimization_run", fail)
    with pytest.raises(RuntimeError):
        service.finish(job["id"], claimed.worker_token, "SUCCEEDED", result)
    assert service.get(job["id"]).status == "RUNNING"
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OptimizationRunRow)) == 0
    monkeypatch.setattr(module, "create_optimization_run", original)
    service.finish(job["id"], claimed.worker_token, "SUCCEEDED", result)
    service.finish(job["id"], claimed.worker_token, "SUCCEEDED", result)
    with service.sessions() as session:
        assert session.scalar(select(func.count()).select_from(OptimizationRunRow)) == 1
