"""Pure helpers for half-open datetime intervals."""

from datetime import datetime


class InvalidIntervalError(ValueError):
    """Raised when an interval is malformed or cannot be compared safely."""


def intervals_overlap(
    first_start: datetime,
    first_end: datetime,
    second_start: datetime,
    second_end: datetime,
) -> bool:
    """Return whether two positive-duration half-open intervals overlap."""

    values = (first_start, first_end, second_start, second_end)
    if any(not isinstance(value, datetime) for value in values):
        raise InvalidIntervalError("all interval boundaries must be datetimes")
    awareness = {_is_aware(value) for value in values}
    if len(awareness) != 1:
        raise InvalidIntervalError(
            "all interval boundaries must have compatible timezone awareness"
        )
    try:
        if first_start >= first_end:
            raise InvalidIntervalError("first interval must have positive duration")
        if second_start >= second_end:
            raise InvalidIntervalError("second interval must have positive duration")
        return first_start < second_end and second_start < first_end
    except TypeError as error:
        raise InvalidIntervalError(
            "all interval boundaries must be mutually comparable"
        ) from error


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None
