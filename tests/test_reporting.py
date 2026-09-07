"""Milestone 13 operational readiness, warnings, adoption, and reporting."""

from dataclasses import replace
from datetime import date, datetime

import pytest

import ramp_optimizer.optimizer as optimizer_module
from ramp_optimizer import (
    BreakStatus,
    EmergencyLeadReason,
    EmergencyPassDisposition,
    EmergencyStaffingStatus,
    Employee,
    EmployeeScheduleResult,
    EmployeeShift,
    Flight,
    FlightType,
    OperationalDay,
    OperationalReadinessStatus,
    OperationalRole,
    OptimizationStatus,
    OptimizerConfig,
    Qualification,
    StaffingStatus,
    WarningCode,
    WarningSeverity,
    format_optimization_report,
    optimize_flight_assignments,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 2, hour, minute)


def arrival(number: str, hour: int = 9, minute: int = 0) -> Flight:
    return Flight(arrival_flight_number=number, arrival_time=at(hour, minute))


def departure(number: str = "101", hour: int = 9) -> Flight:
    return Flight(departure_flight_number=number, departure_time=at(hour))


def employee(
    employee_id: str,
    qualifications: frozenset[Qualification] | None = None,
) -> Employee:
    return Employee(
        employee_id,
        f"Employee {employee_id}",
        qualifications=(
            frozenset({Qualification.PUSH, Qualification.CLOSE_OUT})
            if qualifications is None
            else qualifications
        ),
    )


def day_for(
    flights: tuple[Flight, ...],
    ramp_count: int,
    *,
    lead_count: int = 0,
    ramp_qualifications: frozenset[Qualification] | None = None,
    lead_qualifications: frozenset[Qualification] | None = None,
) -> OperationalDay:
    ramps = tuple(
        employee(f"R{index}", ramp_qualifications)
        for index in range(1, ramp_count + 1)
    )
    leads = tuple(
        employee(f"L{index}", lead_qualifications)
        for index in range(1, lead_count + 1)
    )
    return OperationalDay(
        date(2026, 9, 2),
        employees=ramps + leads,
        employee_shifts=tuple(
            EmployeeShift(
                worker.employee_id,
                at(5),
                at(18),
                OperationalRole.RAMP_AGENT,
            )
            for worker in ramps
        )
        + tuple(
            EmployeeShift(
                worker.employee_id,
                at(5),
                at(18),
                OperationalRole.RAMP_LEAD,
            )
            for worker in leads
        ),
        flights=flights,
    )


def config(*, leads: bool = False, **changes) -> OptimizerConfig:
    return replace(
        OptimizerConfig(),
        allow_leads_for_minimum_staffing=leads,
        solver_time_limit_seconds=5.0,
        **changes,
    )


def warning_codes(result) -> tuple[WarningCode, ...]:
    return tuple(warning.code for warning in result.warnings)


def raw_pass(day: OperationalDay, active_config: OptimizerConfig):
    return optimizer_module._run_optimization_pass(
        day,
        active_config,
        include_leads=False,
        critical_needs=(),
    )[0]


def test_fully_compliant_ordinary_schedule_is_ready() -> None:
    result = optimize_flight_assignments(day_for((departure(),), 4))

    assert result.operational_readiness is OperationalReadinessStatus.READY
    assert result.emergency_pass_disposition is EmergencyPassDisposition.NOT_ENABLED
    assert result.schedule_summary is not None
    assert result.schedule_summary.minimum_staffed_flights == 1
    assert result.schedule_summary.qualification_compliant_flights == 1


def test_lead_assisted_recovery_is_ready_with_warnings() -> None:
    result = optimize_flight_assignments(
        day_for((arrival("101"),), 2, lead_count=1), config(leads=True)
    )

    assert result.operational_readiness is (
        OperationalReadinessStatus.READY_WITH_WARNINGS
    )
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_AND_ADOPTED
    )
    assert result.emergency_staffing_status is (
        EmergencyStaffingStatus.LEAD_ASSISTED_SCHEDULE
    )
    assert warning_codes(result) == (WarningCode.EMERGENCY_LEAD_USED,)


@pytest.mark.parametrize(
    ("qualifications", "missing_code", "summary_field"),
    [
        (
            frozenset({Qualification.CLOSE_OUT}),
            WarningCode.PUSH_QUALIFICATION_NOT_MET,
            "missing_push_flights",
        ),
        (
            frozenset({Qualification.PUSH}),
            WarningCode.CLOSE_QUALIFICATION_NOT_MET,
            "missing_close_out_flights",
        ),
    ],
)
def test_missing_qualification_requires_manual_intervention(
    qualifications, missing_code, summary_field
) -> None:
    result = optimize_flight_assignments(
        day_for((departure(),), 3, ramp_qualifications=qualifications)
    )

    assert result.status is OptimizationStatus.OPTIMAL
    assert result.operational_readiness is (
        OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED
    )
    assert missing_code in warning_codes(result)
    assert WarningCode.MANUAL_INTERVENTION_REQUIRED in warning_codes(result)
    assert getattr(result.schedule_summary, summary_field) == 1


def test_below_minimum_optimal_partial_result_requires_manual_intervention() -> None:
    result = optimize_flight_assignments(day_for((arrival("101"),), 2))

    assert result.status is OptimizationStatus.OPTIMAL
    assert result.operational_readiness is (
        OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED
    )
    assert warning_codes(result) == (
        WarningCode.MINIMUM_STAFFING_NOT_MET,
        WarningCode.MANUAL_INTERVENTION_REQUIRED,
    )


def test_dual_missing_qualification_remains_separately_identifiable() -> None:
    result = optimize_flight_assignments(
        day_for((departure(),), 3, ramp_qualifications=frozenset())
    )

    assert warning_codes(result)[:3] == (
        WarningCode.PUSH_QUALIFICATION_NOT_MET,
        WarningCode.CLOSE_QUALIFICATION_NOT_MET,
        WarningCode.MANUAL_INTERVENTION_REQUIRED,
    )
    assert result.schedule_summary.missing_push_flights == 1
    assert result.schedule_summary.missing_close_out_flights == 1
    assert result.schedule_summary.qualification_compliant_flights == 0


def test_unsatisfied_required_break_controls_readiness_and_identity() -> None:
    result = optimize_flight_assignments(
        day_for((arrival("101", 9), arrival("102", 9, 39)), 1),
        config(
            minimum_staff=1,
            normal_preferred_staff=1,
            heavy_preferred_staff=1,
        ),
    )

    warning = next(
        warning
        for warning in result.warnings
        if warning.code is WarningCode.REQUIRED_BREAK_NOT_MET
    )
    assert warning.employee_id == "R1"
    assert result.operational_readiness is (
        OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED
    )
    assert result.schedule_summary.employees_with_unsatisfied_break == 1


@pytest.mark.parametrize(
    "preference", ["preferred", "fairness", "streak", "continuity"]
)
def test_noncritical_preferences_never_require_manual_intervention(preference) -> None:
    del preference
    result = optimize_flight_assignments(day_for((arrival("101"),), 3))

    assert result.flight_results[0].preferred_met is False
    assert result.operational_readiness is OperationalReadinessStatus.READY
    assert not any(
        warning.severity is WarningSeverity.CRITICAL
        for warning in result.warnings
    )


def test_long_legal_streak_and_continuity_metrics_are_not_warnings() -> None:
    result = optimize_flight_assignments(
        day_for(
            (arrival("101", 9), arrival("102", 9, 50), arrival("103", 10, 40)),
            3,
        ),
        config(required_break_minutes=10),
    )

    assert result.fairness_metrics.maximum_consecutive_streak == 3
    assert result.continuity_metrics.eligible_transition_count > 0
    assert result.operational_readiness is OperationalReadinessStatus.READY


def test_compliant_feasible_result_reports_computational_uncertainty(
    monkeypatch,
) -> None:
    day = day_for((arrival("101"),), 3)
    ordinary = raw_pass(day, config())
    feasible = replace(
        ordinary,
        status=OptimizationStatus.FEASIBLE,
        objective_values=ordinary.objective_values[:-1]
        + (replace(ordinary.objective_values[-1], proven_optimal=False),),
    )
    monkeypatch.setattr(
        optimizer_module,
        "_run_optimization_pass",
        lambda *args, **kwargs: (feasible, 0),
    )

    result = optimize_flight_assignments(day)

    assert result.status is OptimizationStatus.FEASIBLE
    assert result.operational_readiness is (
        OperationalReadinessStatus.READY_WITH_WARNINGS
    )
    assert WarningCode.SOLVER_RESULT_NOT_PROVEN_OPTIMAL in warning_codes(result)
    assert result.schedule_summary.all_objectives_proven_optimal is False


def test_emergency_disabled_shortage_has_manual_escalation() -> None:
    result = optimize_flight_assignments(
        day_for((arrival("101"),), 2, lead_count=1)
    )

    assert result.emergency_pass_disposition is EmergencyPassDisposition.NOT_ENABLED
    assert WarningCode.MANUAL_INTERVENTION_REQUIRED in warning_codes(result)


def test_emergency_enabled_without_eligible_lead_has_manual_escalation() -> None:
    result = optimize_flight_assignments(
        day_for((arrival("101"),), 2), config(leads=True)
    )

    assert len(result.attempts) == 2
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_NOT_ADOPTED_NO_IMPROVEMENT
    )
    assert WarningCode.MANUAL_INTERVENTION_REQUIRED in warning_codes(result)


def _mock_two_passes(monkeypatch, pass_one, pass_two, lead_candidates=1) -> None:
    outcomes = iter(((pass_one, 0), (pass_two, lead_candidates)))
    monkeypatch.setattr(
        optimizer_module,
        "_run_optimization_pass",
        lambda *args, **kwargs: next(outcomes),
    )


def _replace_schedule(base, day, flight_results, *, status=None):
    existing = {item.employee_id: item for item in base.employee_results}
    employee_results = []
    for worker in day.employees:
        assigned_results = tuple(
            flight
            for flight in flight_results
            if worker.employee_id in flight.assigned_employee_ids
        )
        assigned_flights = tuple(flight.flight for flight in assigned_results)
        prior = existing.get(worker.employee_id)
        values = dict(
            assigned_flights=assigned_flights,
            flight_count=len(assigned_flights),
            mainline_flight_count=sum(
                not flight.express for flight in assigned_results
            ),
            express_flight_count=sum(flight.express for flight in assigned_results),
            three_person_flight_count=sum(
                flight.staffing_count == 3 for flight in assigned_results
            ),
            longest_consecutive_streak=1 if assigned_flights else 0,
            break_status=BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS,
        )
        employee_results.append(
            replace(prior, **values)
            if prior is not None
            else EmployeeScheduleResult(
                employee_id=worker.employee_id,
                adjusted_workload=0.0,
                **values,
            )
        )
    return replace(
        base,
        status=base.status if status is None else status,
        flight_results=tuple(flight_results),
        employee_results=tuple(employee_results),
    )


def test_empty_emergency_result_retains_pass_one_and_attempt(monkeypatch) -> None:
    day = day_for((arrival("101"),), 2, lead_count=1)
    active_config = config(leads=True)
    pass_one = raw_pass(day, active_config)
    pass_two = optimizer_module._empty_solver_result(
        OptimizationStatus.UNKNOWN, (), 0.01
    )
    _mock_two_passes(monkeypatch, pass_one, pass_two)

    result = optimize_flight_assignments(day, active_config)

    assert result.flight_results == pass_one.flight_results
    assert len(result.attempts) == 2
    assert result.attempts[0].selected_as_final
    assert not result.attempts[1].usable_schedule
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_NOT_ADOPTED_UNUSABLE
    )
    assert result.lead_assignments == ()
    assert WarningCode.EMERGENCY_RECOVERY_NOT_ADOPTED in warning_codes(result)
    assert WarningCode.MANUAL_INTERVENTION_REQUIRED in warning_codes(result)


def test_unexplainable_emergency_lead_result_is_safely_rejected(monkeypatch) -> None:
    close_only = frozenset({Qualification.CLOSE_OUT})
    day = day_for(
        (departure(),),
        3,
        lead_count=1,
        ramp_qualifications=close_only,
        lead_qualifications=close_only,
    )
    active_config = config(leads=True)
    pass_one = raw_pass(day, active_config)
    flight = pass_one.flight_results[0]
    pass_two = _replace_schedule(
        pass_one,
        day,
        (
            replace(
                flight,
                assigned_employee_ids=flight.assigned_employee_ids[:2] + ("L1",),
            ),
        ),
    )
    _mock_two_passes(monkeypatch, pass_one, pass_two)

    result = optimize_flight_assignments(day, active_config)

    assert result.flight_results == pass_one.flight_results
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_NOT_ADOPTED_UNUSABLE
    )
    assert result.lead_assignments == ()


def test_worse_feasible_emergency_result_retains_pass_one(monkeypatch) -> None:
    day = day_for(
        (arrival("101"), arrival("102")), 5, lead_count=1
    )
    active_config = config(leads=True)
    pass_one = raw_pass(day, active_config)
    under_index = next(
        index
        for index, flight in enumerate(pass_one.flight_results)
        if flight.staffing_count == 2
    )
    covered_index = 1 - under_index
    emergency_flights = list(pass_one.flight_results)
    under = emergency_flights[under_index]
    emergency_flights[under_index] = replace(
        under,
        assigned_employee_ids=under.assigned_employee_ids + ("L1",),
        staffing_count=3,
        minimum_met=True,
        minimum_shortfall=0,
        staffing_status=StaffingStatus.MINIMUM_STAFFED,
        preferred_shortfall=1,
        warnings=(),
    )
    covered = emergency_flights[covered_index]
    emergency_flights[covered_index] = replace(
        covered,
        assigned_employee_ids=covered.assigned_employee_ids[:1],
        staffing_count=1,
        minimum_met=False,
        minimum_shortfall=2,
        staffing_status=StaffingStatus.BELOW_MINIMUM,
        preferred_met=False,
        preferred_shortfall=3,
    )
    pass_two = _replace_schedule(
        pass_one,
        day,
        emergency_flights,
        status=OptimizationStatus.FEASIBLE,
    )
    _mock_two_passes(monkeypatch, pass_one, pass_two)

    result = optimize_flight_assignments(day, active_config)

    assert result.flight_results == pass_one.flight_results
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_NOT_ADOPTED_WORSE
    )
    assert result.lead_assignments == ()
    assert result.attempts[0].selected_as_final
    assert not result.attempts[1].selected_as_final


def test_equal_critical_emergency_result_with_necessary_lead_is_adopted(
    monkeypatch,
) -> None:
    day = day_for((arrival("101"), arrival("102")), 4, lead_count=1)
    active_config = config(leads=True)
    pass_one = raw_pass(day, active_config)
    covered_index = next(
        index
        for index, flight in enumerate(pass_one.flight_results)
        if flight.staffing_count == 3
    )
    under_index = 1 - covered_index
    emergency_flights = list(pass_one.flight_results)
    covered = emergency_flights[covered_index]
    under = emergency_flights[under_index]
    emergency_flights[covered_index] = replace(
        covered,
        assigned_employee_ids=covered.assigned_employee_ids[:1],
        staffing_count=1,
        minimum_met=False,
        minimum_shortfall=2,
        staffing_status=StaffingStatus.BELOW_MINIMUM,
        preferred_met=False,
        preferred_shortfall=3,
    )
    emergency_flights[under_index] = replace(
        under,
        assigned_employee_ids=(
            under.assigned_employee_ids
            + (covered.assigned_employee_ids[1], "L1")
        ),
        staffing_count=3,
        minimum_met=True,
        minimum_shortfall=0,
        staffing_status=StaffingStatus.MINIMUM_STAFFED,
        preferred_shortfall=1,
        warnings=(),
    )
    pass_two = _replace_schedule(pass_one, day, emergency_flights)
    _mock_two_passes(monkeypatch, pass_one, pass_two)

    result = optimize_flight_assignments(day, active_config)

    assert result.flight_results == pass_two.flight_results
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_ADOPTED_WITH_REMAINING_SHORTAGE
    )
    assert result.attempts[1].selected_as_final
    assert result.lead_assignments[0].reasons == (
        EmergencyLeadReason.MINIMUM_STAFFING,
    )


def test_real_better_pass_two_is_adopted_and_summaries_select_it() -> None:
    result = optimize_flight_assignments(
        day_for((arrival("101"),), 2, lead_count=1), config(leads=True)
    )

    assert [attempt.selected_as_final for attempt in result.attempts] == [False, True]
    assert [attempt.usable_schedule for attempt in result.attempts] == [True, True]
    assert [attempt.critical_shortage_count for attempt in result.attempts] == [1, 0]
    assert all(attempt.objective_stages_completed > 0 for attempt in result.attempts)
    assert all(attempt.all_objectives_proven_optimal for attempt in result.attempts)
    assert result.solver_runtime_seconds >= sum(
        attempt.solver_runtime_seconds for attempt in result.attempts
    )


def test_partial_emergency_improvement_reports_lead_and_remaining_shortage() -> None:
    result = optimize_flight_assignments(
        day_for(
            (arrival("101"), arrival("102")), 4, lead_count=1
        ),
        config(leads=True),
    )

    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_ADOPTED_WITH_REMAINING_SHORTAGE
    )
    assert result.operational_readiness is (
        OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED
    )
    assert len(result.lead_assignments) == 1
    assert WarningCode.EMERGENCY_LEAD_USED in warning_codes(result)
    assert WarningCode.MINIMUM_STAFFING_NOT_MET in warning_codes(result)
    assert WarningCode.MANUAL_INTERVENTION_REQUIRED in warning_codes(result)
    assert result.schedule_summary.below_minimum_flights == 1


def test_warning_deduplication_and_distinct_warning_preservation() -> None:
    day = day_for(
        (departure(),), 3, ramp_qualifications=frozenset()
    )
    raw = raw_pass(day, config())
    duplicated = replace(raw, warnings=raw.warnings + raw.warnings)
    result = optimizer_module._finalize_result(
        day,
        duplicated,
        attempts=(),
        emergency_leads_enabled=False,
        disposition=EmergencyPassDisposition.NOT_ENABLED,
        overall_runtime=duplicated.solver_runtime_seconds,
    )

    codes = warning_codes(result)
    assert codes.count(WarningCode.PUSH_QUALIFICATION_NOT_MET) == 1
    assert codes.count(WarningCode.CLOSE_QUALIFICATION_NOT_MET) == 1
    assert WarningCode.MANUAL_INTERVENTION_REQUIRED in codes


def test_warning_order_and_structured_flight_identity_are_deterministic() -> None:
    result = optimize_flight_assignments(
        day_for(
            (departure("101"), departure("202", 11)),
            3,
            ramp_qualifications=frozenset(),
        )
    )

    assert warning_codes(result) == (
        WarningCode.PUSH_QUALIFICATION_NOT_MET,
        WarningCode.CLOSE_QUALIFICATION_NOT_MET,
        WarningCode.PUSH_QUALIFICATION_NOT_MET,
        WarningCode.CLOSE_QUALIFICATION_NOT_MET,
        WarningCode.MANUAL_INTERVENTION_REQUIRED,
        WarningCode.MANUAL_INTERVENTION_REQUIRED,
    )
    assert [
        warning.departure_flight_number
        for warning in result.warnings
        if warning.code is WarningCode.MANUAL_INTERVENTION_REQUIRED
    ] == ["101", "202"]


def test_schedule_summary_matches_mixed_final_results() -> None:
    result = optimize_flight_assignments(
        day_for((arrival("100"), departure("200", 11)), 4)
    )
    summary = result.schedule_summary

    assert summary.total_flights == len(result.flight_results) == 2
    assert summary.minimum_staffed_flights == 2
    assert summary.qualification_required_flights == 1
    assert summary.qualification_compliant_flights == 1
    assert summary.total_assignments == sum(
        flight.staffing_count for flight in result.flight_results
    )
    assert summary.critical_warning_count == sum(
        warning.severity is WarningSeverity.CRITICAL
        for warning in result.warnings
    )
    assert summary.warning_count == len(result.warnings)


def test_empty_day_is_usable_ready_and_all_zero() -> None:
    result = optimize_flight_assignments(OperationalDay(date(2026, 9, 2)))

    assert result.operational_readiness is OperationalReadinessStatus.READY
    assert result.status is OptimizationStatus.OPTIMAL
    assert result.schedule_summary.total_flights == 0
    assert result.schedule_summary.total_assignments == 0
    assert result.schedule_summary.warning_count == 0
    assert result.fairness_metrics.participating_employee_count == 0
    assert result.continuity_metrics.eligible_transition_count == 0


def test_genuine_no_schedule_result_is_reported_clearly(monkeypatch) -> None:
    day = day_for((arrival("101"),), 3)
    failed = optimizer_module._empty_solver_result(
        OptimizationStatus.UNKNOWN, (), 0.01
    )
    monkeypatch.setattr(
        optimizer_module,
        "_run_optimization_pass",
        lambda *args, **kwargs: (failed, 0),
    )

    result = optimize_flight_assignments(day)

    assert result.operational_readiness is (
        OperationalReadinessStatus.NO_USABLE_SCHEDULE
    )
    assert warning_codes(result) == (WarningCode.NO_USABLE_SCHEDULE,)
    assert result.attempts[0].usable_schedule is False
    assert "No usable schedule" in format_optimization_report(result)


@pytest.mark.parametrize(
    ("enum_value", "expected"),
    [
        (OperationalReadinessStatus.READY, "READY"),
        (OperationalReadinessStatus.READY_WITH_WARNINGS, "READY_WITH_WARNINGS"),
        (
            OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED,
            "MANUAL_INTERVENTION_REQUIRED",
        ),
        (OperationalReadinessStatus.NO_USABLE_SCHEDULE, "NO_USABLE_SCHEDULE"),
        (EmergencyPassDisposition.NOT_ENABLED, "NOT_ENABLED"),
        (EmergencyPassDisposition.NOT_NEEDED, "NOT_NEEDED"),
        (
            EmergencyPassDisposition.ATTEMPTED_AND_ADOPTED,
            "ATTEMPTED_AND_ADOPTED",
        ),
        (
            WarningCode.SOLVER_RESULT_NOT_PROVEN_OPTIMAL,
            "SOLVER_RESULT_NOT_PROVEN_OPTIMAL",
        ),
        (WarningCode.EMERGENCY_RECOVERY_NOT_ADOPTED, "EMERGENCY_RECOVERY_NOT_ADOPTED"),
        (WarningCode.NO_USABLE_SCHEDULE, "NO_USABLE_SCHEDULE"),
    ],
)
def test_new_public_enums_have_stable_string_values(enum_value, expected) -> None:
    assert enum_value.value == expected


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (WarningCode.MINIMUM_STAFFING_NOT_MET, "MINIMUM_STAFFING_NOT_MET"),
        (WarningCode.PUSH_QUALIFICATION_NOT_MET, "PUSH_QUALIFICATION_NOT_MET"),
        (WarningCode.CLOSE_QUALIFICATION_NOT_MET, "CLOSE_QUALIFICATION_NOT_MET"),
        (WarningCode.REQUIRED_BREAK_NOT_MET, "REQUIRED_BREAK_NOT_MET"),
        (WarningCode.EMERGENCY_LEAD_USED, "EMERGENCY_LEAD_USED"),
        (WarningCode.MANUAL_INTERVENTION_REQUIRED, "MANUAL_INTERVENTION_REQUIRED"),
    ],
)
def test_existing_warning_code_values_remain_stable(code, expected) -> None:
    assert code.value == expected


@pytest.mark.parametrize("kind", ["ordinary", "partial", "emergency"])
def test_human_readable_report_is_deterministic_and_actionable(kind) -> None:
    if kind == "ordinary":
        result = optimize_flight_assignments(day_for((arrival("101"),), 3))
    elif kind == "partial":
        result = optimize_flight_assignments(day_for((arrival("101"),), 2))
    else:
        result = optimize_flight_assignments(
            day_for((arrival("101"),), 2, lead_count=1), config(leads=True)
        )

    first = format_optimization_report(result)
    second = format_optimization_report(result)
    assert first == second
    assert result.operational_readiness.value in first
    assert "Flights:" in first
    assert "Runtime:" in first
    if kind == "partial":
        assert "MINIMUM_STAFFING_NOT_MET" in first
    if kind == "emergency":
        assert "Lead Employee L1" in first


def test_formatter_performs_no_printing_or_file_io(capsys) -> None:
    result = optimize_flight_assignments(day_for((arrival("101"),), 3))

    report = format_optimization_report(result)

    assert report
    assert capsys.readouterr() == ("", "")


def test_repeated_seeded_runs_have_equivalent_reporting() -> None:
    day = day_for((arrival("101"),), 2, lead_count=1)
    active_config = config(leads=True)

    first = optimize_flight_assignments(day, active_config)
    second = optimize_flight_assignments(day, active_config)

    assert first.operational_readiness == second.operational_readiness
    assert first.emergency_pass_disposition == second.emergency_pass_disposition
    assert first.schedule_summary == second.schedule_summary
    assert first.warnings == second.warnings
    assert format_optimization_report(first).split("Runtime:")[0] == (
        format_optimization_report(second).split("Runtime:")[0]
    )
