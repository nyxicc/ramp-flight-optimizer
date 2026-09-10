"""Flight review provenance around the existing domain Flight, never a second flight model."""

from dataclasses import dataclass
from datetime import time
from enum import StrEnum

from ramp_optimizer.models import Flight


class FlightStatus(StrEnum):
    NORMAL = "NORMAL"
    TERMINATING = "TERMINATING"
    AOG = "AOG"
    CANCELLED = "CANCELLED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class FlightTimePolicy:
    airport_timezone: str
    operational_day_start: time
    planning_basis: str


@dataclass(frozen=True, slots=True)
class FlightReviewRow:
    row_id: str
    source_row: int
    flight: Flight
    arrival_expected: bool
    departure_expected: bool
    origin: str | None = None
    destination: str | None = None
    status: FlightStatus = FlightStatus.NORMAL
    excluded: bool = False
    heavy_reviewed: bool = False
    notes_present: bool = False
    onward_time_present: bool = False
    late_arrival_section: bool = False
    unresolved_fields: tuple[str, ...] = ()
    formula_fields: tuple[str, ...] = ()
    normalized_fields: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FlightCorrection:
    row_id: str
    changes: tuple[tuple[str, str | bool | None], ...]
    original_values: tuple[tuple[str, str | bool | None], ...] = ()
