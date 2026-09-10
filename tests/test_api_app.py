"""Application construction, versioning, and import-boundary tests."""

import importlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import ramp_optimizer
from ramp_optimizer_api.app import API_PREFIX, API_VERSION, create_app


def test_create_app_returns_fastapi_application() -> None:
    application = create_app()

    assert isinstance(application, FastAPI)
    assert application.title == "Ramp Flight Optimizer API"
    assert application.version == API_VERSION


def test_application_import_does_not_optimize_or_write_files(monkeypatch) -> None:
    original_optimizer = ramp_optimizer.optimize_flight_assignments

    def forbidden(*_args, **_kwargs):
        raise AssertionError("application import caused a forbidden side effect")

    monkeypatch.setattr(ramp_optimizer, "optimize_flight_assignments", forbidden)
    monkeypatch.setattr(Path, "write_text", forbidden)
    monkeypatch.setattr(Path, "write_bytes", forbidden)

    module = importlib.import_module("ramp_optimizer_api.app")
    reloaded = importlib.reload(module)

    assert isinstance(reloaded.app, FastAPI)
    reloaded.optimize_flight_assignments = original_optimizer


def test_health_version_and_openapi_contract() -> None:
    client = TestClient(create_app())

    assert client.get(f"{API_PREFIX}/health").json() == {"status": "ok"}
    version = client.get(f"{API_PREFIX}/version")
    assert version.status_code == 200
    assert version.json() == {"api_version": "1", "package_version": "0.1.0"}

    openapi = client.get("/openapi.json")
    assert openapi.status_code == 200
    paths = set(openapi.json()["paths"])
    assert paths == {
        "/api/v1/optimization-jobs",
        "/api/v1/optimization-jobs/{job_id}",
        "/api/v1/optimization-jobs/{job_id}/progress",
        "/api/v1/optimization-jobs/{job_id}/cancel",
        "/api/v1/optimization-jobs/{job_id}/result",
        "/api/v1/operational-days/{day}/drafts",
        "/api/v1/operational-days/{day}/versions",
        "/api/v1/operational-day-versions/{version_id}",
        "/api/v1/operational-day-versions/{version_id}/revisions",
        "/api/v1/imports/daily-flight-log",
        "/api/v1/imports/combine",
        "/api/v1/imports/operational-days/{day_id}/readiness",
        "/api/v1/imports/{import_id}/revisions/{revision}",
        "/api/v1/imports/teamwork-employee-schedule",
        "/api/v1/imports/{import_id}",
        "/api/v1/imports/{import_id}/preview",
        "/api/v1/imports/{import_id}/corrections",
        "/api/v1/imports/{import_id}/confirm",
        "/api/v1/health",
        "/api/v1/version",
        "/api/v1/operational-days",
        "/api/v1/operational-days/{operational_day_id}",
        "/api/v1/operational-days/{operational_day_id}/optimizations",
        "/api/v1/operational-days/{operational_day_id}/optimization-runs",
        "/api/v1/optimization-runs/{optimization_run_id}",
        "/api/v1/operational-days/validate",
        "/api/v1/optimizations",
    }
    assert all(path.startswith(API_PREFIX) for path in paths)
    assert "/health" not in paths
    assert "/version" not in paths
