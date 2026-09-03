"""Deterministic Milestone 5 qualification-coverage tests."""

from dataclasses import replace
from datetime import date, datetime

from ortools.sat.python import cp_model
import pytest

import ramp_optimizer.optimizer as optimizer_module
from ramp_optimizer import (
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    OperationalDay,
    OperationalRole,
    OptimizationStatus,
    OptimizerConfig,
    Qualification,
    WarningCode,
    build_candidate_assignments,
    optimize_flight_assignments,
    optimize_minimum_staffing,
)


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 2, hour, minute)


def departure(number: str = "101", hour: int = 9) -> Flight:
    return Flight(departure_flight_number=number, departure_time=at(hour))


def arrival(number: str = "201", hour: int = 9) -> Flight:
    return Flight(arrival_flight_number=number, arrival_time=at(hour))


def turn() -> Flight:
    return Flight(
        arrival_flight_number="201",
        arrival_time=at(9),
        departure_flight_number="202",
        departure_time=at(10),
    )


def employee(
    employee_id: str,
    *qualifications: Qualification,
    name: str | None = None,
) -> Employee:
    return Employee(
        employee_id,
        name or f"Employee {employee_id}",
        frozenset(qualifications),
    )


def day_for(
    target: Flight,
    employees: tuple[Employee, ...],
    *,
    fixed: tuple[FixedAssignment, ...] = (),
    shifts: tuple[EmployeeShift, ...] | None = None,
) -> OperationalDay:
    return OperationalDay(
        date(2026, 9, 2),
        employees=employees,
        employee_shifts=shifts
        if shifts is not None
        else tuple(
            EmployeeShift(
                worker.employee_id,
                at(5),
                at(13),
                OperationalRole.RAMP_AGENT,
            )
            for worker in employees
        ),
        flights=(target,),
        fixed_assignments=fixed,
    )


def codes(result) -> tuple[WarningCode, ...]:
    return tuple(warning.code for warning in result.flight_results[0].warnings)


def test_arrival_qualifications_are_not_applicable_and_never_warn() -> None:
    employees = (
        employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT),
        employee("PUSH", Qualification.PUSH),
        employee("NONE"),
    )

    result = optimize_flight_assignments(day_for(arrival(), employees))
    flight_result = result.flight_results[0]

    assert flight_result.push_covered is None
    assert flight_result.close_covered is None
    assert all(
        warning.code
        not in {
            WarningCode.PUSH_QUALIFICATION_NOT_MET,
            WarningCode.CLOSE_QUALIFICATION_NOT_MET,
        }
        for warning in (*flight_result.warnings, *result.warnings)
    )
    assert result.objective_values[1].value == 0
    assert result.objective_values[2].value == 0


@pytest.mark.parametrize("target", [departure(), turn()])
def test_separate_qualified_employees_make_required_flight_compliant(
    target: Flight,
) -> None:
    employees = (
        employee("PUSH", Qualification.PUSH),
        employee("CLOSE", Qualification.CLOSE_OUT),
        employee("NONE"),
    )

    result = optimize_flight_assignments(day_for(target, employees))
    flight_result = result.flight_results[0]

    assert flight_result.minimum_met
    assert flight_result.push_covered is True
    assert flight_result.close_covered is True
    assert WarningCode.PUSH_QUALIFICATION_NOT_MET not in codes(result)
    assert WarningCode.CLOSE_QUALIFICATION_NOT_MET not in codes(result)
    assert result.objective_values[1].value == 1
    assert result.objective_values[2].value == 2


def test_one_dual_qualified_partial_crew_covers_both_requirements() -> None:
    dual = employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT)

    result = optimize_flight_assignments(day_for(departure(), (dual,)))
    flight_result = result.flight_results[0]

    assert flight_result.assigned_employee_ids == ("DUAL",)
    assert flight_result.push_covered is True
    assert flight_result.close_covered is True
    assert codes(result) == (WarningCode.MINIMUM_STAFFING_NOT_MET,)


def test_unqualified_employee_remains_a_legal_candidate() -> None:
    worker = employee("NONE")
    day = day_for(departure(), (worker,))

    assert build_candidate_assignments(day, OptimizerConfig()) == (
        optimizer_module.CandidateAssignment("NONE", day.flights[0]),
    )
    assert optimize_flight_assignments(day).flight_results[
        0
    ].assigned_employee_ids == ("NONE",)


def test_fully_staffed_unqualified_crew_has_both_critical_shortages() -> None:
    employees = tuple(employee(f"E{index}") for index in range(1, 5))

    result = optimize_flight_assignments(day_for(departure(), employees))
    flight_result = result.flight_results[0]

    assert flight_result.minimum_met
    assert flight_result.push_covered is False
    assert flight_result.close_covered is False
    assert codes(result) == (
        WarningCode.PUSH_QUALIFICATION_NOT_MET,
        WarningCode.CLOSE_QUALIFICATION_NOT_MET,
    )
    assert all(warning.severity.value == "CRITICAL" for warning in result.warnings)
    assert result.status is OptimizationStatus.OPTIMAL


@pytest.mark.parametrize(
    ("qualifications", "expected_push", "expected_close", "missing_code"),
    [
        (
            (Qualification.PUSH,),
            True,
            False,
            WarningCode.CLOSE_QUALIFICATION_NOT_MET,
        ),
        (
            (Qualification.CLOSE_OUT,),
            False,
            True,
            WarningCode.PUSH_QUALIFICATION_NOT_MET,
        ),
    ],
)
def test_one_sided_partial_coverage_reports_only_other_qualification(
    qualifications: tuple[Qualification, ...],
    expected_push: bool,
    expected_close: bool,
    missing_code: WarningCode,
) -> None:
    workers = (employee("QUALIFIED", *qualifications), employee("NONE"))

    result = optimize_flight_assignments(day_for(departure(), workers))
    flight_result = result.flight_results[0]

    assert flight_result.push_covered is expected_push
    assert flight_result.close_covered is expected_close
    assert codes(result) == (
        WarningCode.MINIMUM_STAFFING_NOT_MET,
        missing_code,
    )


def test_empty_required_flight_has_both_coverage_warnings_with_staffing_warning(
) -> None:
    result = optimize_flight_assignments(day_for(departure(), ()))
    flight_result = result.flight_results[0]

    assert flight_result.assigned_employee_ids == ()
    assert flight_result.push_covered is False
    assert flight_result.close_covered is False
    assert codes(result) == (
        WarningCode.MINIMUM_STAFFING_NOT_MET,
        WarningCode.PUSH_QUALIFICATION_NOT_MET,
        WarningCode.CLOSE_QUALIFICATION_NOT_MET,
    )
    assert result.warnings == flight_result.warnings
    assert result.status is OptimizationStatus.OPTIMAL


def test_fixed_qualified_employees_contribute_to_crew_coverage() -> None:
    target = departure()
    workers = (
        employee("FIXED_PUSH", Qualification.PUSH),
        employee("CLOSE", Qualification.CLOSE_OUT),
        employee("NONE"),
    )
    day = day_for(
        target,
        workers,
        fixed=(FixedAssignment("FIXED_PUSH", target),),
    )

    result = optimize_flight_assignments(day)
    flight_result = result.flight_results[0]

    assert flight_result.fixed_employee_ids == ("FIXED_PUSH",)
    assert flight_result.assigned_employee_ids.count("FIXED_PUSH") == 1
    assert flight_result.push_covered is True
    assert flight_result.close_covered is True


def test_fixed_dual_qualified_employee_covers_both_requirements() -> None:
    target = departure()
    dual = employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT)

    result = optimize_flight_assignments(
        day_for(target, (dual,), fixed=(FixedAssignment("DUAL", target),))
    )

    assert result.flight_results[0].push_covered is True
    assert result.flight_results[0].close_covered is True


def test_fixed_unqualified_employee_counts_as_staff_but_not_coverage() -> None:
    target = departure()
    worker = employee("FIXED_NONE")

    result = optimize_flight_assignments(
        day_for(
            target,
            (worker,),
            fixed=(FixedAssignment("FIXED_NONE", target),),
        )
    )
    flight_result = result.flight_results[0]

    assert flight_result.staffing_count == 1
    assert flight_result.push_covered is False
    assert flight_result.close_covered is False


def test_qualification_shortage_is_recoverable_and_returns_usable_schedule() -> None:
    workers = tuple(employee(f"E{index}") for index in range(1, 4))

    result = optimize_flight_assignments(day_for(departure(), workers))

    assert result.status is OptimizationStatus.OPTIMAL
    assert result.flight_results[0].assigned_employee_ids == tuple(
        worker.employee_id for worker in workers
    )
    assert result.flight_results[0].minimum_met


def test_qualification_coverage_never_reduces_minimum_staffed_flight_count() -> None:
    flights = (departure("101"), departure("102"))
    workers = (
        employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT),
        *(employee(f"E{index}") for index in range(1, 6)),
    )
    day = OperationalDay(
        date(2026, 9, 2),
        employees=workers,
        employee_shifts=tuple(
            EmployeeShift(
                worker.employee_id,
                at(5),
                at(13),
                OperationalRole.RAMP_AGENT,
            )
            for worker in workers
        ),
        flights=flights,
    )

    result = optimize_flight_assignments(day)

    assert sum(item.minimum_met for item in result.flight_results) == 2
    assert sum(
        item.push_covered and item.close_covered for item in result.flight_results
    ) == 1
    assert result.objective_values[0].value == 2


def test_equal_minimum_schedule_prefers_qualified_crew() -> None:
    workers = (
        employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT),
        employee("NONE1"),
        employee("NONE2"),
        employee("NONE3"),
    )
    config = replace(
        OptimizerConfig(),
        normal_preferred_staff=3,
        heavy_preferred_staff=3,
    )

    result = optimize_flight_assignments(day_for(departure(), workers), config)

    assert result.flight_results[0].staffing_count == 3
    assert "DUAL" in result.flight_results[0].assigned_employee_ids
    assert result.flight_results[0].push_covered is True
    assert result.flight_results[0].close_covered is True


def test_high_priority_qualification_objectives_ignore_qualified_fragments() -> None:
    flights = (departure("101"), departure("102"))
    workers = (
        employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT),
        employee("NONE1"),
        employee("NONE2"),
        employee("NONE3"),
    )
    day = OperationalDay(
        date(2026, 9, 2),
        employees=workers,
        employee_shifts=tuple(
            EmployeeShift(
                worker.employee_id,
                at(5),
                at(13),
                OperationalRole.RAMP_AGENT,
            )
            for worker in workers
        ),
        flights=flights,
    )

    result = optimize_flight_assignments(day)
    minimum_staffed = next(item for item in result.flight_results if item.minimum_met)

    assert minimum_staffed.push_covered is True
    assert minimum_staffed.close_covered is True
    assert result.objective_values[1].value == 1
    assert result.objective_values[2].value == 2


def test_final_tie_breaker_prefers_coverage_on_below_minimum_partial_crew() -> None:
    arriving = arrival("201", 9)
    departing = departure("101", 9)
    workers = (
        employee("PUSH", Qualification.PUSH),
        employee("SHARED_NONE"),
        employee("ARRIVAL_ONLY1"),
        employee("ARRIVAL_ONLY2"),
    )
    shared_shifts = (
        EmployeeShift("PUSH", at(5), at(13), OperationalRole.RAMP_AGENT),
        EmployeeShift(
            "SHARED_NONE", at(5), at(13), OperationalRole.RAMP_AGENT
        ),
        EmployeeShift(
            "ARRIVAL_ONLY1", at(8, 45), at(9, 30), OperationalRole.RAMP_AGENT
        ),
        EmployeeShift(
            "ARRIVAL_ONLY2", at(8, 45), at(9, 30), OperationalRole.RAMP_AGENT
        ),
    )
    day = OperationalDay(
        date(2026, 9, 2),
        employees=workers,
        employee_shifts=shared_shifts,
        flights=(arriving, departing),
    )

    result = optimize_flight_assignments(day)
    arrival_result, departure_result = result.flight_results

    assert arrival_result.minimum_met
    assert not departure_result.minimum_met
    assert departure_result.assigned_employee_ids == ("PUSH",)
    assert departure_result.push_covered is True
    assert departure_result.close_covered is False
    assert result.objective_values[-1].value == 1


@pytest.mark.parametrize("assigned", [False, True])
def test_internal_coverage_indicators_exactly_match_assigned_crew(
    assigned: bool,
) -> None:
    dual = employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT)
    day = day_for(departure(), (dual,))
    config = OptimizerConfig()
    candidates = build_candidate_assignments(day, config)
    model_data = optimizer_module._build_model(day, config, candidates)
    decision = model_data.decisions[(0, 0)]
    model_data.model.add(decision == int(assigned))
    solver = cp_model.CpSolver()

    assert solver.solve(model_data.model) in {cp_model.OPTIMAL, cp_model.FEASIBLE}
    push = model_data.push_covered[0]
    close = model_data.close_covered[0]
    compliant = model_data.qualification_compliant[0]
    assert push is not None
    assert close is not None
    assert compliant is not None
    assert solver.value(push) == int(assigned)
    assert solver.value(close) == int(assigned)
    assert solver.value(compliant) == int(assigned)


def test_names_roles_and_fixed_status_never_infer_qualifications() -> None:
    target = departure()
    misleading = employee("PUSH-CLOSE", name="Push Close-Out Specialist")
    workers = (misleading, employee("NONE2"), employee("NONE3"))

    result = optimize_flight_assignments(
        day_for(
            target,
            workers,
            fixed=(FixedAssignment("PUSH-CLOSE", target),),
        )
    )

    assert result.flight_results[0].push_covered is False
    assert result.flight_results[0].close_covered is False


def test_stable_order_and_seeded_repeated_runs_are_equivalent() -> None:
    workers = (
        employee("E3", Qualification.PUSH),
        employee("E1", Qualification.CLOSE_OUT),
        employee("E2"),
        employee("E4"),
    )
    day = day_for(departure(), workers)

    first = optimize_flight_assignments(day)
    second = optimize_flight_assignments(day)

    assert first.flight_results == second.flight_results
    assert first.objective_values == second.objective_values
    assert first.flight_results[0].assigned_employee_ids == tuple(
        worker.employee_id
        for worker in workers
        if worker.employee_id in first.flight_results[0].assigned_employee_ids
    )


def test_new_entry_point_preserves_old_public_function_compatibility() -> None:
    day = day_for(
        departure(),
        (employee("DUAL", Qualification.PUSH, Qualification.CLOSE_OUT),),
    )

    new_result = optimize_flight_assignments(day)
    old_result = optimize_minimum_staffing(day)

    assert new_result.flight_results == old_result.flight_results
    assert new_result.objective_values == old_result.objective_values
    assert new_result.status is old_result.status


def test_later_unknown_preserves_last_feasible_solution(monkeypatch) -> None:
    workers = tuple(employee(f"E{index}") for index in range(3))
    day = day_for(departure(), workers)
    config = OptimizerConfig()
    model_data = optimizer_module._build_model(
        day,
        config,
        build_candidate_assignments(day, config),
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
        return real_solver_type() if calls == 1 else UnknownSolver()

    monkeypatch.setattr(optimizer_module.cp_model, "CpSolver", solver_factory)

    status, solver, objectives = optimizer_module._solve_lexicographically(
        model_data,
        config,
        optimizer_module.monotonic(),
    )

    assert status is OptimizationStatus.FEASIBLE
    assert isinstance(solver, real_solver_type)
    assert [objective.proven_optimal for objective in objectives] == [True, False]
    assert solver.value(model_data.staff_counts[0]) == 3


def test_later_time_budget_exhaustion_preserves_last_feasible_solution(
    monkeypatch,
) -> None:
    workers = tuple(employee(f"E{index}") for index in range(3))
    day = day_for(departure(), workers)
    config = replace(OptimizerConfig(), solver_time_limit_seconds=1.0)
    model_data = optimizer_module._build_model(
        day,
        config,
        build_candidate_assignments(day, config),
    )
    times = iter((0.0, 2.0))
    monkeypatch.setattr(optimizer_module, "monotonic", lambda: next(times))

    status, solver, objectives = optimizer_module._solve_lexicographically(
        model_data,
        config,
        started_at=0.0,
    )

    assert status is OptimizationStatus.FEASIBLE
    assert solver is not None
    assert [objective.proven_optimal for objective in objectives] == [True, False]
    assert solver.value(model_data.staff_counts[0]) == 3
