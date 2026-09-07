"""Focused Milestone 12 emergency-Lead second-pass tests."""

from dataclasses import replace
from datetime import date, datetime

import pytest

import ramp_optimizer.optimizer as optimizer_module
from ramp_optimizer import (
    BreakStatus,
    EmergencyLeadReason,
    EmergencyStaffingStatus,
    Employee,
    EmployeeShift,
    Flight,
    OperationalDay,
    OperationalRole,
    OptimizerConfig,
    Qualification,
    WarningCode,
    optimize_flight_assignments,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 2, hour, minute)


def arrival(number: str, hour: int = 9, minute: int = 0, *, heavy=False) -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=at(hour, minute),
        heavy=heavy,
    )


def departure(number: str = "101", hour: int = 9) -> Flight:
    return Flight(departure_flight_number=number, departure_time=at(hour))


def turn(number: str = "201") -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=at(8),
        departure_flight_number=str(int(number) + 1),
        departure_time=at(9),
    )


def employee(
    employee_id: str,
    qualifications: frozenset[Qualification] | None = None,
    *,
    enabled: bool = True,
) -> Employee:
    return Employee(
        employee_id,
        f"Employee {employee_id}",
        qualifications=(
            frozenset({Qualification.PUSH, Qualification.CLOSE_OUT})
            if qualifications is None
            else qualifications
        ),
        enabled=enabled,
    )


def shift(
    employee_id: str,
    role: OperationalRole = OperationalRole.RAMP_AGENT,
    *,
    start: datetime = datetime(2026, 9, 2, 5),
    end: datetime = datetime(2026, 9, 2, 18),
) -> EmployeeShift:
    return EmployeeShift(employee_id, start, end, role)


def day_for(
    flights: tuple[Flight, ...],
    ramps: tuple[Employee, ...],
    leads: tuple[Employee, ...] = (),
    *,
    lead_shifts: tuple[EmployeeShift, ...] | None = None,
) -> OperationalDay:
    return OperationalDay(
        date(2026, 9, 2),
        employees=ramps + leads,
        employee_shifts=(
            tuple(shift(worker.employee_id) for worker in ramps)
            + (
                lead_shifts
                if lead_shifts is not None
                else tuple(
                    shift(worker.employee_id, OperationalRole.RAMP_LEAD)
                    for worker in leads
                )
            )
        ),
        flights=flights,
    )


def enabled_config(**changes) -> OptimizerConfig:
    return replace(
        OptimizerConfig(),
        allow_leads_for_minimum_staffing=True,
        solver_time_limit_seconds=5.0,
        **changes,
    )


def assigned_leads(result) -> tuple[str, ...]:
    return tuple(item.employee_id for item in result.lead_assignments)


def test_leads_disabled_leave_critical_shortage() -> None:
    day = day_for(
        (arrival("101"),),
        (employee("R1"), employee("R2")),
        (employee("L1"),),
    )

    result = optimize_flight_assignments(day)

    assert result.flight_results[0].staffing_count == 2
    assert assigned_leads(result) == ()
    assert len(result.attempts) == 1
    assert result.emergency_leads_enabled is False
    assert result.emergency_staffing_status is (
        EmergencyStaffingStatus.CRITICAL_SHORTAGE_REMAINS
    )


def test_no_shortage_short_circuits_pass_two(monkeypatch) -> None:
    workers = tuple(employee(f"R{i}") for i in range(3))
    lead = employee("L1")
    day = day_for((departure(),), workers, (lead,))
    original = optimizer_module._run_optimization_pass
    calls: list[bool] = []

    def tracked(*args, **kwargs):
        calls.append(kwargs["include_leads"])
        return original(*args, **kwargs)

    monkeypatch.setattr(optimizer_module, "_run_optimization_pass", tracked)
    result = optimize_flight_assignments(day, enabled_config())

    assert calls == [False]
    assert len(result.attempts) == 1
    assert result.emergency_leads_enabled is True
    assert result.attempts[0].lead_assignments == 0
    assert result.emergency_staffing_status is (
        EmergencyStaffingStatus.NORMAL_SCHEDULE
    )


def test_lead_recovers_minimum_staffing() -> None:
    day = day_for(
        (arrival("101"),),
        (employee("R1"), employee("R2")),
        (employee("L1"),),
    )

    result = optimize_flight_assignments(day, enabled_config())

    assert result.flight_results[0].staffing_count == 3
    assert assigned_leads(result) == ("L1",)
    assert result.lead_assignments[0].reasons == (
        EmergencyLeadReason.MINIMUM_STAFFING,
    )
    assert [attempt.critical_shortage_count for attempt in result.attempts] == [
        1,
        0,
    ]
    assert result.emergency_staffing_status is (
        EmergencyStaffingStatus.LEAD_ASSISTED_SCHEDULE
    )


@pytest.mark.parametrize(
    ("flight", "ramp_count", "expected_count"),
    [(arrival("101"), 3, 3), (arrival("201", heavy=True), 4, 4)],
)
def test_preferred_staffing_shortfall_does_not_trigger_lead(
    flight: Flight, ramp_count: int, expected_count: int
) -> None:
    ramps = tuple(employee(f"R{i}") for i in range(ramp_count))
    result = optimize_flight_assignments(
        day_for((flight,), ramps, (employee("L1"),)), enabled_config()
    )

    assert result.flight_results[0].staffing_count == expected_count
    assert result.flight_results[0].preferred_met is False
    assert assigned_leads(result) == ()
    assert len(result.attempts) == 1


@pytest.mark.parametrize(
    ("flight", "ramp_qualifications", "lead_qualifications", "reason"),
    [
        (
            departure(),
            frozenset({Qualification.CLOSE_OUT}),
            frozenset({Qualification.PUSH}),
            EmergencyLeadReason.PUSH_QUALIFICATION,
        ),
        (
            turn(),
            frozenset({Qualification.PUSH}),
            frozenset({Qualification.CLOSE_OUT}),
            EmergencyLeadReason.CLOSE_QUALIFICATION,
        ),
    ],
)
def test_lead_recovers_one_missing_qualification(
    flight: Flight,
    ramp_qualifications: frozenset[Qualification],
    lead_qualifications: frozenset[Qualification],
    reason: EmergencyLeadReason,
) -> None:
    ramps = tuple(employee(f"R{i}", ramp_qualifications) for i in range(3))
    result = optimize_flight_assignments(
        day_for((flight,), ramps, (employee("L1", lead_qualifications),)),
        enabled_config(),
    )

    solved = result.flight_results[0]
    assert solved.push_covered and solved.close_covered
    assert assigned_leads(result) == ("L1",)
    assert reason in result.lead_assignments[0].reasons
    assert WarningCode.LEAD_QUALIFICATION_REQUIRED in {
        warning.code for warning in result.warnings
    }


def test_one_dual_qualified_lead_covers_both_requirements() -> None:
    ramps = tuple(employee(f"R{i}", frozenset()) for i in range(3))
    result = optimize_flight_assignments(
        day_for((departure(),), ramps, (employee("L1"),)), enabled_config()
    )

    assert assigned_leads(result) == ("L1",)
    assert result.lead_assignments[0].reasons == (
        EmergencyLeadReason.PUSH_QUALIFICATION,
        EmergencyLeadReason.CLOSE_QUALIFICATION,
    )


@pytest.mark.parametrize("lead_enabled", [True, False])
def test_unqualified_or_disabled_lead_cannot_repair_close_coverage(
    lead_enabled: bool,
) -> None:
    ramps = tuple(
        employee(f"R{i}", frozenset({Qualification.PUSH})) for i in range(3)
    )
    lead = employee(
        "L1",
        frozenset({Qualification.PUSH}) if lead_enabled else None,
        enabled=lead_enabled,
    )
    result = optimize_flight_assignments(
        day_for((departure(),), ramps, (lead,)), enabled_config()
    )

    assert result.flight_results[0].close_covered is False
    assert assigned_leads(result) == ()
    assert result.emergency_staffing_status is (
        EmergencyStaffingStatus.CRITICAL_SHORTAGE_REMAINS
    )


def test_lead_outside_shift_cannot_be_assigned() -> None:
    ramps = tuple(employee(f"R{i}") for i in range(2))
    lead = employee("L1")
    result = optimize_flight_assignments(
        day_for(
            (arrival("101"),),
            ramps,
            (lead,),
            lead_shifts=(
                shift(
                    "L1",
                    OperationalRole.RAMP_LEAD,
                    start=at(12),
                    end=at(18),
                ),
            ),
        ),
        enabled_config(),
    )

    assert assigned_leads(result) == ()
    assert result.flight_results[0].staffing_count == 2


def test_overlapping_shortages_allow_only_one_lead_assignment() -> None:
    flights = (arrival("101"), arrival("102"))
    ramps = tuple(employee(f"R{i}") for i in range(4))
    result = optimize_flight_assignments(
        day_for(flights, ramps, (employee("L1"),)), enabled_config()
    )

    assert sorted(flight.staffing_count for flight in result.flight_results) == [2, 3]
    assert len(result.lead_assignments) == 1
    assert result.emergency_staffing_status is (
        EmergencyStaffingStatus.CRITICAL_SHORTAGE_REMAINS
    )


def test_lead_assignment_objective_uses_one_of_two_available_leads() -> None:
    ramps = (employee("R1"), employee("R2"))
    leads = (employee("L1"), employee("L2"))
    result = optimize_flight_assignments(
        day_for((arrival("101"),), ramps, leads), enabled_config()
    )

    assert len(result.lead_assignments) == 1
    assert result.attempts[1].lead_candidate_count == 2
    assert result.attempts[1].lead_assignments == 1
    assert result.objective_values[6].name == "total_emergency_lead_assignments"
    assert result.objective_values[6].value == 1
    assert len(result.objective_values) == 18


@pytest.mark.parametrize("scenario", ["fairness", "continuity", "streak"])
def test_noncritical_downstream_preferences_never_trigger_lead(scenario: str) -> None:
    del scenario
    flights = (arrival("101", 9), arrival("102", 10))
    ramps = tuple(employee(f"R{i}") for i in range(3))
    result = optimize_flight_assignments(
        day_for(flights, ramps, (employee("L1"),)), enabled_config()
    )

    assert assigned_leads(result) == ()
    assert len(result.attempts) == 1


@pytest.mark.parametrize(
    ("flight", "ramp_count", "maximum"),
    [(departure(), 4, 4), (replace(departure("202"), heavy=True), 5, 5)],
)
def test_qualification_recovery_respects_maximum_staffing(
    flight: Flight, ramp_count: int, maximum: int
) -> None:
    ramps = tuple(employee(f"R{i}", frozenset()) for i in range(ramp_count))
    result = optimize_flight_assignments(
        day_for((flight,), ramps, (employee("L1"),)), enabled_config()
    )

    assert result.flight_results[0].staffing_count == maximum
    assert len(result.lead_assignments) == 1


def test_best_partial_schedule_is_returned_when_leads_are_insufficient() -> None:
    result = optimize_flight_assignments(
        day_for(
            (arrival("101"),),
            (employee("R1"),),
            (employee("L1"),),
        ),
        enabled_config(),
    )

    assert result.flight_results[0].staffing_count == 2
    assert assigned_leads(result) == ("L1",)
    assert result.emergency_staffing_status is (
        EmergencyStaffingStatus.CRITICAL_SHORTAGE_REMAINS
    )
    assert WarningCode.CRITICAL_SHORTAGE_REMAINS in {
        warning.code for warning in result.warnings
    }


def test_necessary_multi_flight_lead_obeys_breaks_and_is_excluded_from_fairness_and_continuity() -> None:
    flights = (arrival("101", 9), arrival("102", 10))
    ramps = (employee("R1"), employee("R2"))
    result = optimize_flight_assignments(
        day_for(flights, ramps, (employee("L1"),)), enabled_config()
    )

    lead_result = next(
        item for item in result.employee_results if item.employee_id == "L1"
    )
    assert lead_result.flight_count == 2
    assert lead_result.break_status is BreakStatus.SATISFIED
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 2
    assert result.fairness_metrics.total_assignments == 4
    assert result.continuity_metrics is not None
    assert result.continuity_metrics.total_retained_employee_transitions == 2


def test_unused_lead_in_second_pass_reports_not_applicable_break() -> None:
    ramps = (employee("R1"), employee("R2"))
    result = optimize_flight_assignments(
        day_for(
            (arrival("101"),),
            ramps,
            (employee("L1"), employee("L2")),
        ),
        enabled_config(),
    )

    unused_lead = next(
        item
        for item in result.employee_results
        if item.employee_id not in assigned_leads(result)
        and item.employee_id.startswith("L")
    )
    assert unused_lead.break_status is BreakStatus.NOT_APPLICABLE
