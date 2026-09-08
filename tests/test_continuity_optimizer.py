"""Deterministic Milestone 11 emergent team-continuity tests."""

from dataclasses import replace
from datetime import date, datetime, timedelta

from ortools.sat.python import cp_model
import pytest

import ramp_optimizer.optimizer as optimizer_module
from ramp_optimizer import (
    ContinuityMetrics,
    ContinuityTransitionResult,
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


def at(hour: int, minute: int = 0, *, day: int = 2) -> datetime:
    return datetime(2026, 9, day, hour, minute)


def arrival_from_start(number: str, start: datetime, *, heavy: bool = False) -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=start + timedelta(minutes=10),
        heavy=heavy,
    )


def departure_from_start(number: str, start: datetime) -> Flight:
    return Flight(
        departure_flight_number=number,
        departure_time=start + timedelta(minutes=60),
    )


def turn_from_start(
    arrival_number: str,
    departure_number: str,
    start: datetime,
) -> Flight:
    return Flight(
        arrival_flight_number=arrival_number,
        arrival_time=start + timedelta(minutes=10),
        departure_flight_number=departure_number,
        departure_time=start + timedelta(minutes=60),
    )


def employee(employee_id: str, *, qualified: bool = True) -> Employee:
    qualifications = (
        frozenset({Qualification.PUSH, Qualification.CLOSE_OUT})
        if qualified
        else frozenset()
    )
    return Employee(employee_id, f"Employee {employee_id}", qualifications)


def shift(
    employee_id: str,
    start: datetime = datetime(2026, 9, 2, 5),
    end: datetime = datetime(2026, 9, 2, 18),
) -> EmployeeShift:
    return EmployeeShift(
        employee_id,
        start,
        end,
        OperationalRole.RAMP_AGENT,
    )


def config(staff: int = 1, **changes) -> OptimizerConfig:
    values = {
        "minimum_staff": staff,
        "normal_preferred_staff": staff,
        "heavy_preferred_staff": staff,
    }
    values.update(changes)
    return replace(OptimizerConfig(), **values)


def day_for(
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


def fixed_crew_result(
    flights: tuple[Flight, ...],
    crews: tuple[tuple[str, ...], ...],
    *,
    active_config: OptimizerConfig,
    employee_shifts: tuple[EmployeeShift, ...] | None = None,
):
    employee_ids = tuple(dict.fromkeys(member for crew in crews for member in crew))
    workers = tuple(employee(employee_id) for employee_id in employee_ids)
    fixed = tuple(
        FixedAssignment(employee_id, flight)
        for flight, crew in zip(flights, crews)
        for employee_id in crew
    )
    return optimize_flight_assignments(
        day_for(
            flights,
            workers,
            shifts=employee_shifts,
            fixed=fixed,
        ),
        active_config,
    )


def metrics(result) -> ContinuityMetrics:
    assert result.continuity_metrics is not None
    return result.continuity_metrics


def test_full_team_continuity_reports_four_retained_employees() -> None:
    flights = (
        arrival_from_start("101", at(8)),
        arrival_from_start("102", at(9, 30)),
    )
    crew = ("A", "B", "C", "D")
    result = fixed_crew_result(
        flights,
        (crew, crew),
        active_config=config(4),
    )
    continuity = metrics(result)
    transition = continuity.transitions[0]

    assert continuity.eligible_transition_count == 1
    assert continuity.total_retained_employee_transitions == 4
    assert continuity.average_retained_employees_per_transition == 4.0
    assert continuity.strongest_retention_count == 4
    assert continuity.strongest_transition == transition
    assert transition == ContinuityTransitionResult(
        previous_flight=flights[0],
        next_flight=flights[1],
        retained_employee_ids=crew,
        retention_count=4,
    )
    assert result.objective_values[16].value == 4


def test_partial_team_continuity_reports_two_retained_employees() -> None:
    flights = (
        arrival_from_start("201", at(8)),
        arrival_from_start("202", at(9, 30)),
    )
    result = fixed_crew_result(
        flights,
        (("A", "B", "C", "D"), ("A", "B", "E", "F")),
        active_config=config(4),
    )
    transition = metrics(result).transitions[0]

    assert transition.retained_employee_ids == ("A", "B")
    assert transition.retention_count == 2
    assert metrics(result).total_retained_employee_transitions == 2


def test_zero_team_continuity_keeps_an_explicit_zero_transition() -> None:
    flights = (
        arrival_from_start("301", at(8)),
        arrival_from_start("302", at(9, 30)),
    )
    result = fixed_crew_result(
        flights,
        (("A", "B", "C"), ("D", "E", "F")),
        active_config=config(3),
    )
    continuity = metrics(result)

    assert continuity.eligible_transition_count == 1
    assert continuity.total_retained_employee_transitions == 0
    assert continuity.average_retained_employees_per_transition == 0.0
    assert continuity.strongest_retention_count == 0
    assert continuity.strongest_transition == continuity.transitions[0]
    assert continuity.transitions[0].retained_employee_ids == ()


@pytest.mark.parametrize(
    ("gap_minutes", "expected_edges"),
    [(0, 1), (105, 1), (120, 1), (121, 0)],
    ids=("touching", "inside-horizon", "exact-horizon", "outside-horizon"),
)
def test_continuity_horizon_boundaries(
    gap_minutes: int,
    expected_edges: int,
) -> None:
    first_start = at(8)
    flights = (
        arrival_from_start("401", first_start),
        arrival_from_start(
            "402",
            first_start + timedelta(minutes=30 + gap_minutes),
        ),
    )
    result = fixed_crew_result(
        flights,
        (("A",), ("A",)),
        active_config=config(),
    )

    assert metrics(result).eligible_transition_count == expected_edges
    assert metrics(result).total_retained_employee_transitions == expected_edges


def test_nondefault_continuity_horizon_is_authoritative() -> None:
    first_start = at(8)
    flights = (
        arrival_from_start("501", first_start),
        arrival_from_start("502", first_start + timedelta(minutes=75)),
    )
    included = fixed_crew_result(
        flights,
        (("A",), ("A",)),
        active_config=config(continuity_horizon_minutes=45),
    )
    excluded = fixed_crew_result(
        flights,
        (("A",), ("A",)),
        active_config=config(continuity_horizon_minutes=44),
    )

    assert metrics(included).eligible_transition_count == 1
    assert metrics(excluded).eligible_transition_count == 0


def test_overlapping_flights_have_no_continuity_edge() -> None:
    flights = (
        arrival_from_start("601", at(8)),
        arrival_from_start("602", at(8, 20)),
    )
    result = fixed_crew_result(
        flights,
        (("A",), ("B",)),
        active_config=config(),
    )

    assert metrics(result).eligible_transition_count == 0
    assert metrics(result).transitions == ()
    assert metrics(result).strongest_transition is None


@pytest.mark.parametrize(
    ("first_kind", "second_kind"),
    [("arrival", "departure"), ("departure", "turn"), ("turn", "arrival")],
)
def test_continuity_is_independent_of_flight_type(
    first_kind: str,
    second_kind: str,
) -> None:
    def movement(kind: str, start: datetime, suffix: str) -> Flight:
        if kind == "arrival":
            return arrival_from_start(f"7{suffix}", start)
        if kind == "departure":
            return departure_from_start(f"8{suffix}", start)
        return turn_from_start(f"9{suffix}1", f"9{suffix}2", start)

    first = movement(first_kind, at(8), "01")
    first_duration = timedelta(minutes=30 if first_kind == "arrival" else 60)
    second = movement(second_kind, at(8) + first_duration + timedelta(minutes=60), "02")
    result = fixed_crew_result(
        (first, second),
        (("A",), ("A",)),
        active_config=config(),
    )

    assert metrics(result).eligible_transition_count == 1
    assert metrics(result).total_retained_employee_transitions == 1


def test_four_person_to_three_person_team_retains_three() -> None:
    flights = (
        arrival_from_start("1001", at(8)),
        arrival_from_start("1002", at(9, 30)),
    )
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C", "D"))
    shifts = (
        shift("A"),
        shift("B"),
        shift("C"),
        shift("D", at(7), at(8, 30)),
    )
    fixed = tuple(FixedAssignment(worker_id, flights[0]) for worker_id in "ABCD")
    fixed += tuple(FixedAssignment(worker_id, flights[1]) for worker_id in "ABC")
    result = optimize_flight_assignments(
        day_for(flights, workers, shifts=shifts, fixed=fixed),
        config(3, normal_preferred_staff=4, heavy_preferred_staff=4),
    )

    assert [item.staffing_count for item in result.flight_results] == [4, 3]
    assert metrics(result).transitions[0].retention_count == 3


def test_four_person_team_retained_when_fifth_joins_heavy_flight() -> None:
    flights = (
        arrival_from_start("1101", at(8)),
        arrival_from_start("1102", at(9, 30), heavy=True),
    )
    crew = ("A", "B", "C", "D")
    result = fixed_crew_result(
        flights,
        (crew, crew + ("E",)),
        active_config=config(
            4,
            normal_preferred_staff=4,
            heavy_preferred_staff=5,
        ),
    )

    assert [item.staffing_count for item in result.flight_results] == [4, 5]
    assert metrics(result).transitions[0].retention_count == 4


def test_express_and_mainline_flights_share_normal_continuity_rules() -> None:
    flights = (
        arrival_from_start("1201", at(8)),
        arrival_from_start("3201", at(9, 30)),
    )
    result = fixed_crew_result(
        flights,
        (("A", "B"), ("A", "B")),
        active_config=config(2),
    )

    assert result.flight_results[0].express is False
    assert result.flight_results[1].express is True
    assert metrics(result).transitions[0].retention_count == 2


@pytest.mark.parametrize(
    ("first_selected", "second_selected", "expected_retained"),
    [(0, 0, 0), (0, 1, 0), (1, 0, 0), (1, 1, 1)],
)
def test_retained_auxiliary_is_the_exact_boolean_and(
    first_selected: int,
    second_selected: int,
    expected_retained: int,
) -> None:
    flights = (
        arrival_from_start("1301", at(8)),
        arrival_from_start("1302", at(9, 30)),
    )
    operational_day = day_for(flights, (employee("A"),))
    active_config = config()
    model_data = optimizer_module._build_model(
        operational_day,
        active_config,
        build_candidate_assignments(operational_day, active_config),
    )
    model_data.model.add(model_data.decisions[(0, 0)] == first_selected)
    model_data.model.add(model_data.decisions[(0, 1)] == second_selected)
    solver = cp_model.CpSolver()

    assert solver.solve(model_data.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    retained = model_data.retained_employee_transitions[(0, 0, 1)]
    assert solver.value(retained) == expected_retained


def test_continuity_stage_creates_emergent_teams_after_all_fairness_ties() -> None:
    flights = tuple(
        arrival_from_start(
            str(1401 + index),
            at(8) + timedelta(minutes=90 * index),
        )
        for index in range(4)
    )
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C", "D"))
    result = optimize_flight_assignments(
        day_for(flights, workers),
        config(2),
    )

    assert result.status is OptimizationStatus.OPTIMAL
    assert [item.flight_count for item in result.employee_results] == [2, 2, 2, 2]
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.flight_count_spread == 0
    assert result.fairness_metrics.maximum_consecutive_streak == 1
    assert metrics(result).eligible_transition_count == 3
    assert metrics(result).total_retained_employee_transitions == 4
    assert result.objective_values[16].value == 4


def test_raw_fairness_dominates_continuity_and_splits_the_team() -> None:
    flights = (
        arrival_from_start("1501", at(8)),
        arrival_from_start("1502", at(9, 30)),
    )
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C", "D"))
    result = optimize_flight_assignments(
        day_for(flights, workers),
        config(2),
    )

    assert result.fairness_metrics is not None
    assert result.fairness_metrics.flight_count_spread == 0
    assert sorted(item.flight_count for item in result.employee_results) == [1] * 4
    assert metrics(result).total_retained_employee_transitions == 0


def anchor_choice_day() -> tuple[OperationalDay, tuple[Flight, ...]]:
    flights = (
        arrival_from_start("1601", at(8)),
        arrival_from_start("1602", at(8, 50)),
        arrival_from_start("1603", at(12)),
    )
    workers = (employee("A"), employee("B"))
    operational_day = day_for(
        flights,
        workers,
        fixed=(
            FixedAssignment("A", flights[0]),
            FixedAssignment("B", flights[2]),
        ),
    )
    return operational_day, flights


def test_required_break_dominates_continuity() -> None:
    operational_day, _ = anchor_choice_day()
    result = optimize_flight_assignments(
        operational_day,
        config(required_break_minutes=30, consecutive_reset_minutes=10),
    )

    assert result.flight_results[1].assigned_employee_ids == ("B",)
    assert result.objective_values[5].value == 0
    assert metrics(result).total_retained_employee_transitions == 0


def test_consecutive_streak_fairness_dominates_continuity() -> None:
    operational_day, _ = anchor_choice_day()
    result = optimize_flight_assignments(
        operational_day,
        config(required_break_minutes=10, consecutive_reset_minutes=40),
    )

    assert result.flight_results[1].assigned_employee_ids == ("B",)
    assert result.objective_values[11].value == 1
    assert metrics(result).total_retained_employee_transitions == 0


def test_shift_length_fairness_dominates_continuity() -> None:
    operational_day, flights = anchor_choice_day()
    shifted_day = OperationalDay(
        operational_day.operational_date,
        operational_day.employees,
        (
            shift("A", at(7, 30), at(10)),
            shift("B", at(7, 30), at(14)),
        ),
        flights,
        operational_day.fixed_assignments,
    )
    result = optimize_flight_assignments(
        shifted_day,
        config(required_break_minutes=10, consecutive_reset_minutes=10),
    )

    assert result.flight_results[1].assigned_employee_ids == ("B",)
    assert metrics(result).total_retained_employee_transitions == 0
    assert result.objective_values[13].proven_optimal


def test_adjusted_workload_fairness_dominates_continuity() -> None:
    flights = (
        arrival_from_start("1701", at(8)),
        arrival_from_start("3701", at(9, 30)),
        arrival_from_start("3702", at(12, 1)),
    )
    workers = (employee("A"), employee("B"))
    result = optimize_flight_assignments(
        day_for(
            flights,
            workers,
            fixed=(
                FixedAssignment("A", flights[0]),
                FixedAssignment("B", flights[2]),
            ),
        ),
        config(required_break_minutes=10),
    )

    assert result.flight_results[1].assigned_employee_ids == ("B",)
    assert result.objective_values[14].proven_optimal
    assert result.objective_values[15].proven_optimal
    assert metrics(result).total_retained_employee_transitions == 0


def test_qualification_coverage_dominates_continuity() -> None:
    previous = arrival_from_start("1801", at(8))
    target = departure_from_start("1802", at(9, 30))
    workers = (
        employee("UNQUALIFIED", qualified=False),
        employee("QUALIFIED"),
    )
    result = optimize_flight_assignments(
        day_for(
            (previous, target),
            workers,
            fixed=(FixedAssignment("UNQUALIFIED", previous),),
        ),
        config(),
    )

    assert result.flight_results[1].assigned_employee_ids == ("QUALIFIED",)
    assert result.objective_values[1].value == 1
    assert result.objective_values[2].value == 2
    assert metrics(result).total_retained_employee_transitions == 0


def test_preferred_staffing_and_staffing_caps_dominate_continuity() -> None:
    flights = (
        arrival_from_start("1901", at(8)),
        arrival_from_start("1902", at(9, 30)),
    )
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C", "D", "E"))
    result = optimize_flight_assignments(
        day_for(flights, workers),
        config(3, normal_preferred_staff=4, heavy_preferred_staff=5),
    )

    assert [item.staffing_count for item in result.flight_results] == [4, 4]
    assert all(
        item.staffing_count <= item.maximum_staff
        for item in result.flight_results
    )
    assert result.objective_values[6].value == 2
    assert sum(item.flight_count for item in result.employee_results) == 8


def test_stage_17_unknown_retains_stage_16_schedule_and_reports_it(
    monkeypatch,
) -> None:
    flights = (
        arrival_from_start("2001", at(8)),
        arrival_from_start("2002", at(9, 30)),
    )
    operational_day = day_for(
        flights,
        tuple(employee(worker_id) for worker_id in ("A", "B", "C", "D")),
    )
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
        return real_solver_type() if calls <= 16 else UnknownSolver()

    monkeypatch.setattr(optimizer_module.cp_model, "CpSolver", solver_factory)
    result = optimize_flight_assignments(operational_day, config(2))

    assert result.status is OptimizationStatus.FEASIBLE
    assert len(result.objective_values) == 17
    assert all(item.proven_optimal for item in result.objective_values[:16])
    assert result.objective_values[16].proven_optimal is False
    assert metrics(result).total_retained_employee_transitions == (
        result.objective_values[16].value
    )


@pytest.mark.integration
@pytest.mark.slow
def test_representative_day_has_bounded_continuity_model_and_solves() -> None:
    # Eight flights still exercise all-horizon pair growth and exact retention,
    # without conflating proof of correctness with an eight-second load test.
    flights = tuple(
        arrival_from_start(
            str(2100 + index),
            at(7) + timedelta(minutes=50 * index),
        )
        for index in range(8)
    )
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C", "D"))
    operational_day = day_for(flights, workers)
    active_config = config(
        required_break_minutes=10,
        solver_time_limit_seconds=8.0,
    )
    model_data = optimizer_module._build_model(
        operational_day,
        active_config,
        build_candidate_assignments(operational_day, active_config),
    )

    assert len(model_data.continuity_flight_pairs) == 18
    assert len(model_data.retained_employee_transitions) == 72

    result = optimize_flight_assignments(operational_day, active_config)

    assert result.status is OptimizationStatus.OPTIMAL
    assert len(result.objective_values) == 17
    assert all(item.proven_optimal for item in result.objective_values)
    assert metrics(result).eligible_transition_count == 18
    assert (
        result.solver_runtime_seconds
        <= active_config.solver_time_limit_seconds + 2.0
    )


def test_public_continuity_models_are_immutable_values() -> None:
    transition = ContinuityTransitionResult(
        previous_flight=arrival_from_start("2201", at(8)),
        next_flight=arrival_from_start("2202", at(9, 30)),
        retained_employee_ids=("A",),
        retention_count=1,
    )
    continuity = ContinuityMetrics(
        eligible_transition_count=1,
        total_retained_employee_transitions=1,
        average_retained_employees_per_transition=1.0,
        strongest_retention_count=1,
        strongest_transition=transition,
        transitions=(transition,),
    )

    assert continuity.transitions == (transition,)
    with pytest.raises(AttributeError):
        continuity.total_retained_employee_transitions = 2  # type: ignore[misc]
