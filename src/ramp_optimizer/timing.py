"""Deterministic flight-number, type, timing, and service-category derivation."""

from dataclasses import dataclass
from datetime import datetime, timedelta
import re

from ramp_optimizer.config import OptimizerConfig
from ramp_optimizer.enums import FlightType
from ramp_optimizer.models import Flight

_FLIGHT_NUMBER_PATTERN = re.compile(r"[A-Za-z]*(\d+)\Z")


class FlightDerivationError(ValueError):
    """Raised when operational facts cannot be derived from a flight input."""


class FlightNumberParseError(FlightDerivationError):
    """Raised when a flight number has no valid terminal numeric portion."""


@dataclass(frozen=True, slots=True)
class FlightOperationalFacts:
    """Solver-independent facts derived from one validated aircraft movement."""

    flight_type: FlightType
    work_start: datetime
    work_end: datetime
    arrival_numeric_flight_number: int | None
    departure_numeric_flight_number: int | None
    express: bool


def parse_numeric_flight_number(flight_number: str) -> int:
    """Return the terminal numeric portion of a supported flight number."""

    if not isinstance(flight_number, str):
        raise FlightNumberParseError("flight number must be a string")
    normalized = flight_number.strip()
    match = _FLIGHT_NUMBER_PATTERN.fullmatch(normalized)
    if match is None:
        raise FlightNumberParseError(
            "flight number must contain optional letters followed by terminal digits"
        )
    return int(match.group(1))


def classify_flight_type(flight: Flight) -> FlightType:
    """Classify a movement from its consistently populated directional sides."""

    arrival = _classify_side(
        flight.arrival_flight_number, flight.arrival_time, "arrival"
    )
    departure = _classify_side(
        flight.departure_flight_number, flight.departure_time, "departure"
    )
    if arrival and departure:
        return FlightType.TURN
    if arrival:
        return FlightType.ARRIVAL_ONLY
    if departure:
        return FlightType.DEPARTURE_ONLY
    raise FlightDerivationError("flight must contain an arrival or departure side")


def derive_work_window(
    flight: Flight, config: OptimizerConfig
) -> tuple[datetime, datetime]:
    """Derive the half-open work window ``[work_start, work_end)``."""

    flight_type = classify_flight_type(flight)
    _require_valid_timing_config(config)

    if flight_type is FlightType.ARRIVAL_ONLY:
        arrival = _require_datetime(flight.arrival_time, "arrival_time")
        work_start = arrival - timedelta(minutes=config.arrival_preparation_minutes)
        work_end = arrival + timedelta(minutes=config.arrival_offload_minutes)
    elif flight_type is FlightType.DEPARTURE_ONLY:
        departure = _require_datetime(flight.departure_time, "departure_time")
        work_start = departure - timedelta(minutes=config.departure_work_minutes)
        work_end = departure
    else:
        arrival = _require_datetime(flight.arrival_time, "arrival_time")
        departure = _require_datetime(flight.departure_time, "departure_time")
        try:
            correctly_ordered = departure > arrival
        except TypeError as error:
            raise FlightDerivationError(
                "arrival and departure datetimes must have compatible timezone awareness"
            ) from error
        if not correctly_ordered:
            raise FlightDerivationError(
                "turn departure_time must be later than arrival_time"
            )
        work_start = arrival - timedelta(minutes=config.arrival_preparation_minutes)
        work_end = departure

    if work_start >= work_end:
        raise FlightDerivationError("derived work window must have positive duration")
    return work_start, work_end


def derive_flight_operational_facts(
    flight: Flight, config: OptimizerConfig
) -> FlightOperationalFacts:
    """Derive type, work window, directional numbers, and service category."""

    flight_type = classify_flight_type(flight)
    work_start, work_end = derive_work_window(flight, config)
    _require_valid_express_threshold(config)
    arrival_number = (
        parse_numeric_flight_number(flight.arrival_flight_number)
        if flight.arrival_flight_number is not None
        else None
    )
    departure_number = (
        parse_numeric_flight_number(flight.departure_flight_number)
        if flight.departure_flight_number is not None
        else None
    )
    arrival_express = (
        arrival_number >= config.express_threshold
        if arrival_number is not None
        else None
    )
    departure_express = (
        departure_number >= config.express_threshold
        if departure_number is not None
        else None
    )
    if flight_type is FlightType.TURN and arrival_express != departure_express:
        raise FlightDerivationError(
            "turn arrival and departure must have the same service category"
        )
    express = arrival_express if arrival_express is not None else departure_express
    assert express is not None
    return FlightOperationalFacts(
        flight_type=flight_type,
        work_start=work_start,
        work_end=work_end,
        arrival_numeric_flight_number=arrival_number,
        departure_numeric_flight_number=departure_number,
        express=express,
    )


def _classify_side(
    flight_number: object, timestamp: object, direction: str
) -> bool:
    if (flight_number is None) != (timestamp is None):
        raise FlightDerivationError(
            f"{direction} flight number and timestamp must be supplied together"
        )
    if flight_number is None:
        return False
    if not isinstance(flight_number, str):
        raise FlightDerivationError(f"{direction} flight number must be a string")
    parse_numeric_flight_number(flight_number)
    _require_datetime(timestamp, f"{direction}_time")
    return True


def _require_datetime(value: object, field_name: str) -> datetime:
    if not isinstance(value, datetime):
        raise FlightDerivationError(f"{field_name} must be a datetime")
    return value


def _require_valid_timing_config(config: OptimizerConfig) -> None:
    for field_name in (
        "arrival_preparation_minutes",
        "arrival_offload_minutes",
        "departure_work_minutes",
    ):
        value = getattr(config, field_name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise FlightDerivationError(f"{field_name} must be a positive integer")


def _require_valid_express_threshold(config: OptimizerConfig) -> None:
    threshold = config.express_threshold
    if (
        not isinstance(threshold, int)
        or isinstance(threshold, bool)
        or threshold < 0
    ):
        raise FlightDerivationError("express_threshold must be a non-negative integer")
