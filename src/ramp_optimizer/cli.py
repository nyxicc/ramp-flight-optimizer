"""Thin command-line adapter for fictional demos and reproducible benchmarks."""

import argparse
from math import isfinite
from pathlib import Path
import sys
from typing import Sequence

from ramp_optimizer.benchmarking import (
    BENCHMARK_SCENARIO_NAMES,
    benchmark_results_json,
    run_benchmarks,
    write_benchmark_results,
)
from ramp_optimizer.enums import OperationalReadinessStatus, OptimizationStatus
from ramp_optimizer.optimizer import optimize_flight_assignments
from ramp_optimizer.reporting import format_optimization_report
from ramp_optimizer.sample_data import SAMPLE_SCENARIO_NAMES, build_sample_scenario
from ramp_optimizer.validation import InputValidationError, validate_or_raise


def build_parser() -> argparse.ArgumentParser:
    """Build the public parser without reading process-global arguments."""

    parser = argparse.ArgumentParser(
        prog="ramp-optimizer",
        description="Run the fictional Ramp Team Flight Optimizer demos and benchmarks.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser(
        "demo",
        help="run a deterministic fictional scheduling scenario",
    )
    demo.add_argument(
        "--scenario",
        choices=SAMPLE_SCENARIO_NAMES,
        default="normal",
        help="fictional scenario to run (default: normal)",
    )
    demo.add_argument(
        "--time-limit",
        type=_positive_finite_float,
        default=None,
        metavar="SECONDS",
        help="override the total solver time budget with a positive finite value",
    )
    demo.set_defaults(handler=_run_demo)

    benchmark = subparsers.add_parser(
        "benchmark",
        help="measure deterministic fictional optimizer scenarios",
    )
    benchmark.add_argument(
        "--scenario",
        choices=BENCHMARK_SCENARIO_NAMES,
        default=None,
        help="run one size only (default: small, medium, and full-day)",
    )
    benchmark.add_argument(
        "--repeat",
        type=_positive_integer,
        default=3,
        help="number of real optimizer runs per scenario (default: 3)",
    )
    benchmark.add_argument(
        "--output",
        type=Path,
        default=None,
        metavar="PATH",
        help="write versioned JSON to this explicit path; otherwise print it",
    )
    benchmark.set_defaults(handler=_run_benchmark)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run one CLI command and return its documented process exit code."""

    parser = build_parser()
    namespace = parser.parse_args(arguments)
    try:
        return int(namespace.handler(namespace))
    except InputValidationError as error:
        print(f"Invalid optimizer input: {error}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError) as error:
        print(f"Command failed: {error}", file=sys.stderr)
        return 1


def _run_demo(namespace: argparse.Namespace) -> int:
    scenario = build_sample_scenario(
        namespace.scenario,
        solver_time_limit_seconds=namespace.time_limit,
    )
    validate_or_raise(scenario.day, scenario.config)
    result = optimize_flight_assignments(scenario.day, scenario.config)
    print(format_optimization_report(result))
    if result.status not in {OptimizationStatus.OPTIMAL, OptimizationStatus.FEASIBLE}:
        return 1
    if result.operational_readiness is OperationalReadinessStatus.NO_USABLE_SCHEDULE:
        return 1
    return 0


def _run_benchmark(namespace: argparse.Namespace) -> int:
    selected = None if namespace.scenario is None else (namespace.scenario,)
    results = run_benchmarks(
        repeat_count=namespace.repeat,
        scenario_names=selected,
    )
    if namespace.output is None:
        print(benchmark_results_json(results), end="")
    else:
        write_benchmark_results(results, namespace.output)
        print(f"Benchmark results written to {namespace.output}")
    return 0


def _positive_finite_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be a number") from error
    if not isfinite(parsed) or parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive finite number")
    return parsed


def _positive_integer(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("must be an integer") from error
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed
