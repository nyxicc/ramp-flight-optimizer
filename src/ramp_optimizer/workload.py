"""Deterministic fixed-point adjusted-workload calculations."""

from decimal import Decimal, InvalidOperation
from math import isfinite

from ramp_optimizer.config import OptimizerConfig
from ramp_optimizer.timing import FlightOperationalFacts


MAX_CP_SAT_INTEGER = (1 << 63) - 1


class WorkloadConfigurationError(ValueError):
    """Raised when workload factors cannot be represented by the configured scale."""


def scaled_workload_factors(config: OptimizerConfig) -> tuple[int, int]:
    """Return exact Express and three-person factors at ``workload_scale``."""

    scale = config.workload_scale
    if not isinstance(scale, int) or isinstance(scale, bool) or scale <= 0:
        raise WorkloadConfigurationError("workload_scale must be a positive integer")

    express = _factor_decimal(
        config.express_workload_factor,
        name="express_workload_factor",
    )
    if express <= 0 or express > 1:
        raise WorkloadConfigurationError(
            "express_workload_factor must be greater than 0 and no more than 1"
        )
    multiplier = _factor_decimal(
        config.three_person_workload_multiplier,
        name="three_person_workload_multiplier",
    )
    if multiplier < 1:
        raise WorkloadConfigurationError(
            "three_person_workload_multiplier must be at least 1"
        )

    express_units = _exact_scaled_factor(
        express,
        scale,
        name="express_workload_factor",
    )
    multiplier_units = _exact_scaled_factor(
        multiplier,
        scale,
        name="three_person_workload_multiplier",
    )
    maximum_assignment_units = max(
        scale * scale,
        express_units * scale,
        scale * multiplier_units,
        express_units * multiplier_units,
    )
    if maximum_assignment_units > MAX_CP_SAT_INTEGER:
        raise WorkloadConfigurationError(
            "scaled workload values exceed the supported CP-SAT integer range"
        )
    return express_units, multiplier_units


def workload_unit_scale(config: OptimizerConfig) -> int:
    """Return the public-value denominator for fixed-point workload units."""

    scaled_workload_factors(config)
    return config.workload_scale * config.workload_scale


def adjusted_assignment_workload_units(
    facts: FlightOperationalFacts,
    staffing_count: int,
    config: OptimizerConfig,
) -> int:
    """Return exact workload units for one assigned employee on one flight."""

    if (
        not isinstance(staffing_count, int)
        or isinstance(staffing_count, bool)
        or staffing_count <= 0
    ):
        raise ValueError("staffing_count must be a positive integer")

    express_units, multiplier_units = scaled_workload_factors(config)
    category_units = express_units if facts.express else config.workload_scale
    staffing_units = (
        multiplier_units if staffing_count == 3 else config.workload_scale
    )
    return category_units * staffing_units


def workload_units_to_public_value(
    units: int,
    config: OptimizerConfig,
) -> float:
    """Convert non-negative exact workload units at the reporting boundary."""

    if not isinstance(units, int) or isinstance(units, bool) or units < 0:
        raise ValueError("workload units must be a non-negative integer")
    denominator = workload_unit_scale(config)
    return float(Decimal(units) / Decimal(denominator))


def _factor_decimal(value: object, *, name: str) -> Decimal:
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not isfinite(value)
    ):
        raise WorkloadConfigurationError(f"{name} must be a finite number")
    try:
        return Decimal(str(value))
    except InvalidOperation as error:
        raise WorkloadConfigurationError(f"{name} must be a finite number") from error


def _exact_scaled_factor(value: Decimal, scale: int, *, name: str) -> int:
    scaled = value * scale
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise WorkloadConfigurationError(
            f"{name} is not exactly representable at workload_scale {scale}"
        )
    return int(integral)
