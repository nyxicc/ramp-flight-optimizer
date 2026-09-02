"""Tests for solver-independent flight staffing requirements."""

from dataclasses import fields, replace
from datetime import datetime

from ramp_optimizer import (
    Flight,
    OptimizerConfig,
    staffing_requirements_for,
    validate_config,
)


def test_default_staffing_configuration_is_valid() -> None:
    config = OptimizerConfig()

    assert config.minimum_staff == 3
    assert config.normal_preferred_staff == 4
    assert config.heavy_preferred_staff == 5
    assert validate_config(config) == ()


def test_normal_flight_preferred_and_maximum_are_four() -> None:
    flight = Flight(
        arrival_flight_number="UA123",
        arrival_time=datetime(2026, 9, 2, 8),
    )
    requirements = staffing_requirements_for(flight, OptimizerConfig())

    assert requirements.minimum == 3
    assert requirements.preferred == 4
    assert requirements.maximum == 4


def test_heavy_flight_preferred_and_maximum_are_five() -> None:
    flight = Flight(
        departure_flight_number="UA456",
        departure_time=datetime(2026, 9, 2, 9),
        heavy=True,
    )
    requirements = staffing_requirements_for(
        flight, OptimizerConfig()
    )

    assert requirements.minimum == 3
    assert requirements.preferred == 5
    assert requirements.maximum == 5


def test_invalid_staffing_relationships_are_rejected() -> None:
    normal_below_minimum = replace(OptimizerConfig(), normal_preferred_staff=2)
    heavy_below_normal = replace(OptimizerConfig(), heavy_preferred_staff=3)

    normal_codes = {issue.code for issue in validate_config(normal_below_minimum)}
    heavy_codes = {issue.code for issue in validate_config(heavy_below_normal)}

    assert "INVALID_NORMAL_STAFFING_LEVELS" in normal_codes
    assert "INVALID_STAFFING_RELATIONSHIP" in heavy_codes


def test_ordinary_flight_cannot_resolve_to_heavy_maximum() -> None:
    config = replace(OptimizerConfig(), heavy_preferred_staff=9)
    flight = Flight(
        arrival_flight_number="UA789",
        arrival_time=datetime(2026, 9, 2, 10),
    )
    ordinary = staffing_requirements_for(flight, config)

    assert ordinary.maximum == config.normal_preferred_staff == 4
    assert ordinary.maximum != config.heavy_preferred_staff
    assert "maximum_staff" not in {field.name for field in fields(config)}
