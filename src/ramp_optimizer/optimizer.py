"""Milestone 4 CP-SAT optimizer for minimum and preferred flight staffing."""

from dataclasses import dataclass
from itertools import combinations
from math import isfinite
from time import monotonic

from ortools.sat.python import cp_model

from ramp_optimizer.candidates import build_candidate_assignments
from ramp_optimizer.config import OptimizerConfig
from ramp_optimizer.intervals import intervals_overlap
from ramp_optimizer.models import (
    CandidateAssignment,
    FlightAssignmentResult,
    ObjectiveValue,
    OperationalDay,
    OptimizationResult,
    ScheduleWarning,
)
from ramp_optimizer.enums import (
    OptimizationStatus,
    StaffingStatus,
    WarningCode,
    WarningSeverity,
)
from ramp_optimizer.staffing import StaffingRequirements, staffing_requirements_for
from ramp_optimizer.timing import FlightOperationalFacts, derive_flight_operational_facts


@dataclass(slots=True)
class _ModelData:
    model: cp_model.CpModel
    decisions: dict[tuple[int, int], cp_model.IntVar]
    facts: tuple[FlightOperationalFacts, ...]
    requirements: tuple[StaffingRequirements, ...]
    fixed_employee_indices: tuple[tuple[int, ...], ...]
    staff_counts: tuple[cp_model.IntVar, ...]
    minimum_met: tuple[cp_model.IntVar, ...]
    minimum_shortfalls: tuple[cp_model.IntVar, ...]
    preferred_met: tuple[cp_model.IntVar, ...]
    preferred_shortfalls: tuple[cp_model.IntVar, ...]
    largest_minimum_shortfall: cp_model.IntVar


@dataclass(frozen=True, slots=True)
class _ObjectiveStage:
    name: str
    maximize: bool
    expression: cp_model.LinearExpr | int


def optimize_minimum_staffing(
    day: OperationalDay, config: OptimizerConfig | None = None
) -> OptimizationResult:
    """Optimize only minimum and preferred staffing under Milestone 4 rules."""

    active_config = config or OptimizerConfig()
    started_at = monotonic()

    # Candidate preprocessing validates every input and makes illegal assignment
    # variables unrepresentable in the CP-SAT model.
    candidates = build_candidate_assignments(
        day,
        active_config,
        include_leads=False,
    )
    model_data = _build_model(day, active_config, candidates)
    status, solver, objectives = _solve_lexicographically(
        model_data, active_config, started_at
    )
    runtime = max(0.0, monotonic() - started_at)

    if solver is None:
        return _empty_solver_result(status, objectives, runtime)
    return _build_result(day, model_data, solver, status, objectives, runtime)


def _build_model(
    day: OperationalDay,
    config: OptimizerConfig,
    candidates: tuple[CandidateAssignment, ...],
) -> _ModelData:
    model = cp_model.CpModel()
    facts = tuple(
        derive_flight_operational_facts(flight, config) for flight in day.flights
    )
    requirements = tuple(
        staffing_requirements_for(flight, config) for flight in day.flights
    )
    candidate_indices = _index_candidates(day, candidates)

    # x[e, f] exists only for a legal, non-fixed candidate. Fixed assignments
    # are constants and are never optional decisions.
    decisions = {
        pair: model.new_bool_var(f"x_e{pair[0]}_f{pair[1]}")
        for pair in candidate_indices
    }
    fixed_employee_indices = _fixed_employee_indices_by_flight(day)
    _add_candidate_overlap_constraints(model, decisions, facts)

    staff_counts: list[cp_model.IntVar] = []
    minimum_met: list[cp_model.IntVar] = []
    minimum_shortfalls: list[cp_model.IntVar] = []
    preferred_met: list[cp_model.IntVar] = []
    preferred_shortfalls: list[cp_model.IntVar] = []

    for flight_index, requirement in enumerate(requirements):
        fixed_count = len(fixed_employee_indices[flight_index])
        flight_decisions = [
            decision
            for (employee_index, candidate_flight_index), decision in decisions.items()
            if candidate_flight_index == flight_index
        ]
        staff_count = model.new_int_var(
            fixed_count,
            requirement.maximum,
            f"staff_count_f{flight_index}",
        )
        model.add(staff_count == fixed_count + sum(flight_decisions))
        model.add(staff_count <= requirement.maximum)
        staff_counts.append(staff_count)

        # minimum_met[f] is exact in both directions, and the shortfall records
        # max(0, minimum - staff_count) without making minimum a hard constraint.
        minimum_indicator = _add_exact_threshold_indicator(
            model,
            staff_count,
            requirement.minimum,
            f"minimum_met_f{flight_index}",
        )
        minimum_shortfall = model.new_int_var(
            0,
            requirement.minimum,
            f"minimum_shortfall_f{flight_index}",
        )
        model.add_max_equality(
            minimum_shortfall,
            [requirement.minimum - staff_count, 0],
        )
        minimum_met.append(minimum_indicator)
        minimum_shortfalls.append(minimum_shortfall)

        # preferred_met[f] and preferred_shortfall[f] describe completion of
        # the desired crew. Preferred currently equals the hard maximum.
        preferred_indicator = _add_exact_threshold_indicator(
            model,
            staff_count,
            requirement.preferred,
            f"preferred_met_f{flight_index}",
        )
        preferred_shortfall = model.new_int_var(
            0,
            requirement.preferred,
            f"preferred_shortfall_f{flight_index}",
        )
        model.add(preferred_shortfall == requirement.preferred - staff_count)
        preferred_met.append(preferred_indicator)
        preferred_shortfalls.append(preferred_shortfall)

    largest_shortfall_bound = max(
        (requirement.minimum for requirement in requirements), default=0
    )
    largest_minimum_shortfall = model.new_int_var(
        0,
        largest_shortfall_bound,
        "largest_minimum_shortfall",
    )
    if minimum_shortfalls:
        model.add_max_equality(largest_minimum_shortfall, minimum_shortfalls)
    else:
        model.add(largest_minimum_shortfall == 0)

    return _ModelData(
        model=model,
        decisions=decisions,
        facts=facts,
        requirements=requirements,
        fixed_employee_indices=fixed_employee_indices,
        staff_counts=tuple(staff_counts),
        minimum_met=tuple(minimum_met),
        minimum_shortfalls=tuple(minimum_shortfalls),
        preferred_met=tuple(preferred_met),
        preferred_shortfalls=tuple(preferred_shortfalls),
        largest_minimum_shortfall=largest_minimum_shortfall,
    )


def _index_candidates(
    day: OperationalDay, candidates: tuple[CandidateAssignment, ...]
) -> tuple[tuple[int, int], ...]:
    employee_indices = {
        employee.employee_id.strip().casefold(): index
        for index, employee in enumerate(day.employees)
    }
    pairs: list[tuple[int, int]] = []
    for candidate in candidates:
        employee_index = employee_indices[candidate.employee_id.strip().casefold()]
        flight_index = day.flights.index(candidate.flight)
        pairs.append((employee_index, flight_index))
    return tuple(pairs)


def _fixed_employee_indices_by_flight(
    day: OperationalDay,
) -> tuple[tuple[int, ...], ...]:
    employee_indices = {
        employee.employee_id.strip().casefold(): index
        for index, employee in enumerate(day.employees)
    }
    fixed_by_flight: list[list[int]] = [[] for _ in day.flights]
    for fixed in day.fixed_assignments:
        employee_index = employee_indices[fixed.employee_id.strip().casefold()]
        flight_index = day.flights.index(fixed.flight)
        fixed_by_flight[flight_index].append(employee_index)
    return tuple(tuple(sorted(indices)) for indices in fixed_by_flight)


def _add_candidate_overlap_constraints(
    model: cp_model.CpModel,
    decisions: dict[tuple[int, int], cp_model.IntVar],
    facts: tuple[FlightOperationalFacts, ...],
) -> None:
    flights_by_employee: dict[int, list[int]] = {}
    for employee_index, flight_index in decisions:
        flights_by_employee.setdefault(employee_index, []).append(flight_index)

    for employee_index, flight_indices in flights_by_employee.items():
        for first_flight, second_flight in combinations(flight_indices, 2):
            first = facts[first_flight]
            second = facts[second_flight]
            if intervals_overlap(
                first.work_start,
                first.work_end,
                second.work_start,
                second.work_end,
            ):
                model.add(
                    decisions[(employee_index, first_flight)]
                    + decisions[(employee_index, second_flight)]
                    <= 1
                )


def _add_exact_threshold_indicator(
    model: cp_model.CpModel,
    staff_count: cp_model.IntVar,
    threshold: int,
    name: str,
) -> cp_model.IntVar:
    indicator = model.new_bool_var(name)
    model.add(staff_count >= threshold).only_enforce_if(indicator)
    model.add(staff_count <= threshold - 1).only_enforce_if(indicator.Not())
    return indicator


def _objective_stages(model_data: _ModelData) -> tuple[_ObjectiveStage, ...]:
    return (
        _ObjectiveStage(
            "minimum_covered_flights", True, sum(model_data.minimum_met)
        ),
        _ObjectiveStage(
            "total_minimum_shortfall",
            False,
            sum(model_data.minimum_shortfalls),
        ),
        _ObjectiveStage(
            "largest_minimum_shortfall",
            False,
            model_data.largest_minimum_shortfall,
        ),
        _ObjectiveStage(
            "preferred_staffed_flights", True, sum(model_data.preferred_met)
        ),
        _ObjectiveStage(
            "total_preferred_shortfall",
            False,
            sum(model_data.preferred_shortfalls),
        ),
    )


def _solve_lexicographically(
    model_data: _ModelData,
    config: OptimizerConfig,
    started_at: float,
) -> tuple[
    OptimizationStatus,
    cp_model.CpSolver | None,
    tuple[ObjectiveValue, ...],
]:
    recorded: list[ObjectiveValue] = []
    last_solver: cp_model.CpSolver | None = None

    for stage_number, stage in enumerate(_objective_stages(model_data), start=1):
        remaining_seconds = config.solver_time_limit_seconds - (
            monotonic() - started_at
        )
        if remaining_seconds <= 0:
            if last_solver is None:
                return OptimizationStatus.UNKNOWN, None, tuple(recorded)
            recorded.append(
                ObjectiveValue(
                    stage=stage_number,
                    name=stage.name,
                    value=_expression_value(last_solver, stage.expression),
                    proven_optimal=False,
                )
            )
            return OptimizationStatus.FEASIBLE, last_solver, tuple(recorded)

        if stage.maximize:
            model_data.model.maximize(stage.expression)
        else:
            model_data.model.minimize(stage.expression)

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = remaining_seconds
        solver.parameters.random_seed = config.solver_random_seed
        solver.parameters.num_search_workers = config.solver_num_search_workers
        ortools_status = solver.solve(model_data.model)
        status = _map_status(ortools_status)

        if status is OptimizationStatus.OPTIMAL:
            optimum = _expression_value(solver, stage.expression)
            recorded.append(
                ObjectiveValue(stage_number, stage.name, optimum, True)
            )
            model_data.model.add(stage.expression == optimum)
            last_solver = solver
            continue
        if status is OptimizationStatus.FEASIBLE:
            recorded.append(
                ObjectiveValue(
                    stage_number,
                    stage.name,
                    _expression_value(solver, stage.expression),
                    False,
                )
            )
            return status, solver, tuple(recorded)
        return status, None, tuple(recorded)

    return OptimizationStatus.OPTIMAL, last_solver, tuple(recorded)


def _expression_value(
    solver: cp_model.CpSolver, expression: cp_model.LinearExpr | int
) -> int:
    if isinstance(expression, int):
        return expression
    return int(solver.value(expression))


def _map_status(status: cp_model.CpSolverStatus) -> OptimizationStatus:
    if status == cp_model.OPTIMAL:
        return OptimizationStatus.OPTIMAL
    if status == cp_model.FEASIBLE:
        return OptimizationStatus.FEASIBLE
    if status == cp_model.INFEASIBLE:
        return OptimizationStatus.INFEASIBLE
    return OptimizationStatus.UNKNOWN


def _build_result(
    day: OperationalDay,
    model_data: _ModelData,
    solver: cp_model.CpSolver,
    status: OptimizationStatus,
    objectives: tuple[ObjectiveValue, ...],
    runtime: float,
) -> OptimizationResult:
    flight_results: list[FlightAssignmentResult] = []
    all_warnings: list[ScheduleWarning] = []

    for flight_index, flight in enumerate(day.flights):
        assigned_indices = set(model_data.fixed_employee_indices[flight_index])
        for (employee_index, candidate_flight_index), decision in (
            model_data.decisions.items()
        ):
            if candidate_flight_index == flight_index and solver.value(decision):
                assigned_indices.add(employee_index)

        assigned_employee_ids = tuple(
            employee.employee_id
            for employee_index, employee in enumerate(day.employees)
            if employee_index in assigned_indices
        )
        fixed_indices = set(model_data.fixed_employee_indices[flight_index])
        fixed_employee_ids = tuple(
            employee.employee_id
            for employee_index, employee in enumerate(day.employees)
            if employee_index in fixed_indices
        )
        requirements = model_data.requirements[flight_index]
        facts = model_data.facts[flight_index]
        staffing_count = len(assigned_employee_ids)
        minimum_met = staffing_count >= requirements.minimum
        preferred_met = staffing_count >= requirements.preferred
        minimum_shortfall = max(0, requirements.minimum - staffing_count)
        preferred_shortfall = max(0, requirements.preferred - staffing_count)
        staffing_status = (
            StaffingStatus.PREFERRED_STAFFED
            if preferred_met
            else StaffingStatus.MINIMUM_STAFFED
            if minimum_met
            else StaffingStatus.BELOW_MINIMUM
        )

        flight_warnings: tuple[ScheduleWarning, ...] = ()
        if not minimum_met:
            warning = ScheduleWarning(
                code=WarningCode.MINIMUM_STAFFING_NOT_MET,
                severity=WarningSeverity.CRITICAL,
                message=(
                    f"Flight is below minimum staffing by {minimum_shortfall}"
                ),
                arrival_flight_number=flight.arrival_flight_number,
                departure_flight_number=flight.departure_flight_number,
            )
            flight_warnings = (warning,)
            all_warnings.append(warning)

        flight_results.append(
            FlightAssignmentResult(
                flight=flight,
                flight_type=facts.flight_type,
                work_start=facts.work_start,
                work_end=facts.work_end,
                assigned_employee_ids=assigned_employee_ids,
                fixed_employee_ids=fixed_employee_ids,
                staffing_count=staffing_count,
                minimum_staff=requirements.minimum,
                preferred_staff=requirements.preferred,
                maximum_staff=requirements.maximum,
                staffing_status=staffing_status,
                minimum_met=minimum_met,
                minimum_shortfall=minimum_shortfall,
                preferred_met=preferred_met,
                preferred_shortfall=preferred_shortfall,
                express=facts.express,
                heavy=flight.heavy,
                push_covered=None,
                close_covered=None,
                warnings=flight_warnings,
            )
        )

    assert isfinite(runtime)
    return OptimizationResult(
        status=status,
        flight_results=tuple(flight_results),
        employee_results=(),
        fairness_metrics=None,
        attempts=(),
        objective_values=objectives,
        warnings=tuple(all_warnings),
        emergency_lead_staffing_used=None,
        solver_runtime_seconds=runtime,
    )


def _empty_solver_result(
    status: OptimizationStatus,
    objectives: tuple[ObjectiveValue, ...],
    runtime: float,
) -> OptimizationResult:
    return OptimizationResult(
        status=status,
        flight_results=(),
        employee_results=(),
        fairness_metrics=None,
        attempts=(),
        objective_values=objectives,
        warnings=(),
        emergency_lead_staffing_used=None,
        solver_runtime_seconds=runtime,
    )
