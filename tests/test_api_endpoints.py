"""Validation, optimization, and public error-contract tests."""

import importlib

import pytest
from fastapi.testclient import TestClient

from ramp_optimizer import optimize_flight_assignments
from ramp_optimizer.sample_data import (
    build_emergency_lead_scenario,
    build_staffing_shortage_scenario,
)
from ramp_optimizer_api.app import create_app
from ramp_optimizer_api.mapping import (
    operational_day_to_request,
    optimization_result_to_response,
)
from ramp_optimizer_persistence.database import create_database_engine, make_session_factory
from ramp_optimizer_persistence.models import Base
from tests.invariant_checks import assert_result_invariants
from tests.job_helpers import completed_result
from tests.scenario_builders import small_ready_scenario


@pytest.fixture
def client() -> TestClient:
    engine = create_database_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with TestClient(create_app(session_factory=make_session_factory(engine))) as client:
        yield client
    engine.dispose()


def _payload(scenario) -> dict[str, object]:
    return operational_day_to_request(
        scenario.day,
        scenario.config,
    ).model_dump(mode="json")


def _without_runtimes(value):
    if isinstance(value, dict):
        return {key: _without_runtimes(item) for key, item in value.items() if "runtime" not in key}
    if isinstance(value, list):
        return [_without_runtimes(item) for item in value]
    return value


def test_validation_endpoint_accepts_valid_normal_input(client: TestClient) -> None:
    response = client.post(
        "/api/v1/operational-days/validate",
        json=_payload(small_ready_scenario()),
    )

    assert response.status_code == 200
    assert response.json() == {"valid": True, "issues": []}


def test_validation_endpoint_aggregates_domain_issues(client: TestClient) -> None:
    response = client.post(
        "/api/v1/operational-days/validate",
        json={
            "operational_day": {
                "operational_date": "2035-04-15",
                "employees": [
                    {"employee_id": "A1", "name": "First"},
                    {"employee_id": " a1 ", "name": "Second"},
                ],
                "flights": [
                    {
                        "arrival_flight_number": "3000",
                        "arrival_time": "2035-04-15T08:00:00",
                        "departure_flight_number": "3001",
                        "departure_time": "2035-04-15T09:00:00+00:00",
                    }
                ],
            },
            "config": {"minimum_staff": 0},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    codes = {item["code"] for item in body["issues"]}
    assert "DUPLICATE_EMPLOYEE_ID" in codes
    assert "INVALID_POSITIVE_INTEGER" in codes
    assert "MIXED_TURN_SERVICE_CATEGORY" in codes
    assert "MIXED_DATETIME_AWARENESS" in codes


@pytest.mark.parametrize(
    "payload",
    [
        {"operational_day": {"operational_date": "not-a-date"}},
        {
            "operational_day": {
                "operational_date": "2035-04-15",
                "employee_shifts": [
                    {
                        "employee_id": "A1",
                        "start": "not-a-datetime",
                        "end": "2035-04-15T09:00:00",
                        "normalized_role": "RAMP_AGENT",
                    }
                ],
            }
        },
        {
            "operational_day": {
                "operational_date": "2035-04-15",
                "employees": [
                    {
                        "employee_id": "A1",
                        "name": "Agent",
                        "qualifications": ["NOT_A_QUALIFICATION"],
                    }
                ],
            }
        },
    ],
)
def test_structurally_invalid_requests_use_stable_422_envelope(
    client: TestClient,
    payload: dict[str, object],
) -> None:
    response = client.post("/api/v1/operational-days/validate", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert response.json()["error"]["details"]


def test_malformed_json_uses_stable_422_envelope(client: TestClient) -> None:
    response = client.post(
        "/api/v1/operational-days/validate",
        content="{",
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "REQUEST_VALIDATION_ERROR"


def test_authoritative_values_are_not_silently_coerced(client: TestClient) -> None:
    response = client.post(
        "/api/v1/operational-days/validate",
        json={
            "operational_day": {
                "operational_date": "2035-04-15",
                "employees": [{"employee_id": 123, "name": "Agent", "enabled": 1}],
            },
            "config": {"minimum_staff": "3"},
        },
    )

    assert response.status_code == 422
    paths = {item["path"] for item in response.json()["error"]["details"]}
    assert "operational_day.employees[0].employee_id" in paths
    assert "operational_day.employees[0].enabled" in paths
    assert "config.minimum_staff" in paths


def test_validation_covers_flight_sides_classification_boundary_and_namespaces(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/operational-days/validate",
        json={
            "operational_day": {
                "operational_date": "2035-04-15",
                "flights": [
                    {
                        "arrival_flight_number": "AA3000",
                        "arrival_time": "2035-04-15T08:00:00",
                    },
                    {
                        "departure_flight_number": "BB03000",
                        "departure_time": "2035-04-15T09:00:00",
                    },
                    {"arrival_flight_number": "AA3001"},
                ],
            }
        },
    )

    assert response.status_code == 200
    issues = response.json()["issues"]
    assert [item["code"] for item in issues] == ["ARRIVAL_FLIGHT_NUMBER_WITHOUT_TIME"]
    assert not any("DUPLICATE" in item["code"] for item in issues)


def test_invalid_fixed_assignment_is_validation_result_and_optimizer_error(
    client: TestClient,
) -> None:
    payload = {
        "operational_day": {
            "operational_date": "2035-04-15",
            "fixed_assignments": [
                {
                    "employee_id": "A1",
                    "flight": {"arrival_flight_number": "AA999"},
                }
            ],
        }
    }

    validation = client.post("/api/v1/operational-days/validate", json=payload)
    assert validation.status_code == 200
    assert validation.json()["issues"][0]["code"] == ("UNRESOLVED_FIXED_FLIGHT_REFERENCE")

    optimization = client.post("/api/v1/optimizations", json=payload)
    assert optimization.status_code == 422
    assert optimization.json()["error"]["code"] == ("FIXED_ASSIGNMENT_REFERENCE_INVALID")


def test_domain_invalid_optimization_uses_structured_422(client: TestClient) -> None:
    response = client.post(
        "/api/v1/optimizations",
        json={
            "operational_day": {
                "operational_date": "2035-04-15",
                "employees": [
                    {"employee_id": "A1", "name": "First"},
                    {"employee_id": "a1", "name": "Second"},
                ],
            }
        },
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "OPTIMIZER_INPUT_INVALID"
    assert response.json()["error"]["details"][0]["code"] == ("DUPLICATE_EMPLOYEE_ID")


def test_background_job_accepts_longer_solver_budget(client: TestClient) -> None:
    scenario = small_ready_scenario()
    payload = _payload(scenario)
    payload["config"]["solver_time_limit_seconds"] = 60.01

    response = client.post("/api/v1/optimizations", json=payload)

    assert response.status_code == 202
    assert response.json()["config"]["solver_time_limit_seconds"] == 60.01


def test_ready_optimization_preserves_complete_attempt_and_objective_data(
    client: TestClient,
) -> None:
    scenario = small_ready_scenario()
    response = client.post("/api/v1/optimizations", json=_payload(scenario))

    assert response.status_code == 202
    body = completed_result(client, response)
    assert body["status"] in {"OPTIMAL", "FEASIBLE"}
    assert body["operational_readiness"] in {"READY", "READY_WITH_WARNINGS"}
    assert body["emergency_lead_staffing_used"] is False
    assert body["lead_assignments"] == []
    assert body["attempts"]
    assert body["attempts"][0]["attempt_label"]
    assert body["attempts"][0]["pass_number"] == 1
    assert body["objective_values"]
    assert all(item["name"] for item in body["objective_values"])
    assert all(isinstance(item["proven_optimal"], bool) for item in body["objective_values"])


def test_shortage_is_successful_operational_result_with_warnings(
    client: TestClient,
) -> None:
    scenario = build_staffing_shortage_scenario(solver_time_limit_seconds=2)
    response = client.post("/api/v1/optimizations", json=_payload(scenario))

    assert response.status_code == 202
    body = completed_result(client, response)
    assert body["status"] in {"OPTIMAL", "FEASIBLE"}
    assert body["operational_readiness"] == "MANUAL_INTERVENTION_REQUIRED"
    assert body["status"] != body["operational_readiness"]
    assert body["flight_results"][0]["minimum_shortfall"] == 1
    assert body["warnings"]
    assert "MINIMUM_STAFFING_NOT_MET" in {item["code"] for item in body["warnings"]}


def test_mainline_express_boundary_is_preserved_in_api_output(
    client: TestClient,
) -> None:
    response = client.post(
        "/api/v1/optimizations",
        json={
            "operational_day": {
                "operational_date": "2035-04-15",
                "flights": [
                    {
                        "arrival_flight_number": "AA03000",
                        "arrival_time": "2035-04-15T08:00:00",
                    },
                    {
                        "departure_flight_number": "BB3001",
                        "departure_time": "2035-04-15T10:00:00",
                    },
                ],
            },
            "config": {"solver_time_limit_seconds": 1},
        },
    )

    assert response.status_code == 202
    assert [item["express"] for item in completed_result(client, response)["flight_results"]] == [
        False,
        True,
    ]


def test_no_usable_schedule_remains_an_honest_200_result(client: TestClient) -> None:
    scenario = small_ready_scenario(solver_time_limit_seconds=1e-9)

    response = client.post("/api/v1/optimizations", json=_payload(scenario))

    assert response.status_code == 202
    body = completed_result(client, response)
    assert body["status"] == "UNKNOWN"
    assert body["operational_readiness"] == "NO_USABLE_SCHEDULE"
    assert body["flight_results"] == []
    assert body["employee_results"] == []
    assert "NO_USABLE_SCHEDULE" in {item["code"] for item in body["warnings"]}


def test_emergency_lead_adoption_is_preserved(client: TestClient) -> None:
    scenario = build_emergency_lead_scenario(solver_time_limit_seconds=2)
    response = client.post("/api/v1/optimizations", json=_payload(scenario))

    assert response.status_code == 202
    body = completed_result(client, response)
    assert body["emergency_leads_enabled"] is True
    assert body["emergency_lead_staffing_used"] is True
    assert body["emergency_staffing_status"] == "LEAD_ASSISTED_SCHEDULE"
    assert body["emergency_pass_disposition"] == "ATTEMPTED_AND_ADOPTED"
    assert body["lead_assignments"][0]["employee_id"] == "EL01"
    assert len(body["attempts"]) == 2
    assert body["attempts"][1]["included_leads"] is True
    assert body["attempts"][1]["selected_as_final"] is True


def test_api_assignments_agree_between_flight_and_employee_views(
    client: TestClient,
) -> None:
    scenario = small_ready_scenario()
    body = completed_result(client, client.post("/api/v1/optimizations", json=_payload(scenario)))

    employees = {item["employee_id"]: item for item in body["employee_results"]}
    for flight_result in body["flight_results"]:
        identity = flight_result["flight"]
        for employee_id in flight_result["assigned_employee_ids"]:
            assert identity in employees[employee_id]["assigned_flights"]


def test_api_semantics_match_direct_engine_call_excluding_runtime(
    client: TestClient,
) -> None:
    scenario = small_ready_scenario()
    direct = optimize_flight_assignments(scenario.day, scenario.config)
    assert_result_invariants(scenario.day, scenario.config, direct)
    expected = optimization_result_to_response(direct).model_dump(mode="json")

    response = client.post("/api/v1/optimizations", json=_payload(scenario))

    assert response.status_code == 202
    assert _without_runtimes(completed_result(client, response)) == _without_runtimes(expected)


def test_unexpected_failures_are_sanitized(monkeypatch) -> None:
    app_module = importlib.import_module("ramp_optimizer_api.app")

    def fail(*_args, **_kwargs):
        raise RuntimeError("secret path C:\\private\\optimizer.py solver internals")

    monkeypatch.setattr(app_module.JobService, "submit", fail)
    client = TestClient(app_module.create_app(), raise_server_exceptions=False)
    response = client.post(
        "/api/v1/optimizations",
        json=_payload(small_ready_scenario()),
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "INTERNAL_SERVER_ERROR",
            "message": "The optimizer request could not be completed.",
            "details": [],
        }
    }
    assert "secret" not in response.text
    assert "private" not in response.text
