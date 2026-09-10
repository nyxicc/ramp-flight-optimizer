"""Conservative cell normalization and explicit airport wall-clock policy."""

import re
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from openpyxl.utils.datetime import from_excel

from ramp_optimizer.timing import FlightNumberParseError, parse_numeric_flight_number
from ramp_optimizer_imports.flight_models import FlightStatus, FlightTimePolicy
from ramp_optimizer_imports.models import ImportError


def flight_number(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if not isfinite(value) or value < 0 or value != int(value):
            return None
        value = str(int(value))
    if not isinstance(value, str) or len(value) > 40:
        return None
    text = "".join(value.split()).upper()
    try:
        number = parse_numeric_flight_number(text)
    except FlightNumberParseError:
        return None
    prefix = re.match(r"[A-Z]*", text)
    assert prefix is not None
    return prefix.group() + str(number)


def location(value: object, *, airport: bool = False) -> str | None:
    if value is None:
        return None
    if isinstance(value, (str, int)) and not isinstance(value, bool):
        text = " ".join(str(value).split()).upper()
        pattern = r"[A-Z]{3}" if airport else r"[A-Z0-9][A-Z0-9 /-]{0,15}"
        if re.fullmatch(pattern, text):
            return text
    return None


def operational_status(value: object) -> FlightStatus:
    if value is None or value == "":
        return FlightStatus.NORMAL
    if not isinstance(value, str):
        return FlightStatus.UNKNOWN
    text = " ".join(value.upper().split())
    if not text:
        return FlightStatus.NORMAL
    if re.fullmatch(r"TERM(?: \d+)?", text):
        return FlightStatus.TERMINATING
    if text == "AOG":
        return FlightStatus.AOG
    if text in {"CANCELLED", "CANCELED", "CXL"}:
        return FlightStatus.CANCELLED
    return FlightStatus.UNKNOWN


def validate_policy(policy: FlightTimePolicy) -> None:
    try:
        ZoneInfo(policy.airport_timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise ImportError("INVALID_AIRPORT_TIMEZONE") from None
    if policy.operational_day_start.tzinfo is not None or policy.planning_basis not in {
        "SCHEDULED",
        "ESTIMATED",
    }:
        raise ImportError("INVALID_FLIGHT_TIME_POLICY")


def localize(value: datetime, zone: ZoneInfo) -> datetime:
    """Reject DST gaps/folds unless an explicit offset disambiguates them."""
    if value.utcoffset() is not None:
        return value.astimezone(zone)
    candidates = []
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold)
        if candidate.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None) == value:
            candidates.append(candidate)
    if not candidates or len({c.utcoffset() for c in candidates}) != 1:
        raise ValueError("Ambiguous or nonexistent airport wall time.")
    return candidates[0]


def timestamp(
    value: object, day: date, policy: FlightTimePolicy, epoch: datetime
) -> tuple[datetime | None, bool]:
    """Return airport-aware time and whether a source date was explicitly present."""
    zone = ZoneInfo(policy.airport_timezone)
    explicit = False
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not isfinite(value) or value < 0:
                return None, False
            value = from_excel(value, epoch)
        if isinstance(value, str):
            text = value.strip()
            if re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", text):
                hour, minute, *seconds = map(int, text.split(":"))
                value = time(hour, minute, seconds[0] if seconds else 0)
            elif re.fullmatch(r"\d{1,2}:\d{2}\s*[AP]M", text, re.I):
                value = datetime.strptime(text.replace(" ", "").upper(), "%I:%M%p").time()
            else:
                value = datetime.fromisoformat(text)
        if isinstance(value, datetime):
            explicit = True
            return datetime.fromisoformat(localize(value, zone).isoformat()), explicit
        if isinstance(value, time) and value.tzinfo is None:
            effective_date = day + timedelta(days=int(value < policy.operational_day_start))
            return datetime.fromisoformat(
                localize(datetime.combine(effective_date, value), zone).isoformat()
            ), False
    except (ValueError, TypeError, OverflowError):
        pass
    return None, explicit
