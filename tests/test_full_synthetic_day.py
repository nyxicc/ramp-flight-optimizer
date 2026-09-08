"""Canonical full-day end-to-end verification for Milestone 14."""

from collections import Counter

import pytest

from ramp_optimizer import (
    EmergencyLeadReason,
    EmergencyPassDisposition,
    EmergencyStaffingStatus,
    FlightType,
    OperationalReadinessStatus,
    OperationalRole,
    OptimizationResult,
    OptimizationStatus,
    WarningCode,
    derive_flight_operational_facts,
    format_optimization_report,
    validate_operational_day,
)
from tests.invariant_checks import assert_result_invariants
from tests.scenario_builders import SyntheticScenario


pytestmark = [pytest.mark.integration, pytest.mark.slow]


def test_canonical_day_is_intentional_fictional_and_feature_complete(
    canonical_scenario: SyntheticScenario,
) -> None:
    day = canonical_scenario.day
    config = canonical_scenario.config

    assert validate_operational_day(day, config) == ()
    assert len(day.flights) == 24
    assert len(day.employees) == 21
    assert {employee.employee_id for employee in day.employees} == {
        *(f"R{index:03}" for index in range(1, 17)),
        "R099",
        "L001",
        "L002",
        "T001",
        "N001",
    }
    roles = Counter(shift.normalized_role for shift in day.employee_shifts)
    assert roles[OperationalRole.RAMP_AGENT] == 18
    assert roles[OperationalRole.RAMP_LEAD] == 2
    assert roles[OperationalRole.TRAINEE] == 1
    assert roles[OperationalRole.NON_RAMP] == 1
    assert any(not employee.enabled for employee in day.employees)
    assert sum(shift.employee_id == "R013" for shift in day.employee_shifts) == 2
    assert any(shift.end.date() > shift.start.date() for shift in day.employee_shifts)
    durations = {
        int((shift.end - shift.start).total_seconds() // 3600)
        for shift in day.employee_shifts
        if shift.normalized_role is OperationalRole.RAMP_AGENT
    }
    assert {4, 6, 8, 10} <= durations

    facts = tuple(
        derive_flight_operational_facts(flight, config) for flight in day.flights
    )
    assert {fact.flight_type for fact in facts} == set(FlightType)
    assert {fact.express for fact in facts} == {False, True}
    assert any(flight.heavy for flight in day.flights)
    assert any(
        left.work_start < right.work_end and right.work_start < left.work_end
        for index, left in enumerate(facts)
        for right in facts[index + 1 :]
    )
    assert len(day.fixed_assignments) == 72


def test_ordinary_full_day_reconstructs_every_public_invariant(
    canonical_scenario: SyntheticScenario,
    canonical_result: OptimizationResult,
) -> None:
    assert_result_invariants(
        canonical_scenario.day,
        canonical_scenario.config,
        canonical_result,
    )
    assert canonical_result.status is OptimizationStatus.OPTIMAL
    assert canonical_result.operational_readiness is OperationalReadinessStatus.READY
    assert canonical_result.emergency_pass_disposition is (
        EmergencyPassDisposition.NOT_ENABLED
    )
    assert canonical_result.warnings == ()
    assert len(canonical_result.attempts) == 1
    assert canonical_result.attempts[0].selected_as_final
    assert canonical_result.schedule_summary is not None
    assert canonical_result.schedule_summary.minimum_staffed_flights == 24
    assert canonical_result.schedule_summary.qualification_compliant_flights == 15
    assert canonical_result.schedule_summary.total_assignments == sum(
        flight.staffing_count for flight in canonical_result.flight_results
    )


def test_emergency_full_day_adopts_only_necessary_critical_recovery(
    recoverable_emergency_scenario: SyntheticScenario,
    recoverable_emergency_result: OptimizationResult,
) -> None:
    result = recoverable_emergency_result
    assert_result_invariants(
        recoverable_emergency_scenario.day,
        recoverable_emergency_scenario.config,
        result,
    )
    assert len(result.attempts) == 2
    assert [attempt.critical_shortage_count for attempt in result.attempts] == [1, 0]
    assert [attempt.selected_as_final for attempt in result.attempts] == [False, True]
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_AND_ADOPTED
    )
    assert result.emergency_staffing_status is (
        EmergencyStaffingStatus.LEAD_ASSISTED_SCHEDULE
    )
    assert result.operational_readiness is (
        OperationalReadinessStatus.READY_WITH_WARNINGS
    )
    assert len(result.lead_assignments) == 1
    intervention = result.lead_assignments[0]
    assert intervention.employee_id == "L002"
    assert intervention.reasons == (EmergencyLeadReason.MINIMUM_STAFFING,)
    assert intervention.flight == recoverable_emergency_scenario.day.flights[-1]
    assert {warning.code for warning in result.warnings} == {
        WarningCode.EMERGENCY_LEAD_USED
    }


def test_insufficient_emergency_day_adopts_improvement_and_reports_only_remainder(
    insufficient_emergency_scenario: SyntheticScenario,
    insufficient_emergency_result: OptimizationResult,
) -> None:
    result = insufficient_emergency_result
    assert_result_invariants(
        insufficient_emergency_scenario.day,
        insufficient_emergency_scenario.config,
        result,
    )
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_ADOPTED_WITH_REMAINING_SHORTAGE
    )
    assert result.operational_readiness is (
        OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED
    )
    assert result.schedule_summary is not None
    assert result.schedule_summary.below_minimum_flights == 1
    assert len(result.lead_assignments) == 1
    repaired = insufficient_emergency_scenario.day.flights[-2]
    unresolved = insufficient_emergency_scenario.day.flights[-1]
    by_flight = {item.flight: item for item in result.flight_results}
    assert by_flight[repaired].minimum_met
    assert not by_flight[unresolved].minimum_met
    shortage_warnings = tuple(
        warning
        for warning in result.warnings
        if warning.code is WarningCode.MINIMUM_STAFFING_NOT_MET
    )
    assert len(shortage_warnings) == 1
    assert shortage_warnings[0].departure_flight_number == "FX3991"
    assert all(warning.arrival_flight_number != "FX3990" for warning in shortage_warnings)


def test_large_report_is_structurally_deterministic_and_pure(
    canonical_result: OptimizationResult,
) -> None:
    first = format_optimization_report(canonical_result)
    second = format_optimization_report(canonical_result)

    assert first == second
    assert "Readiness: READY" in first
    assert "Flights: 24; minimum staffed 24/24" in first
    assert "Lead interventions:\n- None" in first
    assert "Warnings:\n- None" in first
    assert "Pass 1 RAMP_AGENT_ONLY" in first
