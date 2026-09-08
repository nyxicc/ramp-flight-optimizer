"""Shared immutable full-day fixtures."""

import pytest

from ramp_optimizer import OptimizationResult, optimize_flight_assignments
from tests.scenario_builders import SyntheticScenario, canonical_full_day


@pytest.fixture(scope="session")
def canonical_scenario() -> SyntheticScenario:
    return canonical_full_day()


@pytest.fixture(scope="session")
def canonical_result(canonical_scenario: SyntheticScenario) -> OptimizationResult:
    return optimize_flight_assignments(
        canonical_scenario.day, canonical_scenario.config
    )


@pytest.fixture(scope="session")
def recoverable_emergency_scenario() -> SyntheticScenario:
    return canonical_full_day(emergency="recoverable")


@pytest.fixture(scope="session")
def recoverable_emergency_result(
    recoverable_emergency_scenario: SyntheticScenario,
) -> OptimizationResult:
    return optimize_flight_assignments(
        recoverable_emergency_scenario.day,
        recoverable_emergency_scenario.config,
    )


@pytest.fixture(scope="session")
def insufficient_emergency_scenario() -> SyntheticScenario:
    return canonical_full_day(emergency="insufficient")


@pytest.fixture(scope="session")
def insufficient_emergency_result(
    insufficient_emergency_scenario: SyntheticScenario,
) -> OptimizationResult:
    return optimize_flight_assignments(
        insufficient_emergency_scenario.day,
        insufficient_emergency_scenario.config,
    )
