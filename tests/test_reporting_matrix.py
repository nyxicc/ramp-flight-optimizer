"""Operational status, warning, attempt, and formatter matrix."""

from dataclasses import replace
from datetime import date, timedelta

import pytest
from ortools.sat.python import cp_model

import ramp_optimizer.optimizer as optimizer_module
from ramp_optimizer import (
    EmergencyPassDisposition,
    EmergencyStaffingStatus,
    FixedAssignment,
    OperationalDay,
    OperationalReadinessStatus,
    OptimizationResult,
    OptimizationStatus,
    OptimizerConfig,
    WarningCode,
    WarningSeverity,
    format_optimization_report,
    optimize_flight_assignments,
)
from tests.scenario_builders import (
    SyntheticScenario,
    arrival,
    at,
    operational_day,
    optimizer_config,
    small_ready_scenario,
    small_shortage_scenario,
    synthetic_employee,
    synthetic_shift,
)


def _warning_codes(result: OptimizationResult) -> tuple[WarningCode, ...]:
    return tuple(warning.code for warning in result.warnings)


def _one_employee_two_flights(gap_minutes: int) -> SyntheticScenario:
    first = arrival("FX101", at(8, 10))
    second = arrival(
        "FX102", at(8, 40) + timedelta(minutes=gap_minutes)
    )
    worker = synthetic_employee("R001")
    return SyntheticScenario(
        operational_day(
            employees=(worker,),
            shifts=(synthetic_shift("R001", start=at(7), end=at(11)),),
            flights=(first, second),
            fixed=(
                FixedAssignment("R001", first),
                FixedAssignment("R001", second),
            ),
        ),
        optimizer_config(
            minimum_staff=1,
            normal_preferred_staff=1,
            heavy_preferred_staff=1,
            solver_time_limit_seconds=2.0,
        ),
    )


@pytest.mark.parametrize(
    (
        "case",
        "expected_readiness",
        "expected_emergency",
        "expected_disposition",
        "expected_codes",
        "expected_attempts",
    ),
    [
        (
            "empty",
            OperationalReadinessStatus.READY,
            EmergencyStaffingStatus.NORMAL_SCHEDULE,
            EmergencyPassDisposition.NOT_ENABLED,
            (),
            1,
        ),
        (
            "ready",
            OperationalReadinessStatus.READY,
            EmergencyStaffingStatus.NORMAL_SCHEDULE,
            EmergencyPassDisposition.NOT_ENABLED,
            (),
            1,
        ),
        (
            "shortage",
            OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED,
            EmergencyStaffingStatus.CRITICAL_SHORTAGE_REMAINS,
            EmergencyPassDisposition.NOT_ENABLED,
            (
                WarningCode.MINIMUM_STAFFING_NOT_MET,
                WarningCode.MANUAL_INTERVENTION_REQUIRED,
            ),
            1,
        ),
        (
            "emergency-unnecessary",
            OperationalReadinessStatus.READY,
            EmergencyStaffingStatus.NORMAL_SCHEDULE,
            EmergencyPassDisposition.NOT_NEEDED,
            (),
            1,
        ),
        (
            "emergency-adopted",
            OperationalReadinessStatus.READY_WITH_WARNINGS,
            EmergencyStaffingStatus.LEAD_ASSISTED_SCHEDULE,
            EmergencyPassDisposition.ATTEMPTED_AND_ADOPTED,
            (WarningCode.EMERGENCY_LEAD_USED,),
            2,
        ),
        (
            "break-failure",
            OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED,
            EmergencyStaffingStatus.NORMAL_SCHEDULE,
            EmergencyPassDisposition.NOT_ENABLED,
            (WarningCode.REQUIRED_BREAK_NOT_MET,),
            1,
        ),
    ],
)
def test_warning_and_readiness_matrix(
    case: str,
    expected_readiness: OperationalReadinessStatus,
    expected_emergency: EmergencyStaffingStatus,
    expected_disposition: EmergencyPassDisposition,
    expected_codes: tuple[WarningCode, ...],
    expected_attempts: int,
) -> None:
    if case == "empty":
        scenario = SyntheticScenario(
            OperationalDay(date(2026, 9, 2)), OptimizerConfig()
        )
    elif case == "ready":
        scenario = small_ready_scenario()
    elif case == "shortage":
        scenario = small_shortage_scenario(leads_enabled=False)
    elif case == "emergency-unnecessary":
        scenario = small_ready_scenario(leads_enabled=True)
    elif case == "emergency-adopted":
        scenario = small_shortage_scenario(leads_enabled=True)
    else:
        scenario = _one_employee_two_flights(29)

    result = optimize_flight_assignments(scenario.day, scenario.config)

    assert result.status is OptimizationStatus.OPTIMAL
    assert result.operational_readiness is expected_readiness
    assert result.emergency_staffing_status is expected_emergency
    assert result.emergency_pass_disposition is expected_disposition
    assert _warning_codes(result) == expected_codes
    assert len(result.attempts) == expected_attempts
    assert sum(attempt.selected_as_final for attempt in result.attempts) == 1
    assert result.schedule_summary is not None
    assert result.schedule_summary.warning_count == len(expected_codes)
    report = format_optimization_report(result)
    assert expected_readiness.value in report
    assert expected_disposition.value in report
    if expected_codes:
        assert all(code.value in report for code in expected_codes)
    else:
        assert "Warnings:\n- None" in report


def test_adopted_emergency_with_remaining_shortage_matrix_entry() -> None:
    scenario = operational_day(
        employees=(synthetic_employee("R001"), synthetic_employee("L001")),
        shifts=(
            synthetic_shift("R001", start=at(7), end=at(11)),
            synthetic_shift(
                "L001",
                start=at(7),
                end=at(11),
                role=optimizer_module.OperationalRole.RAMP_LEAD,
            ),
        ),
        flights=(arrival("FX101", at(9)),),
    )
    result = optimize_flight_assignments(
        scenario,
        optimizer_config(
            allow_leads_for_minimum_staffing=True,
            solver_time_limit_seconds=2.0,
        ),
    )

    assert result.status is OptimizationStatus.OPTIMAL
    assert result.operational_readiness is (
        OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED
    )
    assert result.emergency_staffing_status is (
        EmergencyStaffingStatus.CRITICAL_SHORTAGE_REMAINS
    )
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_ADOPTED_WITH_REMAINING_SHORTAGE
    )
    assert _warning_codes(result) == (
        WarningCode.MINIMUM_STAFFING_NOT_MET,
        WarningCode.EMERGENCY_LEAD_USED,
        WarningCode.MANUAL_INTERVENTION_REQUIRED,
    )
    assert [attempt.selected_as_final for attempt in result.attempts] == [False, True]


def test_feasible_not_optimal_matrix_entry_reports_computational_uncertainty(
    monkeypatch,
) -> None:
    scenario = small_ready_scenario()
    real_solver = cp_model.CpSolver
    calls = 0

    class UnknownSolver:
        def __init__(self) -> None:
            self.parameters = type("Parameters", (), {})()

        def solve(self, model):
            return cp_model.UNKNOWN

    def solver_factory():
        nonlocal calls
        calls += 1
        return real_solver() if calls == 1 else UnknownSolver()

    monkeypatch.setattr(optimizer_module.cp_model, "CpSolver", solver_factory)
    result = optimize_flight_assignments(scenario.day, scenario.config)

    assert result.status is OptimizationStatus.FEASIBLE
    assert result.operational_readiness is (
        OperationalReadinessStatus.READY_WITH_WARNINGS
    )
    assert result.objective_values[0].proven_optimal
    assert not result.objective_values[1].proven_optimal
    assert _warning_codes(result) == (
        WarningCode.SOLVER_RESULT_NOT_PROVEN_OPTIMAL,
    )
    assert result.schedule_summary is not None
    assert not result.schedule_summary.all_objectives_proven_optimal
    assert "all objectives proven optimal: no" in format_optimization_report(result)


def test_attempted_rejected_matrix_entry_keeps_pass_one_and_reports_disposition(
    monkeypatch,
) -> None:
    scenario = small_shortage_scenario(leads_enabled=True)
    pass_one, _ = optimizer_module._run_optimization_pass(
        scenario.day,
        scenario.config,
        include_leads=False,
        critical_needs=(),
    )
    calls = 0

    def tied_pass(*args, **kwargs):
        nonlocal calls
        calls += 1
        return pass_one, 0

    monkeypatch.setattr(optimizer_module, "_run_optimization_pass", tied_pass)
    result = optimize_flight_assignments(scenario.day, scenario.config)

    assert calls == 2
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_NOT_ADOPTED_NO_IMPROVEMENT
    )
    assert [attempt.selected_as_final for attempt in result.attempts] == [True, False]
    assert WarningCode.EMERGENCY_RECOVERY_NOT_ADOPTED in _warning_codes(result)
    assert result.lead_assignments == ()
    assert "ATTEMPTED_NOT_ADOPTED_NO_IMPROVEMENT" in format_optimization_report(result)


def test_no_usable_schedule_matrix_entry_is_explicit(monkeypatch) -> None:
    scenario = small_ready_scenario(leads_enabled=True)
    unusable = OptimizationResult(
        status=OptimizationStatus.UNKNOWN,
        flight_results=(),
        employee_results=(),
        fairness_metrics=None,
        continuity_metrics=None,
        attempts=(),
        objective_values=(),
    )
    monkeypatch.setattr(
        optimizer_module,
        "_run_optimization_pass",
        lambda *args, **kwargs: (unusable, 0),
    )

    result = optimize_flight_assignments(scenario.day, scenario.config)

    assert result.status is OptimizationStatus.UNKNOWN
    assert result.operational_readiness is (
        OperationalReadinessStatus.NO_USABLE_SCHEDULE
    )
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.NOT_ATTEMPTED_NO_USABLE_SCHEDULE
    )
    assert _warning_codes(result) == (WarningCode.NO_USABLE_SCHEDULE,)
    assert result.warnings[0].severity is WarningSeverity.CRITICAL
    report = format_optimization_report(result)
    assert "Schedule summary:" not in report
    assert "Fairness: unavailable" in report
    assert "Continuity: unavailable" in report
