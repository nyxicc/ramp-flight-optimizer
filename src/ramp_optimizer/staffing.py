"""Solver-independent staffing requirements derived from flight inputs."""

from dataclasses import dataclass

from ramp_optimizer.config import OptimizerConfig
from ramp_optimizer.models import Flight


@dataclass(frozen=True, slots=True)
class StaffingRequirements:
    """Minimum, preferred, and maximum staffing for one flight."""

    minimum: int
    preferred: int
    maximum: int


def staffing_requirements_for(
    flight: Flight, config: OptimizerConfig
) -> StaffingRequirements:
    """Derive staffing limits without storing them on the input flight.

    Phase 1 intentionally makes preferred staffing the automatic maximum: four
    for an ordinary flight and five only when ``flight.heavy`` is true.
    """

    preferred_and_maximum = (
        config.heavy_preferred_staff
        if flight.heavy
        else config.normal_preferred_staff
    )
    return StaffingRequirements(
        minimum=config.minimum_staff,
        preferred=preferred_and_maximum,
        maximum=preferred_and_maximum,
    )
