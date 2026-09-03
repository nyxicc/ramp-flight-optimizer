"""Deterministic Milestone 9 adjusted-workload fairness tests."""

from dataclasses import replace
from datetime import date, datetime

from ortools.sat.python import cp_model
import pytest

import ramp_optimizer.optimizer as optimizer_module
from ramp_optimizer import (
    BreakStatus,
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
    WorkloadConfigurationError,
    adjusted_assignment_workload_units,
    build_candidate_assignments,
    derive_flight_operational_facts,
    optimize_flight_assignments,
    validate_config,
    workload_unit_scale,
    workload_units_to_public_value,
)


def at(hour: int, minute: int = 0, *, day: int = 2) -> datetime:
    return datetime(2026, 9, day, hour, minute)


def arrival(
    number: str,
    hour: int,
    minute: int = 0,
    *,
    day: int = 2,
    heavy: bool = False,
) -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=at(hour, minute, day=day),
        heavy=heavy,
    )


def departure(number: str, hour: int, minute: int = 0) -> Flight:
    return Flight(
        departure_flight_number=number,
        departure_time=at(hour, minute),
    )


def employee(
    employee_id: str,
    *qualifications: Qualification,
) -> Employee:
    return Employee(
        employee_id,
        f"Employee {employee_id}",
        frozenset(qualifications),
    )


def shift(
    employee_id: str,
    start: datetime = datetime(2026, 9, 2, 5),
    end: datetime = datetime(2026, 9, 2, 18),
    role: OperationalRole = OperationalRole.RAMP_AGENT,
) -> EmployeeShift:
    return EmployeeShift(employee_id, start, end, role)


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


def staffing_config(
    preferred: int = 1,
    *,
    heavy_preferred: int | None = None,
    **changes: object,
) -> OptimizerConfig:
    values: dict[str, object] = {
        "minimum_staff": 1,
        "normal_preferred_staff": preferred,
        "heavy_preferred_staff": (
            preferred if heavy_preferred is None else heavy_preferred
        ),
    }
    values.update(changes)
    return replace(OptimizerConfig(), **values)


def results_by_id(result) -> dict[str, object]:
    return {item.employee_id: item for item in result.employee_results}


@pytest.mark.parametrize(
    ("number", "staffing_count", "heavy", "expected_units", "expected_public"),
    [
        ("100", 4, False, 10_000, 1.00),
        ("3000", 4, False, 8_000, 0.80),
        ("100", 3, False, 11_500, 1.15),
        ("3000", 3, False, 9_200, 0.92),
        ("100", 2, False, 10_000, 1.00),
        ("3000", 2, False, 8_000, 0.80),
        ("100", 5, True, 10_000, 1.00),
        ("100", 3, True, 11_500, 1.15),
        ("3000", 5, True, 8_000, 0.80),
        ("3000", 3, True, 9_200, 0.92),
    ],
)
def test_pure_workload_derivation_default_combinations(
    number: str,
    staffing_count: int,
    heavy: bool,
    expected_units: int,
    expected_public: float,
) -> None:
    config = OptimizerConfig()
    facts = derive_flight_operational_facts(
        arrival(number, 9, heavy=heavy),
        config,
    )

    units = adjusted_assignment_workload_units(facts, staffing_count, config)

    assert workload_unit_scale(config) == 10_000
    assert units == expected_units
    assert workload_units_to_public_value(units, config) == expected_public


def test_custom_exact_factors_preserve_the_product_without_early_rounding() -> None:
    config = replace(
        OptimizerConfig(),
        express_workload_factor=0.875,
        three_person_workload_multiplier=1.125,
        workload_scale=1000,
    )
    facts = derive_flight_operational_facts(arrival("3000", 9), config)

    units = adjusted_assignment_workload_units(facts, 3, config)

    assert units == 984_375
    assert workload_unit_scale(config) == 1_000_000
    assert workload_units_to_public_value(units, config) == 0.984375


@pytest.mark.parametrize("staffing_count", [0, -1, True, 3.0])
def test_pure_workload_rejects_invalid_staffing_counts(staffing_count) -> None:
    config = OptimizerConfig()
    facts = derive_flight_operational_facts(arrival("100", 9), config)

    with pytest.raises(ValueError, match="staffing_count"):
        adjusted_assignment_workload_units(facts, staffing_count, config)


def test_unrepresentable_workload_factors_are_structured_config_issues() -> None:
    config = replace(
        OptimizerConfig(),
        express_workload_factor=0.333,
        three_person_workload_multiplier=1.155,
    )

    issues = validate_config(config)

    assert {issue.code for issue in issues} >= {
        "UNREPRESENTABLE_EXPRESS_WORKLOAD_FACTOR",
        "UNREPRESENTABLE_THREE_PERSON_WORKLOAD_MULTIPLIER",
    }
    with pytest.raises(WorkloadConfigurationError):
        adjusted_assignment_workload_units(
            derive_flight_operational_facts(arrival("100", 9), config),
            3,
            config,
        )


def test_invalid_workload_scale_and_integer_range_are_structured_issues() -> None:
    invalid_scale = replace(OptimizerConfig(), workload_scale=0)
    excessive_scale = replace(OptimizerConfig(), workload_scale=4_000_000_000)

    assert any(
        issue.path == "config.workload_scale"
        for issue in validate_config(invalid_scale)
    )
    assert any(
        issue.code == "WORKLOAD_INTEGER_RANGE_EXCEEDED"
        for issue in validate_config(excessive_scale)
    )
    with pytest.raises(WorkloadConfigurationError):
        workload_unit_scale(invalid_scale)


def test_operational_day_workload_bounds_are_validated_before_model_building() -> None:
    config = staffing_config(1, workload_scale=1_000_000_000)
    flights = tuple(arrival(str(100 + index), 8 + index) for index in range(9))

    assert validate_config(config) == ()
    with pytest.raises(InputValidationError) as error:
        optimize_flight_assignments(day_for(flights, (employee("A"),)), config)

    assert any(
        issue.code == "WORKLOAD_INTEGER_RANGE_EXCEEDED"
        for issue in error.value.issues
    )


@pytest.mark.parametrize("staffing_count", [2, 3, 4, 5])
def test_exact_three_person_indicator_uses_literal_staffing_count(
    staffing_count: int,
) -> None:
    workers = tuple(employee(f"E{index}") for index in range(staffing_count))
    result = optimize_flight_assignments(
        day_for((arrival("100", 9),), workers),
        staffing_config(staffing_count),
    )

    assert result.flight_results[0].staffing_count == staffing_count
    assert all(
        item.three_person_flight_count == (1 if staffing_count == 3 else 0)
        for item in result.employee_results
    )
    assert all(
        item.adjusted_workload == (1.15 if staffing_count == 3 else 1.0)
        for item in result.employee_results
    )


def test_minimum_staff_one_does_not_redefine_three_person_work() -> None:
    result = optimize_flight_assignments(
        day_for((arrival("100", 9),), (employee("E1"),)),
        staffing_config(1),
    )

    assert result.flight_results[0].staffing_count == 1
    assert result.employee_results[0].three_person_flight_count == 0
    assert result.employee_results[0].adjusted_workload == 1.0


def test_fixed_employee_counts_toward_exactly_three_staffed_workload() -> None:
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C"))
    flight = arrival("100", 9)
    result = optimize_flight_assignments(
        day_for(
            (flight,),
            workers,
            fixed=(FixedAssignment("A", flight),),
        ),
        staffing_config(3),
    )

    assert result.flight_results[0].staffing_count == 3
    assert result.flight_results[0].fixed_employee_ids == ("A",)
    assert all(item.adjusted_workload == 1.15 for item in result.employee_results)


def test_two_mainline_and_two_express_flights_balance_one_of_each() -> None:
    flights = (
        arrival("100", 9),
        arrival("3000", 10, 30),
        arrival("101", 12),
        arrival("3001", 13, 30),
    )
    workers = (employee("A"), employee("B"))

    result = optimize_flight_assignments(
        day_for(flights, workers),
        staffing_config(1),
    )

    assert all(item.flight_count == 2 for item in result.employee_results)
    assert all(item.mainline_flight_count == 1 for item in result.employee_results)
    assert all(item.express_flight_count == 1 for item in result.employee_results)
    assert all(item.adjusted_workload == 1.8 for item in result.employee_results)
    assert result.objective_values[9].value == 0
    assert result.objective_values[12].value == 0
    assert result.objective_values[13].value == 0


def test_three_person_work_is_assigned_to_reduce_existing_workload_gap() -> None:
    baseline = (
        arrival("3000", 9),
        arrival("101", 9),
        arrival("102", 9),
        arrival("103", 9),
    )
    three_person = arrival("104", 11)
    single = arrival("105", 11, heavy=True)
    flights = baseline + (three_person, single)
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C", "D"))
    fixed = tuple(
        FixedAssignment(worker_id, baseline[index])
        for index, worker_id in enumerate(("A", "B", "C", "D"))
    )

    result = optimize_flight_assignments(
        day_for(flights, workers, fixed=fixed),
        staffing_config(3, heavy_preferred=4),
    )
    by_id = results_by_id(result)

    assert all(item.flight_count == 2 for item in result.employee_results)
    assert by_id["A"].three_person_flight_count == 1
    assert sorted(item.adjusted_workload for item in result.employee_results) == [
        1.95,
        2.0,
        2.15,
        2.15,
    ]
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.adjusted_workload_spread == 0.2


def test_express_exactly_three_combines_to_point_nine_two() -> None:
    flight = arrival("3000", 9)
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C"))

    result = optimize_flight_assignments(
        day_for((flight,), workers),
        staffing_config(3),
    )

    assert all(item.adjusted_workload == 0.92 for item in result.employee_results)
    assert all(item.three_person_flight_count == 1 for item in result.employee_results)


def test_pairwise_workload_stage_improves_tied_interior_distribution() -> None:
    first_three = arrival("3000", 8)
    first_single = arrival("3001", 8, heavy=True)
    second_three = arrival("100", 10)
    second_single = arrival("3002", 10, heavy=True)
    final_three = arrival("3003", 12)
    final_single = arrival("3004", 12, heavy=True)
    flights = (
        first_three,
        first_single,
        second_three,
        second_single,
        final_three,
        final_single,
    )
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C", "D"))
    fixed = (
        FixedAssignment("B", first_three),
        FixedAssignment("C", first_three),
        FixedAssignment("D", first_three),
        FixedAssignment("A", first_single),
        FixedAssignment("A", second_three),
        FixedAssignment("B", second_three),
        FixedAssignment("C", second_three),
        FixedAssignment("D", second_single),
    )

    result = optimize_flight_assignments(
        day_for(flights, workers, fixed=fixed),
        staffing_config(3, heavy_preferred=4),
    )

    assert all(item.flight_count == 3 for item in result.employee_results)
    assert sorted(item.adjusted_workload for item in result.employee_results) == [
        2.64,
        2.87,
        2.87,
        2.99,
    ]
    assert result.objective_values[12].value == 3_500
    assert result.objective_values[13].value == 10_500


def test_workload_never_worsens_raw_count_fairness() -> None:
    flights = (
        arrival("100", 9),
        arrival("101", 10, 30),
        arrival("3000", 12),
        arrival("3001", 13, 30),
    )
    result = optimize_flight_assignments(
        day_for(flights, (employee("A"), employee("B"))),
        staffing_config(1),
    )

    assert sorted(item.flight_count for item in result.employee_results) == [2, 2]
    assert result.objective_values[9].value == 0


def test_workload_never_worsens_shift_length_adjustment() -> None:
    flights = (
        arrival("3000", 9),
        arrival("3001", 10),
        arrival("100", 11),
    )
    workers = (employee("LONG"), employee("SHORT"))
    result = optimize_flight_assignments(
        day_for(
            flights,
            workers,
            shifts=(
                shift("LONG", at(8), at(16)),
                shift("SHORT", at(8), at(12)),
            ),
        ),
        staffing_config(1),
    )

    by_id = results_by_id(result)
    assert by_id["LONG"].flight_count == 2
    assert by_id["SHORT"].flight_count == 1
    assert result.objective_values[11].value == 0


def test_workload_stages_preserve_operational_qualification_break_and_preferred() -> None:
    workers = tuple(
        employee(worker_id, Qualification.PUSH, Qualification.CLOSE_OUT)
        for worker_id in ("A", "B", "C")
    )
    flights = (departure("100", 9), departure("101", 11))
    result = optimize_flight_assignments(day_for(flights, workers))

    assert [item.value for item in result.objective_values[:9]] == [
        2,
        2,
        4,
        0,
        0,
        0,
        0,
        2,
        0,
    ]
    assert all(item.minimum_met for item in result.flight_results)
    assert all(item.push_covered and item.close_covered for item in result.flight_results)
    assert all(
        item.break_status is BreakStatus.SATISFIED
        for item in result.employee_results
    )


def test_reporting_is_reconstructed_from_final_assignments() -> None:
    flights = (arrival("100", 9), arrival("3000", 11))
    workers = tuple(employee(worker_id) for worker_id in ("A", "B", "C"))
    config = staffing_config(3)
    result = optimize_flight_assignments(day_for(flights, workers), config)

    staffing_by_flight = {
        item.flight: item.staffing_count for item in result.flight_results
    }
    for employee_result in result.employee_results:
        expected_units = sum(
            adjusted_assignment_workload_units(
                derive_flight_operational_facts(flight, config),
                staffing_by_flight[flight],
                config,
            )
            for flight in employee_result.assigned_flights
        )
        assert employee_result.flight_count == len(employee_result.assigned_flights)
        assert (
            employee_result.mainline_flight_count
            + employee_result.express_flight_count
            == employee_result.flight_count
        )
        assert employee_result.three_person_flight_count == 2
        assert employee_result.adjusted_workload == workload_units_to_public_value(
            expected_units,
            config,
        )


def test_zero_and_one_participant_workload_metrics() -> None:
    empty = optimize_flight_assignments(OperationalDay(date(2026, 9, 2)))
    one = optimize_flight_assignments(
        day_for((arrival("100", 9),), (employee("A"),)),
        staffing_config(1),
    )

    assert empty.fairness_metrics is not None
    assert empty.fairness_metrics.adjusted_workload_spread == 0.0
    assert empty.objective_values[12].value == 0
    assert empty.objective_values[13].value == 0
    assert one.fairness_metrics is not None
    assert one.fairness_metrics.adjusted_workload_spread == 0.0
    assert one.objective_values[12].value == 0
    assert one.objective_values[13].value == 0


def test_participants_with_forced_zero_assignments_have_zero_workload() -> None:
    workers = (employee("A"), employee("B"))
    day = day_for((arrival("100", 9),), workers)
    config = staffing_config(1)
    model_data = optimizer_module._build_model(
        day,
        config,
        build_candidate_assignments(day, config),
    )
    for decision in model_data.decisions.values():
        model_data.model.add(decision == 0)
    solver = cp_model.CpSolver()

    assert solver.solve(model_data.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    assert all(
        solver.value(workload) == 0
        for workload in model_data.adjusted_workload_units
        if workload is not None
    )
    assert solver.value(model_data.adjusted_workload_spread) == 0
    assert solver.value(model_data.total_pairwise_adjusted_workload_difference) == 0


@pytest.mark.parametrize(
    ("role", "config_change"),
    [
        (OperationalRole.TRAINEE, "allow_trainees_for_assignments"),
        (
            OperationalRole.POSSIBLE_RAMP_SUPPORT,
            "allow_possible_ramp_support_for_assignments",
        ),
    ],
)
def test_enabled_configured_roles_receive_reported_workload(
    role: OperationalRole,
    config_change: str,
) -> None:
    worker = employee("E1")
    flight = arrival("3000", 9)
    result = optimize_flight_assignments(
        day_for(
            (flight,),
            (worker,),
            shifts=(shift("E1", role=role),),
            fixed=(FixedAssignment("E1", flight),),
        ),
        staffing_config(1, **{config_change: True}),
    )

    assert result.employee_results[0].adjusted_workload == 0.8
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.total_assignments == 1


def test_leads_remain_excluded_from_workload_population() -> None:
    result = optimize_flight_assignments(
        day_for(
            (),
            (employee("L1"),),
            shifts=(shift("L1", role=OperationalRole.RAMP_LEAD),),
        ),
        staffing_config(1, allow_leads_for_minimum_staffing=True),
    )

    assert result.employee_results == ()
    assert result.fairness_metrics is not None
    assert result.fairness_metrics.participating_employee_count == 0
    assert result.fairness_metrics.adjusted_workload_spread == 0.0


def test_ordinary_nonparticipant_reports_factual_zero_workload() -> None:
    result = optimize_flight_assignments(
        day_for(
            (arrival("100", 9),),
            (employee("AVAILABLE"), employee("OUTSIDE")),
            shifts=(
                shift("AVAILABLE", at(8), at(12)),
                shift("OUTSIDE", at(13), at(17)),
            ),
        ),
        staffing_config(1),
    )
    by_id = results_by_id(result)

    assert by_id["OUTSIDE"].flight_count == 0
    assert by_id["OUTSIDE"].adjusted_workload == 0.0
    assert by_id["OUTSIDE"].proportional_target_flight_count is None
    assert by_id["OUTSIDE"].shift_adjusted_deviation is None


def test_overnight_express_assignment_reports_workload() -> None:
    flight = arrival("3000", 0, 30, day=3)
    result = optimize_flight_assignments(
        day_for(
            (flight,),
            (employee("NIGHT"),),
            shifts=(shift("NIGHT", at(23), at(2, day=3)),),
            fixed=(FixedAssignment("NIGHT", flight),),
        ),
        staffing_config(1),
    )

    assert result.employee_results[0].adjusted_workload == 0.8
    assert result.employee_results[0].scheduled_shift_minutes == 180


def test_objective_order_appends_workload_stages_after_existing_twelve() -> None:
    result = optimize_flight_assignments(OperationalDay(date(2026, 9, 2)))

    assert [(item.stage, item.name) for item in result.objective_values] == [
        (1, "minimum_covered_flights"),
        (2, "minimum_staffed_qualification_compliant_flights"),
        (3, "minimum_staffed_individual_qualification_coverage"),
        (4, "total_minimum_shortfall"),
        (5, "largest_minimum_shortfall"),
        (6, "known_unsatisfied_required_breaks"),
        (7, "preferred_staffed_flights"),
        (8, "total_preferred_shortfall"),
        (9, "partial_crew_individual_qualification_coverage"),
        (10, "raw_flight_count_spread"),
        (11, "total_pairwise_flight_count_difference"),
        (12, "total_shift_adjusted_flight_count_deviation"),
        (13, "adjusted_workload_spread"),
        (14, "total_pairwise_adjusted_workload_difference"),
    ]
    assert all(item.proven_optimal for item in result.objective_values)


def test_time_budget_exhaustion_before_stage_13_preserves_stage_12(
    monkeypatch,
) -> None:
    day = day_for(
        (arrival("100", 9), arrival("3000", 11)),
        (employee("A"), employee("B")),
    )
    config = staffing_config(1, solver_time_limit_seconds=1.0)
    model_data = optimizer_module._build_model(
        day,
        config,
        build_candidate_assignments(day, config),
    )
    times = iter([0.0] * 12 + [2.0])
    monkeypatch.setattr(optimizer_module, "monotonic", lambda: next(times))

    status, solver, objectives = optimizer_module._solve_lexicographically(
        model_data,
        config,
        started_at=0.0,
    )

    assert status is OptimizationStatus.FEASIBLE
    assert solver is not None
    assert len(objectives) == 13
    assert all(item.proven_optimal for item in objectives[:12])
    assert objectives[12].stage == 13
    assert objectives[12].proven_optimal is False
    assert solver.value(model_data.total_shift_adjusted_deviation) == (
        objectives[11].value
    )


@pytest.mark.parametrize(
    ("completed_stages", "expected_last_stage"),
    [(12, 13), (13, 14)],
    ids=("during-stage-13", "during-stage-14"),
)
def test_workload_stage_timeout_preserves_last_proven_solution(
    monkeypatch,
    completed_stages: int,
    expected_last_stage: int,
) -> None:
    day = day_for(
        (arrival("100", 9), arrival("3000", 11)),
        (employee("A"), employee("B")),
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
        return real_solver_type() if calls <= completed_stages else UnknownSolver()

    monkeypatch.setattr(optimizer_module.cp_model, "CpSolver", solver_factory)

    result = optimize_flight_assignments(day, staffing_config(1))

    assert result.status is OptimizationStatus.FEASIBLE
    assert len(result.objective_values) == expected_last_stage
    assert all(
        item.proven_optimal
        for item in result.objective_values[:completed_stages]
    )
    assert result.objective_values[-1].stage == expected_last_stage
    assert result.objective_values[-1].proven_optimal is False
    if completed_stages == 13:
        assert result.objective_values[12].proven_optimal


def test_optimizer_rejects_unrepresentable_workload_policy() -> None:
    config = staffing_config(1, express_workload_factor=0.333)

    with pytest.raises(InputValidationError) as error:
        optimize_flight_assignments(
            day_for((arrival("100", 9),), (employee("A"),)),
            config,
        )

    assert any(
        issue.code == "UNREPRESENTABLE_EXPRESS_WORKLOAD_FACTOR"
        for issue in error.value.issues
    )
