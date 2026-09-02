"""Structured validation for configuration and pure optimizer-domain input."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite

from ramp_optimizer.config import OptimizerConfig, TeamWorkImportConfig
from ramp_optimizer.eligibility import assess_employee_flight_eligibility
from ramp_optimizer.enums import OperationalRole, Qualification
from ramp_optimizer.intervals import InvalidIntervalError, intervals_overlap
from ramp_optimizer.models import EmployeeShift, FixedAssignment, Flight, OperationalDay
from ramp_optimizer.staffing import staffing_requirements_for
from ramp_optimizer.timing import (
    FlightDerivationError,
    FlightNumberParseError,
    FlightOperationalFacts,
    derive_flight_operational_facts,
    derive_work_window,
    parse_numeric_flight_number,
)


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    """One input problem with a stable code and source path."""

    code: str
    path: str
    message: str


class InputValidationError(ValueError):
    """Raised when one or more optimizer inputs are invalid."""

    def __init__(self, issues: tuple[ValidationIssue, ...]) -> None:
        if not issues:
            raise ValueError("InputValidationError requires at least one issue")
        self.issues = issues
        details = "; ".join(f"{issue.path}: {issue.message}" for issue in issues)
        super().__init__(f"Input validation failed: {details}")


def validate_config(config: OptimizerConfig) -> tuple[ValidationIssue, ...]:
    """Return every optimizer-configuration error."""

    issues: list[ValidationIssue] = []
    positive_integer_fields = (
        "arrival_preparation_minutes",
        "arrival_offload_minutes",
        "departure_work_minutes",
        "minimum_staff",
        "normal_preferred_staff",
        "heavy_preferred_staff",
        "required_break_minutes",
        "consecutive_reset_minutes",
        "continuity_horizon_minutes",
        "workload_scale",
        "solver_num_search_workers",
    )
    for field_name in positive_integer_fields:
        value = getattr(config, field_name)
        if not _is_integer(value) or value <= 0:
            issues.append(
                ValidationIssue(
                    "INVALID_POSITIVE_INTEGER",
                    f"config.{field_name}",
                    "must be a positive integer",
                )
            )

    staffing_values = (
        config.minimum_staff,
        config.normal_preferred_staff,
        config.heavy_preferred_staff,
    )
    if all(_is_integer(value) for value in staffing_values):
        if config.minimum_staff > config.normal_preferred_staff:
            issues.append(
                ValidationIssue(
                    "INVALID_NORMAL_STAFFING_LEVELS",
                    "config",
                    "normal preferred staffing must be at least minimum staffing",
                )
            )
        if config.minimum_staff > config.heavy_preferred_staff:
            issues.append(
                ValidationIssue(
                    "INVALID_HEAVY_STAFFING_LEVELS",
                    "config",
                    "heavy preferred staffing must be at least minimum staffing",
                )
            )
        if config.heavy_preferred_staff < config.normal_preferred_staff:
            issues.append(
                ValidationIssue(
                    "INVALID_STAFFING_RELATIONSHIP",
                    "config",
                    "heavy preferred staffing must be at least normal preferred staffing",
                )
            )

    if not _is_integer(config.express_threshold) or config.express_threshold < 0:
        issues.append(
            ValidationIssue(
                "INVALID_EXPRESS_THRESHOLD",
                "config.express_threshold",
                "must be a non-negative integer",
            )
        )

    if not _is_finite_number_in_range(
        config.express_workload_factor, minimum=0, maximum=1, minimum_exclusive=True
    ):
        issues.append(
            ValidationIssue(
                "INVALID_EXPRESS_WORKLOAD_FACTOR",
                "config.express_workload_factor",
                "must be finite and greater than 0 and no more than 1",
            )
        )

    multiplier = config.three_person_workload_multiplier
    if not _is_finite_number(multiplier) or multiplier < 1:
        issues.append(
            ValidationIssue(
                "INVALID_THREE_PERSON_MULTIPLIER",
                "config.three_person_workload_multiplier",
                "must be finite and at least 1",
            )
        )

    time_limit = config.solver_time_limit_seconds
    if not _is_finite_number(time_limit) or time_limit <= 0:
        issues.append(
            ValidationIssue(
                "INVALID_SOLVER_TIME_LIMIT",
                "config.solver_time_limit_seconds",
                "must be finite and greater than 0",
            )
        )

    if not _is_integer(config.solver_random_seed) or config.solver_random_seed < 0:
        issues.append(
            ValidationIssue(
                "INVALID_SOLVER_RANDOM_SEED",
                "config.solver_random_seed",
                "must be a non-negative integer",
            )
        )

    for field_name in (
        "allow_leads_for_minimum_staffing",
        "allow_trainees_for_assignments",
        "allow_possible_ramp_support_for_assignments",
    ):
        if not isinstance(getattr(config, field_name), bool):
            issues.append(
                ValidationIssue(
                    "INVALID_BOOLEAN",
                    f"config.{field_name}",
                    "must be a boolean",
                )
            )

    return tuple(issues)


def validate_teamwork_import_config(
    config: TeamWorkImportConfig,
) -> tuple[ValidationIssue, ...]:
    """Return errors in TeamWork workbook-import configuration."""

    issues: list[ValidationIssue] = []
    if _normalized_text(config.worksheet_name) is None:
        issues.append(
            ValidationIssue(
                "INVALID_WORKSHEET_NAME",
                "import_config.worksheet_name",
                "must be a non-blank string",
            )
        )
    if not _is_integer(config.header_scan_limit) or config.header_scan_limit <= 0:
        issues.append(
            ValidationIssue(
                "INVALID_HEADER_SCAN_LIMIT",
                "import_config.header_scan_limit",
                "must be a positive integer",
            )
        )
    if (
        not _is_finite_number(config.maximum_shift_hours)
        or config.maximum_shift_hours <= 0
        or config.maximum_shift_hours > 24
    ):
        issues.append(
            ValidationIssue(
                "INVALID_MAXIMUM_SHIFT_HOURS",
                "import_config.maximum_shift_hours",
                "must be finite, greater than 0, and no more than 24",
            )
        )
    if not _is_finite_number(config.hours_tolerance) or config.hours_tolerance < 0:
        issues.append(
            ValidationIssue(
                "INVALID_HOURS_TOLERANCE",
                "import_config.hours_tolerance",
                "must be a finite non-negative number of hours",
            )
        )

    mapping_keys: set[str] = set()
    for index, mapping in enumerate(config.position_role_mappings):
        path = f"import_config.position_role_mappings[{index}]"
        if not isinstance(mapping, tuple) or len(mapping) != 2:
            issues.append(
                ValidationIssue(
                    "INVALID_POSITION_MAPPING", path, "must be a (label, role) pair"
                )
            )
            continue
        label, role = mapping
        normalized = _normalized_text(label)
        if normalized is None:
            issues.append(
                ValidationIssue(
                    "INVALID_POSITION_MAPPING_LABEL",
                    f"{path}[0]",
                    "must be a non-blank string",
                )
            )
        elif normalized in mapping_keys:
            issues.append(
                ValidationIssue(
                    "DUPLICATE_POSITION_MAPPING",
                    f"{path}[0]",
                    "duplicates another normalized position label",
                )
            )
        else:
            mapping_keys.add(normalized)
        if not isinstance(role, OperationalRole):
            issues.append(
                ValidationIssue(
                    "INVALID_POSITION_MAPPING_ROLE",
                    f"{path}[1]",
                    "must be an OperationalRole value",
                )
            )

    return tuple(issues)


def validate_operational_day(
    day: OperationalDay,
    config: OptimizerConfig | None = None,
    *,
    include_leads: bool = False,
    allow_trainees: bool | None = None,
    allow_possible_ramp_support: bool | None = None,
) -> tuple[ValidationIssue, ...]:
    """Return all structural errors in one operational-day input."""

    active_config = config or OptimizerConfig()
    issues: list[ValidationIssue] = []
    employee_ids: dict[str, int] = {}
    valid_employee_indices: set[int] = set()
    arrival_numbers: dict[int, int] = {}
    departure_numbers: dict[int, int] = {}
    valid_flight_indices: set[int] = set()
    datetime_awareness: set[bool] = set()

    if not isinstance(day.operational_date, date) or isinstance(
        day.operational_date, datetime
    ):
        issues.append(
            ValidationIssue(
                "INVALID_OPERATIONAL_DATE",
                "operational_date",
                "must be a date value, not a datetime",
            )
        )

    for index, employee in enumerate(day.employees):
        path = f"employees[{index}]"
        issue_count_before_employee = len(issues)
        normalized_id = _normalized_text(employee.employee_id)
        if normalized_id is None:
            issues.append(
                ValidationIssue(
                    "INVALID_EMPLOYEE_ID",
                    f"{path}.employee_id",
                    "must be a non-blank string",
                )
            )
        elif normalized_id in employee_ids:
            issues.append(
                ValidationIssue(
                    "DUPLICATE_EMPLOYEE_ID",
                    f"{path}.employee_id",
                    f"duplicates employees[{employee_ids[normalized_id]}].employee_id",
                )
            )
        else:
            employee_ids[normalized_id] = index

        if _normalized_text(employee.name) is None:
            issues.append(
                ValidationIssue(
                    "INVALID_EMPLOYEE_NAME",
                    f"{path}.name",
                    "must be a non-blank string",
                )
            )
        if not isinstance(employee.enabled, bool):
            issues.append(
                ValidationIssue(
                    "INVALID_BOOLEAN", f"{path}.enabled", "must be a boolean"
                )
            )
        if not isinstance(employee.qualifications, frozenset) or any(
            not isinstance(value, Qualification) for value in employee.qualifications
        ):
            issues.append(
                ValidationIssue(
                    "INVALID_QUALIFICATIONS",
                    f"{path}.qualifications",
                    "must be a frozenset of Qualification values",
                )
            )
        if len(issues) == issue_count_before_employee:
            valid_employee_indices.add(index)

    valid_shifts: dict[str, list[tuple[int, EmployeeShift]]] = defaultdict(list)
    for index, shift in enumerate(day.employee_shifts):
        path = f"employee_shifts[{index}]"
        normalized_id = _normalized_text(shift.employee_id)
        if normalized_id is None:
            issues.append(
                ValidationIssue(
                    "INVALID_EMPLOYEE_ID",
                    f"{path}.employee_id",
                    "must be a non-blank string",
                )
            )
        elif normalized_id not in employee_ids:
            issues.append(
                ValidationIssue(
                    "UNKNOWN_SHIFT_EMPLOYEE",
                    f"{path}.employee_id",
                    "does not reference an employee in this operational day",
                )
            )

        start_valid = _record_datetime(
            shift.start, f"{path}.start", issues, datetime_awareness
        )
        end_valid = _record_datetime(
            shift.end, f"{path}.end", issues, datetime_awareness
        )
        role_valid = isinstance(shift.normalized_role, OperationalRole)
        if start_valid and end_valid and _same_awareness(shift.start, shift.end):
            if shift.start >= shift.end:
                issues.append(
                    ValidationIssue(
                        "INVALID_EMPLOYEE_SHIFT", path, "start must be earlier than end"
                    )
                )
            elif (
                normalized_id is not None
                and normalized_id in employee_ids
                and role_valid
            ):
                valid_shifts[normalized_id].append((index, shift))

        if not role_valid:
            issues.append(
                ValidationIssue(
                    "INVALID_OPERATIONAL_ROLE",
                    f"{path}.normalized_role",
                    "must be an OperationalRole value",
                )
            )
    _validate_shift_collisions(valid_shifts, issues)

    for index, flight in enumerate(day.flights):
        path = f"flights[{index}]"
        issue_count_before_flight = len(issues)
        if (
            flight.arrival_flight_number is None
            and flight.arrival_time is None
            and flight.departure_flight_number is None
            and flight.departure_time is None
        ):
            issues.append(
                ValidationIssue(
                    "MISSING_FLIGHT_SIDES",
                    path,
                    "must supply an arrival side, departure side, or both",
                )
            )

        arrival_number, arrival_number_valid = _validate_directional_flight_number(
            flight.arrival_flight_number, "arrival", path, issues
        )
        departure_number, departure_number_valid = _validate_directional_flight_number(
            flight.departure_flight_number, "departure", path, issues
        )

        if flight.arrival_flight_number is None and flight.arrival_time is not None:
            issues.append(
                ValidationIssue(
                    "ARRIVAL_TIME_WITHOUT_FLIGHT_NUMBER",
                    f"{path}.arrival_time",
                    "arrival_time requires arrival_flight_number",
                )
            )
        if flight.arrival_flight_number is not None and flight.arrival_time is None:
            issues.append(
                ValidationIssue(
                    "ARRIVAL_FLIGHT_NUMBER_WITHOUT_TIME",
                    f"{path}.arrival_flight_number",
                    "arrival_flight_number requires arrival_time",
                )
            )
        if flight.departure_flight_number is None and flight.departure_time is not None:
            issues.append(
                ValidationIssue(
                    "DEPARTURE_TIME_WITHOUT_FLIGHT_NUMBER",
                    f"{path}.departure_time",
                    "departure_time requires departure_flight_number",
                )
            )
        if flight.departure_flight_number is not None and flight.departure_time is None:
            issues.append(
                ValidationIssue(
                    "DEPARTURE_FLIGHT_NUMBER_WITHOUT_TIME",
                    f"{path}.departure_flight_number",
                    "departure_flight_number requires departure_time",
                )
            )

        arrival_time_valid = flight.arrival_time is None
        departure_time_valid = flight.departure_time is None
        if flight.arrival_time is not None:
            arrival_time_valid = _record_datetime(
                flight.arrival_time,
                f"{path}.arrival_time",
                issues,
                datetime_awareness,
                code="INVALID_ARRIVAL_TIME",
            )
        if flight.departure_time is not None:
            departure_time_valid = _record_datetime(
                flight.departure_time,
                f"{path}.departure_time",
                issues,
                datetime_awareness,
                code="INVALID_DEPARTURE_TIME",
            )

        arrival_complete = (
            arrival_number_valid
            and flight.arrival_flight_number is not None
            and flight.arrival_time is not None
            and arrival_time_valid
        )
        departure_complete = (
            departure_number_valid
            and flight.departure_flight_number is not None
            and flight.departure_time is not None
            and departure_time_valid
        )
        arrival_side_valid = (
            flight.arrival_flight_number is None and flight.arrival_time is None
        ) or arrival_complete
        departure_side_valid = (
            flight.departure_flight_number is None and flight.departure_time is None
        ) or departure_complete

        if arrival_complete:
            _record_directional_uniqueness(
                arrival_number,
                index,
                "arrival",
                arrival_numbers,
                issues,
            )
        if departure_complete:
            _record_directional_uniqueness(
                departure_number,
                index,
                "departure",
                departure_numbers,
                issues,
            )

        turn_datetimes_comparable = (
            arrival_complete
            and departure_complete
            and _same_awareness(flight.arrival_time, flight.departure_time)
        )
        if (
            turn_datetimes_comparable
            and flight.departure_time <= flight.arrival_time
        ):
            issues.append(
                ValidationIssue(
                    "INVALID_TURN_TIMES",
                    path,
                    "departure_time must be later than arrival_time for a turn",
                )
            )

        if (
            arrival_complete
            and departure_complete
            and _valid_express_threshold(active_config)
            and (arrival_number >= active_config.express_threshold)
            != (departure_number >= active_config.express_threshold)
        ):
            issues.append(
                ValidationIssue(
                    "MIXED_TURN_SERVICE_CATEGORY",
                    path,
                    "turn arrival and departure must both be Mainline or both be Express",
                )
            )

        prerequisites_valid = (
            (arrival_complete or departure_complete)
            and arrival_side_valid
            and departure_side_valid
            and not (
                arrival_complete
                and departure_complete
                and not turn_datetimes_comparable
            )
            and not (
                turn_datetimes_comparable
                and flight.departure_time <= flight.arrival_time
            )
            and _valid_timing_config(active_config)
        )
        if prerequisites_valid:
            try:
                work_start, work_end = derive_work_window(flight, active_config)
                if work_start >= work_end:
                    raise FlightDerivationError(
                        "derived work window must have positive duration"
                    )
            except FlightDerivationError as error:
                issues.append(
                    ValidationIssue(
                        "INVALID_DERIVED_WORK_WINDOW", path, str(error)
                    )
                )

        if flight.gate is not None and not isinstance(flight.gate, str):
            issues.append(
                ValidationIssue(
                    "INVALID_GATE",
                    f"{path}.gate",
                    "must be a string or None",
                )
            )
        if not isinstance(flight.heavy, bool):
            issues.append(
                ValidationIssue(
                    "INVALID_BOOLEAN", f"{path}.heavy", "must be a boolean"
                )
            )

        if len(issues) == issue_count_before_flight:
            valid_flight_indices.add(index)

    mixed_datetime_awareness = len(datetime_awareness) > 1
    if mixed_datetime_awareness:
        issues.append(
            ValidationIssue(
                "MIXED_DATETIME_AWARENESS",
                "operational_day",
                "all datetimes must be consistently timezone-aware or timezone-naive",
            )
        )

    _validate_fixed_assignments(
        day,
        active_config,
        employee_ids,
        valid_employee_indices,
        valid_shifts,
        valid_flight_indices,
        issues,
        allow_calculations=(
            not mixed_datetime_awareness
            and _valid_timing_config(active_config)
            and _valid_express_threshold(active_config)
        ),
        include_leads=include_leads,
        allow_trainees=allow_trainees,
        allow_possible_ramp_support=allow_possible_ramp_support,
    )
    return tuple(issues)


def validate_or_raise(
    day: OperationalDay,
    config: OptimizerConfig,
    *,
    include_leads: bool = False,
    allow_trainees: bool | None = None,
    allow_possible_ramp_support: bool | None = None,
) -> None:
    """Raise one aggregate exception if configuration or input is invalid."""

    issues = validate_config(config) + validate_operational_day(
        day,
        config,
        include_leads=include_leads,
        allow_trainees=allow_trainees,
        allow_possible_ramp_support=allow_possible_ramp_support,
    )
    if issues:
        raise InputValidationError(issues)


def _validate_fixed_assignments(
    day: OperationalDay,
    config: OptimizerConfig,
    employee_ids: dict[str, int],
    valid_employee_indices: set[int],
    valid_shifts: dict[str, list[tuple[int, EmployeeShift]]],
    valid_flight_indices: set[int],
    issues: list[ValidationIssue],
    *,
    allow_calculations: bool,
    include_leads: bool,
    allow_trainees: bool | None,
    allow_possible_ramp_support: bool | None,
) -> None:
    records: list[tuple[int, FixedAssignment, str, int, int]] = []
    seen_assignments: dict[tuple[str, int], int] = {}

    for index, fixed in enumerate(day.fixed_assignments):
        path = f"fixed_assignments[{index}]"
        if not isinstance(fixed, FixedAssignment):
            issues.append(
                ValidationIssue(
                    "INVALID_FIXED_ASSIGNMENT",
                    path,
                    "must be a FixedAssignment value",
                )
            )
            continue

        normalized_employee_id = _normalized_text(fixed.employee_id)
        employee_index = (
            employee_ids.get(normalized_employee_id)
            if normalized_employee_id is not None
            else None
        )
        if employee_index is None:
            issues.append(
                ValidationIssue(
                    "UNKNOWN_FIXED_ASSIGNMENT_EMPLOYEE",
                    f"{path}.employee_id",
                    "does not reference an employee in this operational day",
                )
            )

        flight_index = _find_flight_index(fixed.flight, day.flights)
        if flight_index is None:
            issues.append(
                ValidationIssue(
                    "UNKNOWN_FIXED_ASSIGNMENT_FLIGHT",
                    f"{path}.flight",
                    "does not reference a flight in this operational day",
                )
            )

        if (
            normalized_employee_id is None
            or employee_index is None
            or flight_index is None
        ):
            continue

        assignment_key = (normalized_employee_id, flight_index)
        if assignment_key in seen_assignments:
            issues.append(
                ValidationIssue(
                    "DUPLICATE_FIXED_ASSIGNMENT",
                    path,
                    f"duplicates fixed_assignments[{seen_assignments[assignment_key]}]",
                )
            )
            continue
        seen_assignments[assignment_key] = index
        records.append(
            (index, fixed, normalized_employee_id, employee_index, flight_index)
        )

    if _valid_staffing_config(config):
        fixed_count_by_flight: dict[int, int] = defaultdict(int)
        overstaffed_flights: set[int] = set()
        for index, _, _, _, flight_index in records:
            if flight_index not in valid_flight_indices:
                continue
            fixed_count_by_flight[flight_index] += 1
            maximum = staffing_requirements_for(
                day.flights[flight_index], config
            ).maximum
            if (
                fixed_count_by_flight[flight_index] > maximum
                and flight_index not in overstaffed_flights
            ):
                issues.append(
                    ValidationIssue(
                        "FIXED_ASSIGNMENTS_EXCEED_MAXIMUM_STAFFING",
                        f"fixed_assignments[{index}]",
                        f"fixed staffing exceeds the flight maximum of {maximum}",
                    )
                )
                overstaffed_flights.add(flight_index)

    if not allow_calculations:
        return

    facts_by_flight: dict[int, FlightOperationalFacts] = {}
    for flight_index in valid_flight_indices:
        try:
            facts_by_flight[flight_index] = derive_flight_operational_facts(
                day.flights[flight_index], config
            )
        except FlightDerivationError:
            continue

    comparable_records: list[tuple[int, str, int]] = []
    for index, fixed, normalized_id, employee_index, flight_index in records:
        if (
            employee_index not in valid_employee_indices
            or flight_index not in facts_by_flight
        ):
            continue

        employee_shifts = tuple(shift for _, shift in valid_shifts[normalized_id])
        assessment = assess_employee_flight_eligibility(
            day.employees[employee_index],
            employee_shifts,
            day.flights[flight_index],
            config,
            include_leads=include_leads,
            allow_trainees=allow_trainees,
            allow_possible_ramp_support=allow_possible_ramp_support,
        )
        if not assessment.eligible:
            reason_values = ", ".join(reason.value for reason in assessment.reasons)
            issues.append(
                ValidationIssue(
                    "ILLEGAL_FIXED_ASSIGNMENT",
                    f"fixed_assignments[{index}]",
                    f"violates employee-flight eligibility: {reason_values}",
                )
            )

        for earlier_index, earlier_id, earlier_flight_index in comparable_records:
            if earlier_id != normalized_id:
                continue
            earlier_facts = facts_by_flight[earlier_flight_index]
            current_facts = facts_by_flight[flight_index]
            try:
                overlaps = intervals_overlap(
                    earlier_facts.work_start,
                    earlier_facts.work_end,
                    current_facts.work_start,
                    current_facts.work_end,
                )
            except InvalidIntervalError:
                overlaps = False
            if overlaps:
                issues.append(
                    ValidationIssue(
                        "OVERLAPPING_FIXED_ASSIGNMENTS",
                        f"fixed_assignments[{index}]",
                        f"overlaps fixed_assignments[{earlier_index}]",
                    )
                )
                break
        comparable_records.append((index, normalized_id, flight_index))


def _find_flight_index(flight: object, flights: tuple[Flight, ...]) -> int | None:
    if not isinstance(flight, Flight):
        return None
    return next(
        (index for index, candidate in enumerate(flights) if candidate == flight),
        None,
    )


def _validate_shift_collisions(
    shifts_by_employee: dict[str, list[tuple[int, EmployeeShift]]],
    issues: list[ValidationIssue],
) -> None:
    for indexed_shifts in shifts_by_employee.values():
        awareness_groups: dict[bool, list[tuple[int, EmployeeShift]]] = defaultdict(list)
        for indexed_shift in indexed_shifts:
            awareness_groups[_is_aware(indexed_shift[1].start)].append(indexed_shift)
        for comparable_shifts in awareness_groups.values():
            ordered = sorted(
                comparable_shifts, key=lambda item: (item[1].start, item[1].end)
            )
            for position, (index, shift) in enumerate(ordered):
                fingerprint = (
                    shift.start,
                    shift.end,
                    shift.normalized_role,
                )
                for earlier_index, earlier in ordered[:position]:
                    earlier_fingerprint = (
                        earlier.start,
                        earlier.end,
                        earlier.normalized_role,
                    )
                    if fingerprint == earlier_fingerprint:
                        issues.append(
                            ValidationIssue(
                                "DUPLICATE_EMPLOYEE_SHIFT",
                                f"employee_shifts[{index}]",
                                f"duplicates employee_shifts[{earlier_index}]",
                            )
                        )
                        break
                    if shift.start < earlier.end and earlier.start < shift.end:
                        issues.append(
                            ValidationIssue(
                                "OVERLAPPING_EMPLOYEE_SHIFTS",
                                f"employee_shifts[{index}]",
                                f"overlaps employee_shifts[{earlier_index}]",
                            )
                        )
                        break


def _record_datetime(
    value: object,
    path: str,
    issues: list[ValidationIssue],
    datetime_awareness: set[bool],
    code: str = "INVALID_DATETIME",
) -> bool:
    if not isinstance(value, datetime):
        issues.append(
            ValidationIssue(code, path, "must be a datetime value")
        )
        return False
    datetime_awareness.add(_is_aware(value))
    return True


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _same_awareness(left: datetime, right: datetime) -> bool:
    return _is_aware(left) == _is_aware(right)


def _normalized_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split()).casefold()
    return normalized or None


def _is_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and isfinite(value)
    )


def _is_finite_number_in_range(
    value: object,
    *,
    minimum: float,
    maximum: float,
    minimum_exclusive: bool = False,
) -> bool:
    if not _is_finite_number(value):
        return False
    lower_ok = value > minimum if minimum_exclusive else value >= minimum
    return lower_ok and value <= maximum


def _validate_directional_flight_number(
    value: object,
    direction: str,
    flight_path: str,
    issues: list[ValidationIssue],
) -> tuple[int | None, bool]:
    if value is None:
        return None, True
    field_path = f"{flight_path}.{direction}_flight_number"
    if not isinstance(value, str):
        issues.append(
            ValidationIssue(
                f"INVALID_{direction.upper()}_FLIGHT_NUMBER",
                field_path,
                "must be a string or None",
            )
        )
        return None, False
    if not value.strip():
        issues.append(
            ValidationIssue(
                f"BLANK_{direction.upper()}_FLIGHT_NUMBER",
                field_path,
                "must not be blank",
            )
        )
        return None, False
    try:
        return parse_numeric_flight_number(value), True
    except FlightNumberParseError:
        issues.append(
            ValidationIssue(
                f"MALFORMED_{direction.upper()}_FLIGHT_NUMBER",
                field_path,
                "must contain optional letters followed by terminal digits",
            )
        )
        return None, False


def _record_directional_uniqueness(
    numeric_flight_number: int,
    index: int,
    direction: str,
    seen: dict[int, int],
    issues: list[ValidationIssue],
) -> None:
    if numeric_flight_number in seen:
        issues.append(
            ValidationIssue(
                f"DUPLICATE_{direction.upper()}_FLIGHT_NUMBER",
                f"flights[{index}].{direction}_flight_number",
                f"duplicates flights[{seen[numeric_flight_number]}].{direction}_flight_number",
            )
        )
    else:
        seen[numeric_flight_number] = index


def _valid_express_threshold(config: OptimizerConfig) -> bool:
    return _is_integer(config.express_threshold) and config.express_threshold >= 0


def _valid_timing_config(config: OptimizerConfig) -> bool:
    return all(
        _is_integer(value) and value > 0
        for value in (
            config.arrival_preparation_minutes,
            config.arrival_offload_minutes,
            config.departure_work_minutes,
        )
    )


def _valid_staffing_config(config: OptimizerConfig) -> bool:
    values = (
        config.minimum_staff,
        config.normal_preferred_staff,
        config.heavy_preferred_staff,
    )
    return (
        all(_is_integer(value) and value > 0 for value in values)
        and config.minimum_staff <= config.normal_preferred_staff
        and config.normal_preferred_staff <= config.heavy_preferred_staff
    )
