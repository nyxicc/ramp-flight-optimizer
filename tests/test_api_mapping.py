"""Pure mapping and deterministic serialization tests."""

from dataclasses import fields, replace
from datetime import date, datetime, timedelta, timezone

from pydantic import ValidationError
import pytest

from ramp_optimizer import (
    ContinuityMetrics,
    ContinuityTransitionResult,
    EmergencyLeadAssignmentResult,
    Employee,
    EmployeeScheduleResult,
    EmployeeShift,
    FairnessMetrics,
    FixedAssignment,
    Flight,
    FlightAssignmentResult,
    ObjectiveValue,
    OperationalDay,
    OperationalRole,
    OptimizationAttemptSummary,
    OptimizationResult,
    OptimizerConfig,
    Qualification,
    ScheduleSummary,
    ScheduleWarning,
    optimize_flight_assignments,
)
from ramp_optimizer.benchmarking import build_benchmark_scenarios
from ramp_optimizer.sample_data import build_emergency_lead_scenario
from ramp_optimizer_api.mapping import (
    config_to_domain,
    employee_to_domain,
    employee_to_request,
    map_optimization_request,
    operational_day_to_request,
    optimization_result_to_response,
)
from ramp_optimizer_api.schemas import (
    ContinuityMetricsResponse,
    ContinuityTransitionResponse,
    EmergencyLeadAssignmentResponse,
    EmployeeRequest,
    EmployeeScheduleResponse,
    EmployeeShiftRequest,
    FairnessMetricsResponse,
    FlightAssignmentResponse,
    FlightResponse,
    ObjectiveValueResponse,
    OptimizationAttemptResponse,
    OptimizationRequest,
    OptimizationResponse,
    OptimizerConfigRequest,
    ScheduleSummaryResponse,
    ScheduleWarningResponse,
)


def _field_names(value_type) -> set[str]:
    return {item.name for item in fields(value_type)}


def test_response_models_cover_every_public_result_field() -> None:
    pairs = (
        (OptimizationResult, OptimizationResponse),
        (Flight, FlightResponse),
        (FlightAssignmentResult, FlightAssignmentResponse),
        (EmployeeScheduleResult, EmployeeScheduleResponse),
        (FairnessMetrics, FairnessMetricsResponse),
        (ContinuityTransitionResult, ContinuityTransitionResponse),
        (ContinuityMetrics, ContinuityMetricsResponse),
        (ObjectiveValue, ObjectiveValueResponse),
        (OptimizationAttemptSummary, OptimizationAttemptResponse),
        (EmergencyLeadAssignmentResult, EmergencyLeadAssignmentResponse),
        (ScheduleSummary, ScheduleSummaryResponse),
        (ScheduleWarning, ScheduleWarningResponse),
    )

    for domain_type, response_type in pairs:
        assert set(response_type.model_fields) == _field_names(domain_type)


def test_config_schema_covers_domain_config_and_uses_domain_defaults() -> None:
    request = OptimizerConfigRequest()

    assert set(OptimizerConfigRequest.model_fields) == _field_names(OptimizerConfig)
    assert config_to_domain(request) == OptimizerConfig()

    supplied = OptimizerConfigRequest(
        arrival_preparation_minutes=12,
        arrival_offload_minutes=21,
        departure_work_minutes=48,
        minimum_staff=2,
        normal_preferred_staff=3,
        heavy_preferred_staff=4,
        required_break_minutes=25,
        consecutive_reset_minutes=35,
        continuity_horizon_minutes=90,
        express_threshold=2999,
        express_workload_factor=0.75,
        three_person_workload_multiplier=1.2,
        workload_scale=20,
        allow_leads_for_minimum_staffing=True,
        allow_trainees_for_assignments=True,
        allow_possible_ramp_support_for_assignments=True,
        solver_time_limit_seconds=5.5,
        solver_random_seed=7,
        solver_num_search_workers=2,
    )
    mapped = config_to_domain(supplied)
    assert all(
        getattr(mapped, item.name) == getattr(supplied, item.name)
        for item in fields(OptimizerConfig)
    )


def test_employee_qualification_conversion_is_immutable_and_deterministic() -> None:
    request = EmployeeRequest(
        employee_id="A001",
        name="Example Agent",
        qualifications=(Qualification.PUSH, Qualification.CLOSE_OUT),
    )

    employee = employee_to_domain(request)
    assert employee.qualifications == frozenset(
        {Qualification.PUSH, Qualification.CLOSE_OUT}
    )
    assert employee_to_request(employee).model_dump(mode="json")["qualifications"] == [
        "CLOSE_OUT",
        "PUSH",
    ]


def test_datetime_and_flight_shapes_round_trip_without_timezone_repair() -> None:
    offset = timezone(timedelta(hours=-5))
    aware = datetime(2035, 4, 15, 8, 30, tzinfo=offset)
    naive = datetime(2035, 4, 15, 9, 45)
    day = OperationalDay(
        date(2035, 4, 15),
        employees=(Employee("A001", "Agent"),),
        employee_shifts=(
            EmployeeShift("A001", aware, aware + timedelta(hours=2), OperationalRole.RAMP_AGENT),
        ),
        flights=(
            Flight(arrival_flight_number="AA100", arrival_time=aware, gate="A1"),
            Flight(departure_flight_number="AA200", departure_time=aware, heavy=True),
            Flight(
                arrival_flight_number="AA300",
                arrival_time=naive,
                departure_flight_number="AA301",
                departure_time=naive + timedelta(hours=1),
                gate="C3",
                heavy=True,
            ),
        ),
    )

    request = operational_day_to_request(day)
    reparsed = OptimizationRequest.model_validate_json(request.model_dump_json())
    mapped = map_optimization_request(reparsed).operational_day

    assert mapped == day
    assert mapped.employee_shifts[0].start.utcoffset() == timedelta(hours=-5)
    assert mapped.flights[2].arrival_time is not None
    assert mapped.flights[2].arrival_time.tzinfo is None
    assert mapped.flights[0].gate == "A1"
    assert mapped.flights[1].heavy is True


def test_fixed_references_use_canonical_directional_identity() -> None:
    timestamp = datetime(2035, 4, 15, 9)
    flights = (
        Flight(arrival_flight_number="AA00123", arrival_time=timestamp),
        Flight(departure_flight_number="BB123", departure_time=timestamp),
        Flight(
            arrival_flight_number="CC03001",
            arrival_time=timestamp,
            departure_flight_number="CC03002",
            departure_time=timestamp + timedelta(hours=1),
        ),
    )
    day = OperationalDay(
        date(2035, 4, 15),
        flights=flights,
        fixed_assignments=(
            FixedAssignment("A1", flights[0]),
            FixedAssignment("A2", flights[1]),
            FixedAssignment("A3", flights[2]),
        ),
    )
    request = operational_day_to_request(day)
    payload = request.model_dump(mode="json")
    payload["operational_day"]["fixed_assignments"][0]["flight"][
        "arrival_flight_number"
    ] = "zz123"
    payload["operational_day"]["fixed_assignments"][1]["flight"][
        "departure_flight_number"
    ] = "000123"
    payload["operational_day"]["fixed_assignments"][2]["flight"] = {
        "arrival_flight_number": "3001",
        "departure_flight_number": "ZZ03002",
    }

    mapped = map_optimization_request(OptimizationRequest.model_validate(payload))

    assert mapped.issues == ()
    assert tuple(item.flight for item in mapped.operational_day.fixed_assignments) == flights


def test_missing_ambiguous_and_empty_fixed_references_are_structured_issues() -> None:
    timestamp = datetime(2035, 4, 15, 9)
    payload = {
        "operational_day": {
            "operational_date": "2035-04-15",
            "flights": [
                {"arrival_flight_number": "AA123", "arrival_time": timestamp.isoformat()},
                {"arrival_flight_number": "BB00123", "arrival_time": timestamp.isoformat()},
            ],
            "fixed_assignments": [
                {"employee_id": "A1", "flight": {"arrival_flight_number": "123"}},
                {"employee_id": "A2", "flight": {"departure_flight_number": "999"}},
                {"employee_id": "A3", "flight": {}},
            ],
        }
    }

    mapped = map_optimization_request(OptimizationRequest.model_validate(payload))

    assert [item.code for item in mapped.issues] == [
        "AMBIGUOUS_FIXED_FLIGHT_REFERENCE",
        "UNRESOLVED_FIXED_FLIGHT_REFERENCE",
        "MISSING_FIXED_FLIGHT_REFERENCE",
    ]


def test_complete_nested_result_mapping_is_json_safe_and_ordered() -> None:
    scenario = build_benchmark_scenarios()[0]
    result = optimize_flight_assignments(scenario.day, scenario.config)
    response = optimization_result_to_response(result)
    serialized = response.model_dump(mode="json")

    assert serialized["status"] == result.status.value
    assert [item["stage"] for item in serialized["objective_values"]] == list(
        range(1, len(result.objective_values) + 1)
    )
    assert [item["code"] for item in serialized["warnings"]] == [
        item.code.value for item in result.warnings
    ]
    assert serialized["flight_results"][0]["flight"]["departure_time"].endswith(
        "-05:00"
    )
    assert serialized["fairness_metrics"] is not None
    assert serialized["continuity_metrics"] is not None
    assert serialized["attempts"]

    emergency = build_emergency_lead_scenario(solver_time_limit_seconds=2)
    emergency_response = optimization_result_to_response(
        optimize_flight_assignments(emergency.day, emergency.config)
    ).model_dump(mode="json")
    assert emergency_response["lead_assignments"]
    assert emergency_response["lead_assignments"][0]["reasons"]


def test_non_finite_result_values_are_rejected_before_json_serialization() -> None:
    scenario = build_benchmark_scenarios()[0]
    result = optimize_flight_assignments(scenario.day, scenario.config)

    with pytest.raises(ValidationError):
        optimization_result_to_response(
            replace(result, solver_runtime_seconds=float("inf"))
        )
