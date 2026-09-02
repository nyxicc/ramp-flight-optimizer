"""Tests for solver-independent flight timing and classification."""

from dataclasses import replace
from datetime import datetime, timezone

import pytest

from ramp_optimizer import (
    Flight,
    FlightDerivationError,
    FlightNumberParseError,
    FlightType,
    OptimizerConfig,
    classify_flight_type,
    derive_flight_operational_facts,
    derive_work_window,
    parse_numeric_flight_number,
)


def arrival(number: str = "1428", at: datetime | None = None) -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=at or datetime(2026, 9, 2, 9, 2),
    )


def departure(number: str = "2690", at: datetime | None = None) -> Flight:
    return Flight(
        departure_flight_number=number,
        departure_time=at or datetime(2026, 9, 2, 6),
    )


def turn(
    arrival_number: str = "1428",
    departure_number: str = "1814",
    arrival_at: datetime | None = None,
    departure_at: datetime | None = None,
) -> Flight:
    return Flight(
        arrival_flight_number=arrival_number,
        arrival_time=arrival_at or datetime(2026, 9, 2, 9, 2),
        departure_flight_number=departure_number,
        departure_time=departure_at or datetime(2026, 9, 2, 10, 10),
    )


@pytest.mark.parametrize(
    ("flight", "expected"),
    [
        (arrival(), FlightType.ARRIVAL_ONLY),
        (departure(), FlightType.DEPARTURE_ONLY),
        (turn(), FlightType.TURN),
    ],
)
def test_classifies_populated_directional_sides(
    flight: Flight, expected: FlightType
) -> None:
    assert classify_flight_type(flight) is expected


@pytest.mark.parametrize(
    "flight",
    [
        Flight(),
        Flight(arrival_flight_number="1428"),
        Flight(arrival_time=datetime(2026, 9, 2, 9, 2)),
        Flight(departure_flight_number="2690"),
        Flight(departure_time=datetime(2026, 9, 2, 6)),
    ],
)
def test_classification_rejects_missing_or_mismatched_sides(flight: Flight) -> None:
    with pytest.raises(FlightDerivationError):
        classify_flight_type(flight)


@pytest.mark.parametrize(
    "flight",
    [
        Flight(
            arrival_flight_number=123,  # type: ignore[arg-type]
            arrival_time=datetime(2026, 9, 2, 9, 2),
        ),
        Flight(
            arrival_flight_number="UA12A",
            arrival_time=datetime(2026, 9, 2, 9, 2),
        ),
        Flight(
            arrival_flight_number="1428",
            arrival_time="09:02",  # type: ignore[arg-type]
        ),
    ],
)
def test_classification_rejects_malformed_side_values(flight: Flight) -> None:
    with pytest.raises(FlightDerivationError):
        classify_flight_type(flight)


def test_exact_default_work_windows() -> None:
    config = OptimizerConfig()

    assert derive_work_window(arrival(), config) == (
        datetime(2026, 9, 2, 8, 52),
        datetime(2026, 9, 2, 9, 22),
    )
    assert derive_work_window(departure(), config) == (
        datetime(2026, 9, 2, 5),
        datetime(2026, 9, 2, 6),
    )
    assert derive_work_window(turn(), config) == (
        datetime(2026, 9, 2, 8, 52),
        datetime(2026, 9, 2, 10, 10),
    )


def test_custom_timing_configuration() -> None:
    config = replace(
        OptimizerConfig(),
        arrival_preparation_minutes=15,
        arrival_offload_minutes=25,
        departure_work_minutes=75,
    )

    assert derive_work_window(arrival(), config) == (
        datetime(2026, 9, 2, 8, 47),
        datetime(2026, 9, 2, 9, 27),
    )
    assert derive_work_window(departure(), config)[0] == datetime(2026, 9, 2, 4, 45)
    assert derive_work_window(turn(), config)[0] == datetime(2026, 9, 2, 8, 47)


def test_work_windows_cross_midnight_naturally() -> None:
    config = OptimizerConfig()
    arrival_flight = arrival("1428", datetime(2026, 9, 2, 23, 55))
    departure_flight = departure("2690", datetime(2026, 9, 3, 0, 30))
    turn_flight = turn(
        arrival_at=datetime(2026, 9, 2, 23, 30),
        departure_at=datetime(2026, 9, 3, 0, 20),
    )

    assert derive_work_window(arrival_flight, config) == (
        datetime(2026, 9, 2, 23, 45),
        datetime(2026, 9, 3, 0, 15),
    )
    assert derive_work_window(departure_flight, config) == (
        datetime(2026, 9, 2, 23, 30),
        datetime(2026, 9, 3, 0, 30),
    )
    assert derive_work_window(turn_flight, config) == (
        datetime(2026, 9, 2, 23, 20),
        datetime(2026, 9, 3, 0, 20),
    )


def test_timezone_aware_work_window_preserves_timezone() -> None:
    flight = arrival("1428", datetime(2026, 9, 2, 9, 2, tzinfo=timezone.utc))

    work_start, work_end = derive_work_window(flight, OptimizerConfig())

    assert work_start.tzinfo is timezone.utc
    assert work_end.tzinfo is timezone.utc


def test_gate_does_not_change_operational_facts() -> None:
    without_gate = turn()
    with_gate = replace(without_gate, gate="B4")

    assert derive_flight_operational_facts(
        without_gate, OptimizerConfig()
    ) == derive_flight_operational_facts(with_gate, OptimizerConfig())


def test_invalid_turn_order_raises_domain_exception() -> None:
    flight = turn(
        arrival_at=datetime(2026, 9, 2, 10),
        departure_at=datetime(2026, 9, 2, 10),
    )

    with pytest.raises(FlightDerivationError, match="later"):
        derive_work_window(flight, OptimizerConfig())


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1428", 1428),
        ("UA123", 123),
        ("UA4521", 4521),
        ("OO3550", 3550),
        ("UA03000", 3000),
        ("  UA123  ", 123),
    ],
)
def test_numeric_flight_number_parsing(value: str, expected: int) -> None:
    assert parse_numeric_flight_number(value) == expected


@pytest.mark.parametrize("value", ["", "   ", "UA", "FLIGHT", "UA12A"])
def test_invalid_flight_number_never_returns_a_fallback(value: str) -> None:
    with pytest.raises(FlightNumberParseError):
        parse_numeric_flight_number(value)


def test_non_string_flight_number_raises_narrow_exception() -> None:
    with pytest.raises(FlightNumberParseError):
        parse_numeric_flight_number(123)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("number", "expected_express"),
    [("2999", False), ("3000", True), ("3001", True)],
)
def test_default_express_threshold(number: str, expected_express: bool) -> None:
    facts = derive_flight_operational_facts(arrival(number), OptimizerConfig())

    assert facts.express is expected_express


def test_custom_express_threshold() -> None:
    config = replace(OptimizerConfig(), express_threshold=4000)

    assert not derive_flight_operational_facts(departure("3999"), config).express
    assert derive_flight_operational_facts(departure("4000"), config).express


def test_operational_facts_include_directional_numbers_for_each_type() -> None:
    config = OptimizerConfig()
    arrival_facts = derive_flight_operational_facts(arrival("2999"), config)
    departure_facts = derive_flight_operational_facts(departure("3001"), config)

    assert arrival_facts.arrival_numeric_flight_number == 2999
    assert arrival_facts.departure_numeric_flight_number is None
    assert arrival_facts.flight_type is FlightType.ARRIVAL_ONLY
    assert departure_facts.arrival_numeric_flight_number is None
    assert departure_facts.departure_numeric_flight_number == 3001
    assert departure_facts.flight_type is FlightType.DEPARTURE_ONLY


@pytest.mark.parametrize(
    ("flight", "expected_express"),
    [(turn("1428", "1814"), False), (turn("3550", "4521"), True)],
)
def test_turns_require_and_report_one_service_category(
    flight: Flight, expected_express: bool
) -> None:
    facts = derive_flight_operational_facts(flight, OptimizerConfig())

    assert facts.express is expected_express


def test_mixed_category_turn_raises_instead_of_choosing_a_side() -> None:
    with pytest.raises(FlightDerivationError, match="same service category"):
        derive_flight_operational_facts(turn("2999", "3000"), OptimizerConfig())


def test_malformed_number_cannot_be_classified_as_mainline() -> None:
    with pytest.raises(FlightNumberParseError):
        derive_flight_operational_facts(arrival("UA12A"), OptimizerConfig())


def test_invalid_configuration_raises_meaningful_domain_exception() -> None:
    bad_timing = replace(OptimizerConfig(), arrival_offload_minutes=0)
    bad_threshold = replace(OptimizerConfig(), express_threshold=-1)

    with pytest.raises(FlightDerivationError, match="arrival_offload_minutes"):
        derive_work_window(arrival(), bad_timing)
    with pytest.raises(FlightDerivationError, match="express_threshold"):
        derive_flight_operational_facts(arrival(), bad_threshold)
