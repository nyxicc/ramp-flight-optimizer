"""Tests for half-open datetime interval behavior."""

from datetime import datetime, timezone

import pytest

from ramp_optimizer import InvalidIntervalError, intervals_overlap


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 2, hour, minute)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ((at(8), at(9)), (at(8), at(9))),
        ((at(8), at(9)), (at(8, 30), at(10))),
        ((at(8), at(11)), (at(9), at(10))),
    ],
)
def test_overlapping_intervals(
    first: tuple[datetime, datetime], second: tuple[datetime, datetime]
) -> None:
    assert intervals_overlap(*first, *second)


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ((at(8), at(9)), (at(9), at(10))),
        ((at(8), at(9)), (at(10), at(11))),
    ],
)
def test_touching_or_separated_intervals_do_not_overlap(
    first: tuple[datetime, datetime], second: tuple[datetime, datetime]
) -> None:
    assert not intervals_overlap(*first, *second)


@pytest.mark.parametrize("first", [(at(8), at(8)), (at(9), at(8))])
def test_zero_length_or_reversed_interval_is_rejected(
    first: tuple[datetime, datetime],
) -> None:
    with pytest.raises(InvalidIntervalError, match="positive duration"):
        intervals_overlap(*first, at(10), at(11))


def test_incompatible_timezone_awareness_is_rejected_clearly() -> None:
    with pytest.raises(InvalidIntervalError, match="timezone awareness"):
        intervals_overlap(
            at(8),
            at(9),
            datetime(2026, 9, 2, 10, tzinfo=timezone.utc),
            datetime(2026, 9, 2, 11, tzinfo=timezone.utc),
        )
