"""Deliberately ugly but valid shortage scenarios must remain coherent."""

from dataclasses import replace
from datetime import timedelta

import pytest

from ramp_optimizer import (
    BreakStatus,
    EmergencyPassDisposition,
    FixedAssignment,
    OperationalReadinessStatus,
    OperationalRole,
    OptimizationStatus,
    OptimizerConfig,
    Qualification,
    WarningCode,
    optimize_flight_assignments,
)
from tests.invariant_checks import assert_result_invariants
from tests.scenario_builders import (
    arrival,
    at,
    departure,
    operational_day,
    optimizer_config,
    synthetic_employee,
    synthetic_shift,
    turn,
)


def _shift_for(employee_id: str, *, role=OperationalRole.RAMP_AGENT):
    return synthetic_shift(employee_id, start=at(6), end=at(14), role=role)


@pytest.mark.parametrize("missing", [Qualification.PUSH, Qualification.CLOSE_OUT])
def test_overlapping_qualified_flights_compete_for_one_specialist(
    missing: Qualification,
) -> None:
    if missing is Qualification.PUSH:
        flights = (departure("FX101", at(9)), departure("FX102", at(9, 15)))
        specialist_qualifications = (Qualification.PUSH, Qualification.CLOSE_OUT)
        common_qualifications = (Qualification.CLOSE_OUT,)
    else:
        flights = (
            turn("FX201", at(8), "FX301", at(9)),
            turn("FX202", at(8, 15), "FX302", at(9, 15)),
        )
        specialist_qualifications = (Qualification.PUSH, Qualification.CLOSE_OUT)
        common_qualifications = (Qualification.PUSH,)
    employees = (
        synthetic_employee("R001", qualifications=specialist_qualifications),
        *(synthetic_employee(f"R{index:03}", qualifications=common_qualifications)
          for index in range(2, 7)),
    )
    day = operational_day(
        employees=employees,
        shifts=tuple(_shift_for(worker.employee_id) for worker in employees),
        flights=flights,
    )
    config = optimizer_config(
        normal_preferred_staff=3,
        heavy_preferred_staff=3,
        solver_time_limit_seconds=2.0,
    )

    result = optimize_flight_assignments(day, config)

    assert_result_invariants(day, config, result)
    assert all(flight.minimum_met for flight in result.flight_results)
    coverage = (
        [flight.push_covered for flight in result.flight_results]
        if missing is Qualification.PUSH
        else [flight.close_covered for flight in result.flight_results]
    )
    assert sorted(coverage) == [False, True]
    expected_code = (
        WarningCode.PUSH_QUALIFICATION_NOT_MET
        if missing is Qualification.PUSH
        else WarningCode.CLOSE_QUALIFICATION_NOT_MET
    )
    assert sum(warning.code is expected_code for warning in result.warnings) == 1


@pytest.mark.parametrize("kind", ["no-employees", "all-disabled", "unavailable", "excluded"])
def test_no_eligible_candidate_cases_return_reported_partial_schedule(kind: str) -> None:
    target = departure("FX101", at(9))
    if kind == "no-employees":
        employees = ()
        shifts = ()
    elif kind == "all-disabled":
        employees = tuple(
            synthetic_employee(f"R{index:03}", enabled=False)
            for index in range(1, 4)
        )
        shifts = tuple(_shift_for(worker.employee_id) for worker in employees)
    elif kind == "unavailable":
        employees = tuple(
            synthetic_employee(f"R{index:03}") for index in range(1, 4)
        )
        shifts = tuple(
            synthetic_shift(worker.employee_id, start=at(12), end=at(16))
            for worker in employees
        )
    else:
        employees = (
            synthetic_employee("T001"),
            synthetic_employee("N001"),
        )
        shifts = (
            _shift_for("T001", role=OperationalRole.TRAINEE),
            _shift_for("N001", role=OperationalRole.NON_RAMP),
        )
    day = operational_day(employees=employees, shifts=shifts, flights=(target,))
    config = optimizer_config(solver_time_limit_seconds=2.0)

    result = optimize_flight_assignments(day, config)

    assert_result_invariants(day, config, result)
    assert result.status is OptimizationStatus.OPTIMAL
    assert result.flight_results[0].staffing_count == 0
    assert result.operational_readiness is (
        OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED
    )
    assert WarningCode.MINIMUM_STAFFING_NOT_MET in {
        warning.code for warning in result.warnings
    }


def test_overlapping_shortages_use_one_dual_qualified_lead_where_it_helps_most() -> None:
    flights = (arrival("FX101", at(9)), arrival("FX102", at(9)))
    ramps = tuple(synthetic_employee(f"R{index:03}") for index in range(1, 5))
    lead = synthetic_employee(
        "L001", qualifications=(Qualification.PUSH, Qualification.CLOSE_OUT)
    )
    day = operational_day(
        employees=ramps + (lead,),
        shifts=tuple(_shift_for(worker.employee_id) for worker in ramps)
        + (_shift_for("L001", role=OperationalRole.RAMP_LEAD),),
        flights=flights,
    )
    config = optimizer_config(
        allow_leads_for_minimum_staffing=True,
        solver_time_limit_seconds=2.0,
    )

    result = optimize_flight_assignments(day, config)

    assert_result_invariants(day, config, result)
    assert sorted(flight.staffing_count for flight in result.flight_results) == [2, 3]
    assert len(result.lead_assignments) == 1
    assert result.emergency_pass_disposition is (
        EmergencyPassDisposition.ATTEMPTED_ADOPTED_WITH_REMAINING_SHORTAGE
    )


@pytest.mark.parametrize("lead_problem", ["outside-shift", "wrong-qualification"])
def test_unhelpful_emergency_lead_never_falsely_repairs_shortage(
    lead_problem: str,
) -> None:
    ramps = tuple(
        synthetic_employee(
            f"R{index:03}", qualifications=(Qualification.PUSH,)
        )
        for index in range(1, 4)
    )
    lead = synthetic_employee("L001", qualifications=(Qualification.PUSH,))
    day = operational_day(
        employees=ramps + (lead,),
        shifts=tuple(_shift_for(worker.employee_id) for worker in ramps)
        + (
            synthetic_shift(
                "L001",
                start=at(12) if lead_problem == "outside-shift" else at(6),
                end=at(16) if lead_problem == "outside-shift" else at(14),
                role=OperationalRole.RAMP_LEAD,
            ),
        ),
        flights=(departure("FX101", at(9)),),
    )
    config = optimizer_config(
        allow_leads_for_minimum_staffing=True,
        solver_time_limit_seconds=2.0,
    )

    result = optimize_flight_assignments(day, config)

    assert_result_invariants(day, config, result)
    assert result.flight_results[0].close_covered is False
    assert result.lead_assignments == ()
    assert result.operational_readiness is (
        OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED
    )


def test_fixed_work_can_force_both_break_failure_and_long_streak_without_exception() -> None:
    flights = tuple(
        arrival(f"FX{101 + index}", at(8, 10) + timedelta(minutes=40 * index))
        for index in range(4)
    )
    worker = synthetic_employee("R001")
    day = operational_day(
        employees=(worker,),
        shifts=(synthetic_shift("R001", start=at(7), end=at(12)),),
        flights=flights,
        fixed=tuple(FixedAssignment("R001", flight) for flight in flights),
    )
    config = optimizer_config(
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
        required_break_minutes=30,
        consecutive_reset_minutes=40,
        solver_time_limit_seconds=2.0,
    )

    result = optimize_flight_assignments(day, config)

    assert_result_invariants(day, config, result)
    assert result.employee_results[0].break_status is BreakStatus.UNSATISFIED
    assert result.employee_results[0].longest_consecutive_streak == 4
    assert WarningCode.REQUIRED_BREAK_NOT_MET in {
        warning.code for warning in result.warnings
    }
