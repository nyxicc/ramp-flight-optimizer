"""Deterministic Milestone 7 raw flight-count fairness tests."""

from dataclasses import replace
from datetime import date, datetime, timedelta

from ortools.sat.python import cp_model
import pytest

import ramp_optimizer.optimizer as optimizer_module
from ramp_optimizer import (
    BreakStatus,
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    OperationalDay,
    OperationalRole,
    OptimizationStatus,
    OptimizerConfig,
    Qualification,
    build_candidate_assignments,
    optimize_flight_assignments,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 2, hour, minute)


def arrival(number: str, hour: int, minute: int = 40) -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=at(hour, minute),
    )


def departure(number: str, hour: int, minute: int = 0) -> Flight:
    return Flight(
        departure_flight_number=number,
        departure_time=at(hour, minute),
    )


def turn(
    arrival_number: str,
    departure_number: str,
    arrival_hour: int = 9,
    departure_hour: int = 10,
) -> Flight:
    return Flight(
        arrival_flight_number=arrival_number,
        arrival_time=at(arrival_hour),
        departure_flight_number=departure_number,
        departure_time=at(departure_hour),
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
    start: datetime = datetime(2026, 9, 2, 5),
    end: datetime = datetime(2026, 9, 2, 18),
    role: OperationalRole = OperationalRole.RAMP_AGENT,
) -> EmployeeShift:
    return EmployeeShift(employee_id, start, end, role)


def operational_day(
    flights: tuple[Flight, ...],
    employees: tuple[Employee, ...],
    *,
    shifts: tuple[EmployeeShift, ...] | None = None,
    fixed: tuple[FixedAssignment, ...] = (),
) -> OperationalDay:
    return OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=(
            shifts
            if shifts is not None
            else tuple(shift(worker.employee_id) for worker in employees)
        ),
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


def employee_counts(result) -> dict[str, int]:
    return {
        employee_result.employee_id: employee_result.flight_count
        for employee_result in result.employee_results
    }


def assigned_count(result) -> int:
    return sum(item.staffing_count for item in result.flight_results)


def test_equal_distribution_when_equal_schedule_is_possible() -> None:
    flights = tuple(arrival(str(100 + index), 7 + index) for index in range(6))
    workers = tuple(employee(f"E{index}") for index in range(3))

    result = optimize_flight_assignments(
        operational_day(flights, workers), one_person_config()
    )

    assert sorted(employee_counts(result).values()) == [2, 2, 2]
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.flight_count_spread == 0
    assert result.objective_values[9].value == 0
    assert result.objective_values[10].value == 0


def test_near_equal_distribution_when_total_is_not_evenly_divisible() -> None:
    flights = tuple(arrival(str(100 + index), 7 + index) for index in range(5))
    workers = tuple(employee(f"E{index}") for index in range(3))

    result = optimize_flight_assignments(
        operational_day(flights, workers), one_person_config()
    )

    assert sorted(employee_counts(result).values()) == [1, 2, 2]
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.flight_count_spread == 1
    assert result.objective_values[10].value == 2


def test_fairness_objectives_follow_all_existing_objectives() -> None:
    result = optimize_flight_assignments(
        operational_day((arrival("101", 8),), (employee("E1"),)),
        one_person_config(),
    )

    assert [(item.stage, item.name) for item in result.objective_values[9:11]] == [
        (10, "raw_flight_count_spread"),
        (11, "total_pairwise_flight_count_difference"),
    ]
    assert len(result.objective_values) == 12


def test_pairwise_stage_resolves_a_tied_spread_in_the_middle() -> None:
    flights = (
        arrival("101", 7),
        arrival("102", 8),
        arrival("103", 10),
        arrival("104", 11),
    )
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C", "D"))
    shifts = (
        shift("A", at(7, 30), at(9)),
        shift("B", at(10, 30), at(12)),
        shift("C", at(10, 30), at(12)),
        shift("D", at(7, 30), at(9)),
    )
    fixed = (
        FixedAssignment("D", flights[0]),
        FixedAssignment("D", flights[1]),
    )

    result = optimize_flight_assignments(
        operational_day(flights, workers, shifts=shifts, fixed=fixed),
        one_person_config(),
    )

    assert employee_counts(result) == {"A": 0, "B": 1, "C": 1, "D": 2}
    assert result.objective_values[9].value == 2
    assert result.objective_values[10].value == 6


def test_minimum_staffed_flight_count_is_not_sacrificed_for_fairness() -> None:
    flights = (arrival("101", 9), arrival("102", 9))
    workers = tuple(employee(f"E{index}") for index in range(3))
    config = one_person_config(
        minimum_staff=2,
        normal_preferred_staff=2,
        heavy_preferred_staff=2,
    )

    result = optimize_flight_assignments(operational_day(flights, workers), config)

    assert result.objective_values[0].value == 1
    assert sorted(item.staffing_count for item in result.flight_results) == [1, 2]


def test_qualification_compliance_is_not_sacrificed_for_fairness() -> None:
    flights = (departure("101", 9), departure("102", 10))
    workers = (
        employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT),
        employee("NONE"),
    )

    result = optimize_flight_assignments(
        operational_day(flights, workers), one_person_config()
    )

    assert employee_counts(result) == {"DUAL": 2, "NONE": 0}
    assert result.objective_values[1].value == 2
    assert all(
        item.push_covered is True and item.close_covered is True
        for item in result.flight_results
    )


def test_individual_qualification_coverage_is_not_sacrificed() -> None:
    flights = (departure("101", 9), departure("102", 9))
    workers = (
        employee("PUSH", Qualification.PUSH),
        employee("CLOSE", Qualification.CLOSE_OUT),
    )

    result = optimize_flight_assignments(
        operational_day(flights, workers), one_person_config()
    )

    assert result.objective_values[2].value == 2
    assert sum(bool(item.push_covered) for item in result.flight_results) == 1
    assert sum(bool(item.close_covered) for item in result.flight_results) == 1


def test_minimum_shortfall_distribution_is_not_worsened_for_fairness() -> None:
    flights = (arrival("101", 9), arrival("102", 9))
    workers = tuple(employee(f"E{index}") for index in range(5))

    result = optimize_flight_assignments(operational_day(flights, workers))

    assert sorted(item.staffing_count for item in result.flight_results) == [2, 3]
    assert result.objective_values[3].value == 1
    assert result.objective_values[4].value == 1


def test_required_break_is_not_interrupted_for_fairness() -> None:
    first = arrival("101", 8)
    intervening = arrival("102", 9, 15)
    last = arrival("103", 10, 10)
    workers = (employee("A"), employee("B"))
    shifts = (
        shift("A"),
        shift("B", at(9, 5), at(9, 35)),
    )
    fixed = (
        FixedAssignment("A", first),
        FixedAssignment("A", last),
        FixedAssignment("B", intervening),
    )
    config = one_person_config(
        normal_preferred_staff=2,
        heavy_preferred_staff=2,
    )

    result = optimize_flight_assignments(
        operational_day(
            (first, intervening, last), workers, shifts=shifts, fixed=fixed
        ),
        config,
    )

    assert result.flight_results[1].assigned_employee_ids == ("B",)
    assert employee_counts(result) == {"A": 2, "B": 1}
    assert result.employee_results[0].break_status is BreakStatus.SATISFIED
    assert result.objective_values[5].value == 0


def test_preferred_staffing_wins_and_fairness_adds_nothing_beyond_it() -> None:
    workers = tuple(employee(f"E{index}") for index in range(4))
    config = one_person_config(
        normal_preferred_staff=2,
        heavy_preferred_staff=2,
    )

    result = optimize_flight_assignments(
        operational_day((arrival("101", 9),), workers), config
    )

    assert result.objective_values[6].value == 1
    assert result.objective_values[7].value == 0
    assert assigned_count(result) == 2
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_assignments == 2


def test_partial_crew_qualification_coverage_wins_over_fairness() -> None:
    arriving = arrival("201", 9, 0)
    departing = departure("101", 9)
    workers = (
        employee("PUSH", Qualification.PUSH),
        employee("SHARED_NONE"),
        employee("ARRIVAL_ONLY1"),
        employee("ARRIVAL_ONLY2"),
    )
    shifts = (
        shift("PUSH"),
        shift("SHARED_NONE"),
        shift("ARRIVAL_ONLY1", at(8, 45), at(9, 30)),
        shift("ARRIVAL_ONLY2", at(8, 45), at(9, 30)),
    )

    result = optimize_flight_assignments(
        operational_day((arriving, departing), workers, shifts=shifts)
    )

    assert result.objective_values[8].value == 1
    assert result.flight_results[1].assigned_employee_ids == ("PUSH",)
    assert result.flight_results[1].push_covered is True


def test_optional_assignments_balance_around_fixed_work() -> None:
    flights = tuple(arrival(str(100 + index), 7 + index) for index in range(6))
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C"))
    fixed = (
        FixedAssignment("A", flights[0]),
        FixedAssignment("A", flights[1]),
    )

    result = optimize_flight_assignments(
        operational_day(flights, workers, fixed=fixed), one_person_config()
    )

    assert employee_counts(result) == {"A": 2, "B": 2, "C": 2}
    assert result.flight_results[0].fixed_employee_ids == ("A",)
    assert result.flight_results[1].fixed_employee_ids == ("A",)
    assert all(
        "A" in item.assigned_employee_ids for item in result.flight_results[:2]
    )


def test_legal_opportunity_with_zero_final_assignments_stays_in_population() -> None:
    workers = (employee("A"), employee("B"))

    result = optimize_flight_assignments(
        operational_day((arrival("101", 9),), workers), one_person_config()
    )

    assert sorted(employee_counts(result).values()) == [0, 1]
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 2
    assert result.fairness_metrics.lowest_flight_count == 0


def test_employee_without_a_legal_opportunity_is_excluded_from_fairness() -> None:
    workers = (employee("AVAILABLE"), employee("OUTSIDE"))
    shifts = (
        shift("AVAILABLE", at(8, 30), at(9)),
        shift("OUTSIDE", at(15), at(16)),
    )

    result = optimize_flight_assignments(
        operational_day((arrival("101", 8),), workers, shifts=shifts),
        one_person_config(),
    )

    assert tuple(item.employee_id for item in result.employee_results) == (
        "AVAILABLE",
        "OUTSIDE",
    )
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 1


def test_disabled_lead_non_ramp_unknown_and_no_shift_are_excluded() -> None:
    workers = (
        employee("RAMP"),
        employee("DISABLED", enabled=False),
        employee("LEAD"),
        employee("NON_RAMP"),
        employee("UNKNOWN"),
        employee("NO_SHIFT"),
    )
    shifts = (
        shift("RAMP"),
        shift("DISABLED"),
        shift("LEAD", role=OperationalRole.RAMP_LEAD),
        shift("NON_RAMP", role=OperationalRole.NON_RAMP),
        shift("UNKNOWN", role=OperationalRole.UNKNOWN),
    )

    result = optimize_flight_assignments(
        operational_day((arrival("101", 9),), workers, shifts=shifts),
        one_person_config(),
    )

    assert tuple(item.employee_id for item in result.employee_results) == ("RAMP",)
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 1


def test_fixed_only_employee_is_a_fairness_participant() -> None:
    target = arrival("101", 9)
    workers = (employee("FIXED"),)

    result = optimize_flight_assignments(
        operational_day(
            (target,),
            workers,
            fixed=(FixedAssignment("FIXED", target),),
        ),
        one_person_config(),
    )

    assert employee_counts(result) == {"FIXED": 1}
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 1
    assert result.fairness_metrics.total_assignments == 1
    assert result.fairness_metrics.flight_count_spread == 0
    assert result.objective_values[10].value == 0


@pytest.mark.parametrize(
    "target",
    [
        arrival("101", 9),
        departure("102", 10),
        turn("201", "202", 11, 12),
    ],
    ids=("arrival", "departure", "turn"),
)
def test_each_movement_type_counts_as_exactly_one_assignment(target: Flight) -> None:
    worker = employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT)

    result = optimize_flight_assignments(
        operational_day((target,), (worker,)), one_person_config()
    )

    assert employee_counts(result) == {"DUAL": 1}
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_assignments == 1


def test_overlapping_assignments_remain_illegal() -> None:
    flights = (arrival("101", 9), arrival("102", 9))

    result = optimize_flight_assignments(
        operational_day(flights, (employee("E1"),)), one_person_config()
    )

    assert assigned_count(result) == 1
    assert employee_counts(result) == {"E1": 1}


def test_fairness_never_assigns_outside_an_employee_shift() -> None:
    flights = (arrival("101", 8), arrival("102", 15))
    workers = (employee("E1"),)

    result = optimize_flight_assignments(
        operational_day(
            flights,
            workers,
            shifts=(shift("E1", at(8, 30), at(9)),),
        ),
        one_person_config(),
    )

    assert result.flight_results[0].assigned_employee_ids == ("E1",)
    assert result.flight_results[1].assigned_employee_ids == ()
    assert employee_counts(result) == {"E1": 1}


def test_empty_day_has_zero_valued_fairness_metrics() -> None:
    result = optimize_flight_assignments(OperationalDay(date(2026, 9, 2)))

    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 0
    assert result.fairness_metrics.total_assignments == 0
    assert result.fairness_metrics.average_flights == 0.0
    assert result.fairness_metrics.highest_flight_count == 0
    assert result.fairness_metrics.lowest_flight_count == 0
    assert result.fairness_metrics.flight_count_spread == 0
    assert result.objective_values[9].value == 0
    assert result.objective_values[10].value == 0


def test_two_participants_have_exact_public_spread_and_pairwise_values() -> None:
    flights = tuple(arrival(str(100 + index), 8 + index) for index in range(3))
    workers = (employee("A"), employee("B"))

    result = optimize_flight_assignments(
        operational_day(flights, workers), one_person_config()
    )

    assert sorted(employee_counts(result).values()) == [1, 2]
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_assignments == 3
    assert result.fairness_metrics.average_flights == 1.5
    assert result.fairness_metrics.highest_flight_count == 2
    assert result.fairness_metrics.lowest_flight_count == 1
    assert result.fairness_metrics.flight_count_spread == 1
    assert result.objective_values[10].value == 1


def test_three_participant_extrema_and_pairwise_links_are_exact() -> None:
    flights = (
        arrival("101", 7),
        arrival("102", 8),
        arrival("103", 9),
    )
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C"))
    fixed = (
        FixedAssignment("B", flights[0]),
        FixedAssignment("C", flights[1]),
        FixedAssignment("C", flights[2]),
    )
    day = operational_day(flights, workers, fixed=fixed)
    config = one_person_config()

    result = optimize_flight_assignments(day, config)
    model_data = optimizer_module._build_model(
        day, config, build_candidate_assignments(day, config)
    )
    solver = cp_model.CpSolver()

    assert solver.solve(model_data.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    assert employee_counts(result) == {"A": 0, "B": 1, "C": 2}
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_assignments == 3
    assert result.fairness_metrics.average_flights == 1.0
    assert result.fairness_metrics.highest_flight_count == 2
    assert result.fairness_metrics.lowest_flight_count == 0
    assert result.fairness_metrics.flight_count_spread == 2
    assert result.objective_values[10].value == 4
    assert solver.value(model_data.highest_flight_count) == 2
    assert solver.value(model_data.lowest_flight_count) == 0
    assert solver.value(model_data.flight_count_spread) == 2
    assert sorted(
        solver.value(item)
        for item in model_data.pairwise_flight_count_differences
    ) == [1, 1, 2]
    assert solver.value(model_data.total_pairwise_flight_count_difference) == 4


def test_public_counts_order_and_future_fields_match_final_assignments() -> None:
    workers = tuple(employee(worker_id) for worker_id in ("E3", "E1", "E2"))
    flights = (arrival("101", 8), arrival("3001", 10))

    result = optimize_flight_assignments(
        operational_day(flights, workers), one_person_config()
    )

    assert tuple(item.employee_id for item in result.employee_results) == (
        "E3",
        "E1",
        "E2",
    )
    assert all(
        item.flight_count == len(item.assigned_flights)
        and item.longest_consecutive_streak is None
        and item.adjusted_workload is None
        for item in result.employee_results
    )
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_assignments == assigned_count(result)
    assert result.fairness_metrics.maximum_consecutive_streak is None
    assert result.fairness_metrics.adjusted_workload_spread is None


def test_fairness_population_preserves_original_employee_order() -> None:
    workers = tuple(employee(worker_id) for worker_id in ("E3", "E1", "E2"))
    shifts = (
        shift("E3"),
        shift("E1", at(15), at(16)),
        shift("E2"),
    )
    day = operational_day((arrival("101", 9),), workers, shifts=shifts)
    config = one_person_config()

    model_data = optimizer_module._build_model(
        day, config, build_candidate_assignments(day, config)
    )

    assert model_data.fairness_employee_indices == (0, 2)


def test_repeated_seeded_runs_return_equivalent_fairness_results() -> None:
    flights = tuple(arrival(str(100 + index), 7 + index) for index in range(5))
    workers = tuple(employee(f"E{index}") for index in range(3))
    day = operational_day(flights, workers)
    config = one_person_config()

    first = optimize_flight_assignments(day, config)
    second = optimize_flight_assignments(day, config)

    assert first.flight_results == second.flight_results
    assert first.employee_results == second.employee_results
    assert first.fairness_metrics == second.fairness_metrics
    assert first.objective_values == second.objective_values


@pytest.mark.parametrize(
    ("completed_stages", "expected_objectives", "incomplete_stage"),
    [(9, 10, 10), (10, 11, 11)],
    ids=("first-fairness-stage", "second-fairness-stage"),
)
def test_fairness_stage_timeout_preserves_the_last_feasible_schedule(
    monkeypatch,
    completed_stages: int,
    expected_objectives: int,
    incomplete_stage: int,
) -> None:
    flights = (arrival("101", 8), arrival("102", 9))
    workers = (employee("A"), employee("B"))
    day = operational_day(flights, workers)
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
        return real_solver_type() if calls <= completed_stages else UnknownSolver()

    monkeypatch.setattr(optimizer_module.cp_model, "CpSolver", solver_factory)

    result = optimize_flight_assignments(day, one_person_config())

    assert result.status is OptimizationStatus.FEASIBLE
    assert len(result.objective_values) == expected_objectives
    assert all(item.proven_optimal for item in result.objective_values[:-1])
    assert result.objective_values[-1].stage == incomplete_stage
    assert result.objective_values[-1].proven_optimal is False
    assert assigned_count(result) == 2
    assert result.fairness_metrics is not None
    if completed_stages == 10:
        assert result.fairness_metrics.flight_count_spread == 0


def test_moderate_mixed_day_completes_with_fairness_and_priorities() -> None:
    flights = (
        arrival("101", 8, 0),
        departure("201", 8, 30),
        arrival("102", 9, 15),
        departure("202", 10),
        turn("301", "302", 10, 11),
        arrival("103", 12, 15),
        departure("203", 13, 30),
        arrival("104", 14, 15),
    )
    workers = (
        employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT),
        employee("PUSH", Qualification.PUSH),
        employee("CLOSE", Qualification.CLOSE_OUT),
        employee("R1"),
        employee("EARLY"),
        employee("LATE", Qualification.PUSH, Qualification.CLOSE_OUT),
    )
    shifts = (
        shift("DUAL"),
        shift("PUSH"),
        shift("CLOSE"),
        shift("R1"),
        shift("EARLY", at(5), at(10)),
        shift("LATE", at(9), at(18)),
    )
    fixed = (FixedAssignment("R1", flights[0]),)
    config = one_person_config(
        minimum_staff=2,
        normal_preferred_staff=2,
        heavy_preferred_staff=2,
        solver_time_limit_seconds=8.0,
    )

    result = optimize_flight_assignments(
        operational_day(flights, workers, shifts=shifts, fixed=fixed), config
    )

    assert result.status in {OptimizationStatus.OPTIMAL, OptimizationStatus.FEASIBLE}
    assert result.objective_values[0].value == 8
    assert result.objective_values[1].value == 4
    assert result.objective_values[2].value == 8
    assert result.objective_values[5].value == 0
    assert result.objective_values[6].value == 8
    assert assigned_count(result) == 16
    assert result.flight_results[0].fixed_employee_ids == ("R1",)
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_assignments == 16
    assert result.fairness_metrics.flight_count_spread == 1
    assert result.solver_runtime_seconds <= config.solver_time_limit_seconds + 2.0
