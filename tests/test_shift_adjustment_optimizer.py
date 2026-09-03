"""Deterministic Milestone 8 shift-length fairness tests."""

from dataclasses import replace
from datetime import date, datetime, timedelta

from ortools.sat.python import cp_model
import pytest

import ramp_optimizer.optimizer as optimizer_module
from ramp_optimizer import (
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    InputValidationError,
    OperationalDay,
    OperationalRole,
    OptimizationStatus,
    OptimizerConfig,
    Qualification,
    build_candidate_assignments,
    optimize_flight_assignments,
    validate_operational_day,
)


def at(hour: int, minute: int = 0, *, day: int = 2) -> datetime:
    return datetime(2026, 9, day, hour, minute)


def arrival(
    number: str,
    hour: int,
    minute: int = 40,
    *,
    day: int = 2,
) -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=at(hour, minute, day=day),
    )


def departure(number: str, hour: int, minute: int = 0) -> Flight:
    return Flight(
        departure_flight_number=number,
        departure_time=at(hour, minute),
    )


def employee(
    employee_id: str,
    *qualifications: Qualification,
    enabled: bool = True,
) -> Employee:
    return Employee(
        employee_id,
        f"Employee {employee_id}",
        frozenset(qualifications),
        enabled,
    )


def shift(
    employee_id: str,
    start: datetime,
    end: datetime,
    role: OperationalRole = OperationalRole.RAMP_AGENT,
) -> EmployeeShift:
    return EmployeeShift(employee_id, start, end, role)


def day_for(
    flights: tuple[Flight, ...],
    employees: tuple[Employee, ...],
    shifts: tuple[EmployeeShift, ...],
    *,
    fixed: tuple[FixedAssignment, ...] = (),
) -> OperationalDay:
    return OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=shifts,
        flights=flights,
        fixed_assignments=fixed,
    )


def one_person_config(**changes) -> OptimizerConfig:
    values = {
        "minimum_staff": 1,
        "normal_preferred_staff": 1,
        "heavy_preferred_staff": 1,
    }
    values.update(changes)
    return replace(OptimizerConfig(), **values)


def spaced_arrivals(count: int) -> tuple[Flight, ...]:
    first_time = at(8, 10)
    return tuple(
        Flight(
            arrival_flight_number=str(100 + index),
            arrival_time=first_time + timedelta(minutes=30 * index),
        )
        for index in range(count)
    )


def result_by_id(result) -> dict[str, object]:
    return {item.employee_id: item for item in result.employee_results}


def flight_counts(result) -> dict[str, int]:
    return {
        employee_id: item.flight_count
        for employee_id, item in result_by_id(result).items()
    }


def test_longer_shift_receives_unavoidable_extra_flight() -> None:
    workers = (employee("LONG"), employee("SHORT"))
    shifts = (
        shift("LONG", at(8), at(16)),
        shift("SHORT", at(8), at(12)),
    )

    result = optimize_flight_assignments(
        day_for(spaced_arrivals(3), workers, shifts), one_person_config()
    )

    assert flight_counts(result) == {"LONG": 2, "SHORT": 1}
    assert result.objective_values[9].value == 1
    assert result.objective_values[10].value == 1
    assert result.objective_values[11].value == 0


def test_raw_equality_remains_primary_over_proportional_target() -> None:
    workers = (employee("LONG"), employee("SHORT"))
    shifts = (
        shift("LONG", at(8), at(16)),
        shift("SHORT", at(8), at(12)),
    )

    result = optimize_flight_assignments(
        day_for(spaced_arrivals(6), workers, shifts), one_person_config()
    )
    results = result_by_id(result)

    assert flight_counts(result) == {"LONG": 3, "SHORT": 3}
    assert result.objective_values[9].value == 0
    assert result.objective_values[10].value == 0
    assert result.objective_values[11].value == 1440
    assert results["LONG"].scheduled_shift_minutes == 480
    assert results["SHORT"].scheduled_shift_minutes == 240
    assert results["LONG"].proportional_target_flight_count == 4.0
    assert results["SHORT"].proportional_target_flight_count == 2.0
    assert results["LONG"].shift_adjusted_deviation == 1.0
    assert results["SHORT"].shift_adjusted_deviation == 1.0
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_participating_shift_minutes == 720
    assert result.fairness_metrics.total_shift_adjusted_deviation == 2.0
    assert (
        result.objective_values[11].value
        == result.fairness_metrics.total_shift_adjusted_deviation
        * result.fairness_metrics.total_participating_shift_minutes
    )


def test_equal_shift_lengths_preserve_raw_fairness_deterministically() -> None:
    workers = (employee("A"), employee("B"))
    shifts = (
        shift("A", at(8), at(16)),
        shift("B", at(8), at(16)),
    )
    day = day_for(spaced_arrivals(3), workers, shifts)
    config = one_person_config()

    first = optimize_flight_assignments(day, config)
    second = optimize_flight_assignments(day, config)

    assert sorted(flight_counts(first).values()) == [1, 2]
    assert first.flight_results == second.flight_results
    assert first.employee_results == second.employee_results
    assert first.fairness_metrics == second.fairness_metrics
    assert first.objective_values == second.objective_values


def test_three_employees_put_lower_count_on_short_shift() -> None:
    workers = tuple(employee(worker_id) for worker_id in ("LONG1", "LONG2", "SHORT"))
    shifts = (
        shift("LONG1", at(8), at(16)),
        shift("LONG2", at(8), at(16)),
        shift("SHORT", at(8), at(12)),
    )

    result = optimize_flight_assignments(
        day_for(spaced_arrivals(8), workers, shifts), one_person_config()
    )

    assert flight_counts(result) == {"LONG1": 3, "LONG2": 3, "SHORT": 2}
    assert result.objective_values[9].value == 1
    assert result.objective_values[10].value == 2


def test_objective_reporting_appends_shift_adjustment_as_stage_12() -> None:
    worker = employee("E1")
    result = optimize_flight_assignments(
        day_for(
            (arrival("101", 9),),
            (worker,),
            (shift("E1", at(8), at(16)),),
        ),
        one_person_config(),
    )

    assert len(result.objective_values) == 14
    assert result.objective_values[11].stage == 12
    assert result.objective_values[11].name == (
        "total_shift_adjusted_flight_count_deviation"
    )
    assert result.objective_values[11].value == 0
    assert result.objective_values[11].proven_optimal


def test_fixed_short_shift_work_contributes_and_optional_work_favors_long_shift() -> None:
    flights = (
        arrival("101", 8, 10),
        arrival("102", 9, 10),
        arrival("103", 10, 10),
    )
    workers = (employee("LONG"), employee("SHORT"))
    shifts = (
        shift("LONG", at(8), at(16)),
        shift("SHORT", at(8), at(12)),
    )
    fixed = (FixedAssignment("SHORT", flights[0]),)

    result = optimize_flight_assignments(
        day_for(flights, workers, shifts, fixed=fixed), one_person_config()
    )

    assert flight_counts(result) == {"LONG": 2, "SHORT": 1}
    assert result.flight_results[0].fixed_employee_ids == ("SHORT",)
    assert result.flight_results[0].assigned_employee_ids == ("SHORT",)
    assert all(
        item.assigned_employee_ids == ("LONG",)
        for item in result.flight_results[1:]
    )


def test_raw_fairness_still_controls_optional_work_around_fixed_assignments() -> None:
    flights = spaced_arrivals(3)
    workers = (employee("LONG"), employee("SHORT"))
    shifts = (
        shift("LONG", at(8), at(16)),
        shift("SHORT", at(8), at(12)),
    )
    fixed = (
        FixedAssignment("SHORT", flights[0]),
        FixedAssignment("SHORT", flights[1]),
    )

    result = optimize_flight_assignments(
        day_for(flights, workers, shifts, fixed=fixed), one_person_config()
    )

    assert flight_counts(result) == {"LONG": 1, "SHORT": 2}
    assert result.objective_values[9].value == 1
    assert result.flight_results[0].fixed_employee_ids == ("SHORT",)
    assert result.flight_results[1].fixed_employee_ids == ("SHORT",)


def test_eligible_zero_assignment_employee_keeps_target_and_deviation() -> None:
    target = arrival("101", 9)
    workers = (employee("FIXED"), employee("ZERO"))
    shifts = (
        shift("FIXED", at(8), at(12)),
        shift("ZERO", at(8), at(16)),
    )

    result = optimize_flight_assignments(
        day_for(
            (target,),
            workers,
            shifts,
            fixed=(FixedAssignment("FIXED", target),),
        ),
        one_person_config(),
    )
    results = result_by_id(result)

    assert flight_counts(result) == {"FIXED": 1, "ZERO": 0}
    assert results["ZERO"].proportional_target_flight_count == pytest.approx(2 / 3)
    assert results["ZERO"].shift_adjusted_deviation == pytest.approx(2 / 3)
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 2


def test_no_opportunity_employee_has_minutes_but_no_target_or_deviation() -> None:
    workers = (employee("AVAILABLE"), employee("OUTSIDE"))
    shifts = (
        shift("AVAILABLE", at(8), at(12)),
        shift("OUTSIDE", at(14), at(18)),
    )

    result = optimize_flight_assignments(
        day_for((arrival("101", 9),), workers, shifts), one_person_config()
    )
    outside = result_by_id(result)["OUTSIDE"]

    assert outside.scheduled_shift_minutes == 240
    assert outside.proportional_target_flight_count is None
    assert outside.shift_adjusted_deviation is None
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 1
    assert result.fairness_metrics.total_participating_shift_minutes == 240


def test_multiple_ramp_shifts_sum_without_counting_the_gap() -> None:
    worker = employee("SPLIT")
    shifts = (
        shift("SPLIT", at(5), at(9)),
        shift("SPLIT", at(13), at(17)),
    )

    result = optimize_flight_assignments(
        day_for((arrival("101", 8),), (worker,), shifts), one_person_config()
    )
    employee_result = result.employee_results[0]

    assert employee_result.scheduled_shift_minutes == 480
    assert employee_result.proportional_target_flight_count == 1.0
    assert employee_result.shift_adjusted_deviation == 0.0
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_participating_shift_minutes == 480


def test_lead_shift_does_not_inflate_ramp_shift_minutes() -> None:
    worker = employee("MIXED")
    shifts = (
        shift("MIXED", at(5), at(9)),
        shift("MIXED", at(13), at(17), OperationalRole.RAMP_LEAD),
    )

    result = optimize_flight_assignments(
        day_for((arrival("101", 8),), (worker,), shifts), one_person_config()
    )

    assert result.employee_results[0].scheduled_shift_minutes == 240
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_participating_shift_minutes == 240


@pytest.mark.parametrize(
    ("role", "config_field"),
    [
        (OperationalRole.TRAINEE, "allow_trainees_for_assignments"),
        (
            OperationalRole.POSSIBLE_RAMP_SUPPORT,
            "allow_possible_ramp_support_for_assignments",
        ),
    ],
)
def test_explicitly_enabled_ordinary_support_shift_contributes_minutes(
    role: OperationalRole,
    config_field: str,
) -> None:
    worker = employee("MIXED")
    shifts = (
        shift("MIXED", at(5), at(9)),
        shift("MIXED", at(13), at(17), role),
    )
    config = one_person_config(**{config_field: True})

    result = optimize_flight_assignments(
        day_for((arrival("101", 8),), (worker,), shifts), config
    )

    assert result.employee_results[0].scheduled_shift_minutes == 480
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_participating_shift_minutes == 480


def test_overnight_shift_uses_cross_date_duration() -> None:
    worker = employee("OVERNIGHT")
    overnight = shift("OVERNIGHT", at(22), at(6, day=3))
    target = arrival("101", 1, 0, day=3)

    result = optimize_flight_assignments(
        day_for((target,), (worker,), (overnight,)), one_person_config()
    )

    assert result.employee_results[0].scheduled_shift_minutes == 480
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_participating_shift_minutes == 480


@pytest.mark.parametrize(
    ("shifts", "expected_code"),
    [
        (
            (
                shift("E1", at(5), at(9)),
                shift("E1", at(8), at(12)),
            ),
            "OVERLAPPING_EMPLOYEE_SHIFTS",
        ),
        (
            (
                shift("E1", at(5), at(9)),
                shift("E1", at(5), at(9)),
            ),
            "DUPLICATE_EMPLOYEE_SHIFT",
        ),
        (
            (
                shift(
                    "E1",
                    at(5),
                    datetime(2026, 9, 2, 9, 0, 30),
                ),
            ),
            "INVALID_EMPLOYEE_SHIFT_MINUTES",
        ),
    ],
)
def test_invalid_shift_data_cannot_inflate_scheduled_duration(
    shifts: tuple[EmployeeShift, ...],
    expected_code: str,
) -> None:
    day = day_for((), (employee("E1"),), shifts)

    assert expected_code in {issue.code for issue in validate_operational_day(day)}
    with pytest.raises(InputValidationError):
        optimize_flight_assignments(day)


def test_long_shift_cannot_make_an_outside_assignment_legal() -> None:
    workers = (employee("LONG_OUTSIDE"), employee("SHORT_AVAILABLE"))
    shifts = (
        shift("LONG_OUTSIDE", at(12), at(20)),
        shift("SHORT_AVAILABLE", at(8), at(12)),
    )

    result = optimize_flight_assignments(
        day_for((arrival("101", 9),), workers, shifts), one_person_config()
    )

    assert result.flight_results[0].assigned_employee_ids == ("SHORT_AVAILABLE",)


def test_shift_adjustment_does_not_allow_overlapping_assignments() -> None:
    worker = employee("LONG")
    flights = (arrival("101", 9), arrival("102", 9))

    result = optimize_flight_assignments(
        day_for(
            flights,
            (worker,),
            (shift("LONG", at(5), at(17)),),
        ),
        one_person_config(),
    )

    assert sum(item.staffing_count for item in result.flight_results) == 1
    assert result.employee_results[0].flight_count == 1


def test_shift_adjustment_does_not_manufacture_extra_staffing() -> None:
    workers = (employee("LONG"), employee("SHORT"))
    shifts = (
        shift("LONG", at(8), at(16)),
        shift("SHORT", at(8), at(12)),
    )

    result = optimize_flight_assignments(
        day_for((arrival("101", 9),), workers, shifts), one_person_config()
    )

    assert sum(item.staffing_count for item in result.flight_results) == 1
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_assignments == 1


def test_zero_participants_have_zero_shift_metrics_and_objective() -> None:
    result = optimize_flight_assignments(OperationalDay(date(2026, 9, 2)))

    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_participating_shift_minutes == 0
    assert result.fairness_metrics.total_shift_adjusted_deviation == 0.0
    assert result.objective_values[11].value == 0


def test_zero_participant_assignments_have_zero_modeled_deviation() -> None:
    workers = (employee("A"), employee("B"))
    shifts = (
        shift("A", at(8), at(16)),
        shift("B", at(8), at(12)),
    )
    day = day_for((arrival("101", 9),), workers, shifts)
    config = one_person_config()
    model_data = optimizer_module._build_model(
        day,
        config,
        build_candidate_assignments(day, config),
    )
    for decision in model_data.decisions.values():
        model_data.model.add(decision == 0)
    solver = cp_model.CpSolver()

    assert solver.solve(model_data.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    assert solver.value(model_data.total_fairness_assignment_count) == 0
    assert all(
        solver.value(deviation) == 0
        for deviation in model_data.shift_adjusted_deviations
        if deviation is not None
    )
    assert solver.value(model_data.total_shift_adjusted_deviation) == 0


def test_one_participant_has_zero_shift_adjusted_deviation() -> None:
    worker = employee("ONLY")

    result = optimize_flight_assignments(
        day_for(
            spaced_arrivals(3),
            (worker,),
            (shift("ONLY", at(8), at(16)),),
        ),
        one_person_config(),
    )

    assert result.employee_results[0].flight_count == 3
    assert result.employee_results[0].proportional_target_flight_count == 3.0
    assert result.employee_results[0].shift_adjusted_deviation == 0.0
    assert result.objective_values[11].value == 0


def test_shift_adjustment_adds_no_warnings_and_streak_fields_stay_none() -> None:
    workers = (employee("LONG"), employee("SHORT"))
    shifts = (
        shift("LONG", at(8), at(16)),
        shift("SHORT", at(8), at(12)),
    )

    result = optimize_flight_assignments(
        day_for(spaced_arrivals(3), workers, shifts), one_person_config()
    )

    assert result.warnings == ()
    assert all(
        item.longest_consecutive_streak is None
        and item.adjusted_workload is not None
        for item in result.employee_results
    )
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.maximum_consecutive_streak is None
    assert result.fairness_metrics.adjusted_workload_spread is not None


def test_timeout_during_shift_stage_preserves_stage_11_solution(monkeypatch) -> None:
    workers = (employee("LONG"), employee("SHORT"))
    shifts = (
        shift("LONG", at(8), at(16)),
        shift("SHORT", at(8), at(12)),
    )
    day = day_for(spaced_arrivals(3), workers, shifts)
    real_solver_type = cp_model.CpSolver
    calls = 0

    class UnknownSolver:
        def __init__(self) -> None:
            self.parameters = type("Parameters", (), {})()

        def solve(self, model) -> cp_model.CpSolverStatus:
            return cp_model.UNKNOWN

    def solver_factory():
        nonlocal calls
        calls += 1
        return real_solver_type() if calls <= 11 else UnknownSolver()

    monkeypatch.setattr(optimizer_module.cp_model, "CpSolver", solver_factory)

    result = optimize_flight_assignments(day, one_person_config())

    assert result.status is OptimizationStatus.FEASIBLE
    assert len(result.objective_values) == 12
    assert all(item.proven_optimal for item in result.objective_values[:11])
    assert result.objective_values[11].proven_optimal is False
    assert sorted(flight_counts(result).values()) == [1, 2]
    assert result.objective_values[9].value == 1
    assert result.objective_values[10].value == 1


def test_moderate_mixed_day_prefers_longer_shifts_after_raw_fairness() -> None:
    group_times = ((8, 40), (9, 40), (10, 40), (11, 40), (12, 40))
    flights = tuple(
        movement
        for index, (hour, minute) in enumerate(group_times)
        for movement in (
            arrival(str(100 + index), hour, minute),
            departure(str(200 + index), hour, minute),
        )
    )
    workers = tuple(
        employee(worker_id, Qualification.PUSH, Qualification.CLOSE_OUT)
        for worker_id in ("LONG", "MID", "SHORT", "SPLIT")
    )
    shifts = (
        shift("LONG", at(7, 30), at(17, 30)),
        shift("MID", at(7, 30), at(15, 30)),
        shift("SHORT", at(7, 30), at(13, 30)),
        shift("SPLIT", at(7, 30), at(10)),
        shift("SPLIT", at(10, 30), at(13, 30)),
    )
    fixed = (FixedAssignment("SPLIT", flights[0]),)
    config = one_person_config(solver_time_limit_seconds=8.0)

    result = optimize_flight_assignments(
        day_for(flights, workers, shifts, fixed=fixed), config
    )

    assert result.status is OptimizationStatus.OPTIMAL
    assert all(item.proven_optimal for item in result.objective_values)
    assert result.objective_values[0].value == 10
    assert result.objective_values[1].value == 5
    assert result.objective_values[2].value == 10
    assert result.objective_values[5].value == 0
    assert result.objective_values[6].value == 10
    assert result.objective_values[9].value == 1
    assert result.objective_values[10].value == 4
    assert flight_counts(result) == {
        "LONG": 3,
        "MID": 3,
        "SHORT": 2,
        "SPLIT": 2,
    }
    assert result.flight_results[0].fixed_employee_ids == ("SPLIT",)
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_assignments == 10
    assert result.fairness_metrics.flight_count_spread == 1
    assert result.fairness_metrics.total_participating_shift_minutes == 1770
    assert result.solver_runtime_seconds <= config.solver_time_limit_seconds + 2.0
