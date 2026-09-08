"""Controlled and real-budget graceful-degradation verification."""

from dataclasses import replace

from ortools.sat.python import cp_model

import ramp_optimizer.optimizer as optimizer_module
from ramp_optimizer import (
    OperationalReadinessStatus,
    OptimizationStatus,
    WarningCode,
    format_optimization_report,
    optimize_flight_assignments,
)
from tests.invariant_checks import assert_result_invariants
from tests.scenario_builders import small_ready_scenario


def test_later_stage_unknown_retains_last_proven_usable_solution(monkeypatch) -> None:
    scenario = small_ready_scenario()
    real_solver = cp_model.CpSolver
    completed_stages = 10
    calls = 0

    class UnknownSolver:
        def __init__(self) -> None:
            self.parameters = type("Parameters", (), {})()

        def solve(self, model):
            return cp_model.UNKNOWN

    def solver_factory():
        nonlocal calls
        calls += 1
        return real_solver() if calls <= completed_stages else UnknownSolver()

    monkeypatch.setattr(optimizer_module.cp_model, "CpSolver", solver_factory)
    result = optimize_flight_assignments(scenario.day, scenario.config)

    assert result.status is OptimizationStatus.FEASIBLE
    assert len(result.objective_values) == completed_stages + 1
    assert all(
        objective.proven_optimal
        for objective in result.objective_values[:completed_stages]
    )
    assert not result.objective_values[-1].proven_optimal
    assert_result_invariants(scenario.day, scenario.config, result)
    assert WarningCode.SOLVER_RESULT_NOT_PROVEN_OPTIMAL in {
        warning.code for warning in result.warnings
    }
    report = format_optimization_report(result)
    assert "Solver: FEASIBLE" in report
    assert "all objectives proven optimal: no" in report


def test_extremely_small_real_budget_returns_explicit_no_usable_schedule() -> None:
    scenario = small_ready_scenario()
    tiny_config = replace(scenario.config, solver_time_limit_seconds=1e-9)

    result = optimize_flight_assignments(scenario.day, tiny_config)

    assert result.status is OptimizationStatus.UNKNOWN
    assert result.flight_results == ()
    assert result.operational_readiness is (
        OperationalReadinessStatus.NO_USABLE_SCHEDULE
    )
    assert tuple(warning.code for warning in result.warnings) == (
        WarningCode.NO_USABLE_SCHEDULE,
    )
    assert result.attempts[0].usable_schedule is False
    assert result.attempts[0].selected_as_final is True


def test_timeout_warning_is_not_a_false_operational_shortage(monkeypatch) -> None:
    scenario = small_ready_scenario()
    real_solver = cp_model.CpSolver
    calls = 0

    class UnknownSolver:
        def __init__(self) -> None:
            self.parameters = type("Parameters", (), {})()

        def solve(self, model):
            return cp_model.UNKNOWN

    def solver_factory():
        nonlocal calls
        calls += 1
        return real_solver() if calls == 1 else UnknownSolver()

    monkeypatch.setattr(optimizer_module.cp_model, "CpSolver", solver_factory)
    result = optimize_flight_assignments(scenario.day, scenario.config)

    assert result.operational_readiness is (
        OperationalReadinessStatus.READY_WITH_WARNINGS
    )
    assert result.schedule_summary is not None
    assert result.schedule_summary.below_minimum_flights == 0
    assert result.schedule_summary.missing_push_flights == 0
    assert result.schedule_summary.missing_close_out_flights == 0
    assert tuple(warning.code for warning in result.warnings) == (
        WarningCode.SOLVER_RESULT_NOT_PROVEN_OPTIMAL,
    )
