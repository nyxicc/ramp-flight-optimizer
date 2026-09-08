"""Dependency-free reproducible benchmark harness for fictional scenarios."""

from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
from statistics import median
from time import perf_counter
from typing import Iterable

from ramp_optimizer.candidates import build_candidate_assignments
from ramp_optimizer.models import OperationalDay
from ramp_optimizer.optimizer import optimize_flight_assignments
from ramp_optimizer.sample_data import SampleScenario, build_normal_scenario
from ramp_optimizer.validation import validate_or_raise


BENCHMARK_SCHEMA_VERSION = 1
BENCHMARK_SCENARIO_NAMES = ("small", "medium", "full-day")
BENCHMARK_SOLVER_TIME_LIMIT_SECONDS = 5.0


def build_benchmark_scenarios() -> tuple[SampleScenario, ...]:
    """Return deterministic small, medium, and full-day benchmark inputs."""

    normal = build_normal_scenario(
        solver_time_limit_seconds=BENCHMARK_SOLVER_TIME_LIMIT_SECONDS
    )
    return (
        _subset_scenario(
            normal,
            name="small",
            description="First three movements and their fictional morning team.",
            flight_count=3,
            employee_ids={
                "A001",
                "A002",
                "A003",
                "A004",
                "A005",
                "A006",
                "L001",
                "T001",
                "N001",
            },
        ),
        _subset_scenario(
            normal,
            name="medium",
            description="First twelve movements and the fictional daytime team.",
            flight_count=12,
            employee_ids={
                *(f"A{index:03}" for index in range(1, 8)),
                "A013",
                "L001",
                "T001",
                "N001",
            },
        ),
        replace(normal, name="full-day"),
    )


def run_benchmarks(
    *,
    repeat_count: int = 3,
    scenario_names: Iterable[str] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, object]:
    """Measure real optimizer runs and return a stable versioned result object."""

    if (
        not isinstance(repeat_count, int)
        or isinstance(repeat_count, bool)
        or repeat_count <= 0
    ):
        raise ValueError("repeat_count must be a positive integer")

    requested = (
        BENCHMARK_SCENARIO_NAMES
        if scenario_names is None
        else tuple(scenario_names)
    )
    unknown = tuple(name for name in requested if name not in BENCHMARK_SCENARIO_NAMES)
    if unknown:
        raise ValueError(f"unknown benchmark scenario: {unknown[0]}")
    requested_set = set(requested)
    selected = tuple(
        scenario
        for scenario in build_benchmark_scenarios()
        if scenario.name in requested_set
    )

    scenario_results = tuple(
        _run_scenario(scenario, repeat_count) for scenario in selected
    )
    return {
        "schema_version": BENCHMARK_SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or _utc_timestamp(),
        "environment": _environment_metadata(),
        "settings": {
            "repeat_count": repeat_count,
            "deterministic_solver_workers": 1,
        },
        "scenarios": list(scenario_results),
    }


def benchmark_results_json(results: dict[str, object]) -> str:
    """Serialize benchmark results deterministically for display or storage."""

    return json.dumps(results, indent=2, sort_keys=True, allow_nan=False) + "\n"


def write_benchmark_results(
    results: dict[str, object],
    output_path: str | Path,
) -> Path:
    """Write results only to the caller's explicit destination."""

    path = Path(output_path)
    path.write_text(benchmark_results_json(results), encoding="utf-8")
    return path


def _run_scenario(
    scenario: SampleScenario,
    repeat_count: int,
) -> dict[str, object]:
    validate_or_raise(scenario.day, scenario.config)
    candidates = build_candidate_assignments(scenario.day, scenario.config)
    runs: list[dict[str, object]] = []
    for repetition in range(1, repeat_count + 1):
        started = perf_counter()
        result = optimize_flight_assignments(scenario.day, scenario.config)
        wall_seconds = max(0.0, perf_counter() - started)
        all_optimal = bool(result.objective_values) and all(
            item.proven_optimal for item in result.objective_values
        )
        runs.append(
            {
                "repetition": repetition,
                "status": result.status.value,
                "objective_stage_count": len(result.objective_values),
                "all_objectives_proven_optimal": all_optimal,
                "objective_optimality_state": (
                    "ALL_PROVEN_OPTIMAL" if all_optimal else "NOT_ALL_PROVEN_OPTIMAL"
                ),
                "optimization_attempt_count": len(result.attempts),
                "operational_readiness": result.operational_readiness.value,
                "solver_runtime_seconds": result.solver_runtime_seconds,
                "wall_clock_seconds": wall_seconds,
            }
        )

    wall_values = [float(run["wall_clock_seconds"]) for run in runs]
    solver_values = [float(run["solver_runtime_seconds"]) for run in runs]
    return {
        "name": scenario.name,
        "description": scenario.description,
        "employee_count": len(scenario.day.employees),
        "flight_count": len(scenario.day.flights),
        "candidate_assignment_count": len(candidates),
        "fixed_assignment_count": len(scenario.day.fixed_assignments),
        "solver_time_limit_seconds": scenario.config.solver_time_limit_seconds,
        "repeat_count": repeat_count,
        "runs": runs,
        "median_solver_runtime_seconds": median(solver_values),
        "median_wall_clock_seconds": median(wall_values),
    }


def _subset_scenario(
    source: SampleScenario,
    *,
    name: str,
    description: str,
    flight_count: int,
    employee_ids: set[str],
) -> SampleScenario:
    flights = source.day.flights[:flight_count]
    flight_set = set(flights)
    employees = tuple(
        employee
        for employee in source.day.employees
        if employee.employee_id in employee_ids
    )
    shifts = tuple(
        shift
        for shift in source.day.employee_shifts
        if shift.employee_id in employee_ids
    )
    fixed = tuple(
        assignment
        for assignment in source.day.fixed_assignments
        if assignment.flight in flight_set and assignment.employee_id in employee_ids
    )
    return SampleScenario(
        name=name,
        description=description,
        day=OperationalDay(
            source.day.operational_date,
            employees=employees,
            employee_shifts=shifts,
            flights=flights,
            fixed_assignments=fixed,
        ),
        config=source.config,
    )


def _environment_metadata() -> dict[str, str | None]:
    return {
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "processor": platform.processor() or None,
    }


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
