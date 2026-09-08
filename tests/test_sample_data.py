"""Public fictional scenario verification for the Phase 1 release."""

import ast
from pathlib import Path

import pytest

from ramp_optimizer import (
    EmergencyPassDisposition,
    OperationalReadinessStatus,
    WarningCode,
    build_emergency_lead_scenario,
    build_normal_scenario,
    build_sample_scenario,
    build_staffing_shortage_scenario,
    optimize_flight_assignments,
    validate_operational_day,
)
from tests.invariant_checks import assert_result_invariants


@pytest.mark.parametrize(
    "builder",
    [
        build_normal_scenario,
        build_staffing_shortage_scenario,
        build_emergency_lead_scenario,
    ],
)
def test_public_scenarios_are_valid_aware_and_deterministic(builder) -> None:
    first = builder(solver_time_limit_seconds=5.0)
    second = builder(solver_time_limit_seconds=5.0)

    assert first == second
    assert validate_operational_day(first.day, first.config) == ()
    datetimes = (
        *(shift.start for shift in first.day.employee_shifts),
        *(shift.end for shift in first.day.employee_shifts),
        *(
            value
            for flight in first.day.flights
            for value in (flight.arrival_time, flight.departure_time)
            if value is not None
        ),
    )
    assert datetimes
    assert all(value.tzinfo is not None for value in datetimes)
    assert all("Fictional" in employee.name for employee in first.day.employees)


def test_normal_scenario_is_ready_without_emergency_recovery() -> None:
    scenario = build_normal_scenario(solver_time_limit_seconds=5.0)
    result = optimize_flight_assignments(scenario.day, scenario.config)

    assert_result_invariants(scenario.day, scenario.config, result)
    assert result.operational_readiness is OperationalReadinessStatus.READY
    assert result.emergency_pass_disposition is EmergencyPassDisposition.NOT_ENABLED
    assert result.lead_assignments == ()
    assert result.warnings == ()


def test_shortage_scenario_returns_a_useful_warned_partial_schedule() -> None:
    scenario = build_staffing_shortage_scenario(solver_time_limit_seconds=5.0)
    result = optimize_flight_assignments(scenario.day, scenario.config)

    assert_result_invariants(scenario.day, scenario.config, result)
    assert result.operational_readiness is (
        OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED
    )
    assert result.flight_results[0].staffing_count == 2
    assert WarningCode.MINIMUM_STAFFING_NOT_MET in {
        warning.code for warning in result.warnings
    }


def test_emergency_scenario_reports_adopted_lead_recovery() -> None:
    scenario = build_emergency_lead_scenario(solver_time_limit_seconds=5.0)
    result = optimize_flight_assignments(scenario.day, scenario.config)

    assert_result_invariants(scenario.day, scenario.config, result)
    assert result.operational_readiness is (
        OperationalReadinessStatus.READY_WITH_WARNINGS
    )
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_AND_ADOPTED
    )
    assert len(result.lead_assignments) == 1
    assert result.lead_assignments[0].employee_id == "EL01"
    assert {warning.code for warning in result.warnings} == {
        WarningCode.EMERGENCY_LEAD_USED
    }


def test_named_builder_rejects_unknown_scenario() -> None:
    with pytest.raises(ValueError, match="unknown sample scenario"):
        build_sample_scenario("not-a-scenario")


def test_installable_package_modules_never_import_tests() -> None:
    package_root = Path(__file__).parents[1] / "src" / "ramp_optimizer"
    violations: list[tuple[str, str]] = []
    for path in sorted(package_root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = (node.module,)
            else:
                continue
            for name in names:
                if name == "tests" or name.startswith("tests."):
                    violations.append((path.name, name))
    assert violations == []
