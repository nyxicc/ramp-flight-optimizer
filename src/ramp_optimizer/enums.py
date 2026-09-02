"""Shared enumerations for optimizer inputs and results."""

from enum import StrEnum


class Qualification(StrEnum):
    """Authoritative qualifications supplied independently of schedule roles."""

    PUSH = "PUSH"
    CLOSE_OUT = "CLOSE_OUT"


class OperationalRole(StrEnum):
    """Normalized operational meaning of a source schedule position."""

    RAMP_AGENT = "RAMP_AGENT"
    RAMP_LEAD = "RAMP_LEAD"
    POSSIBLE_RAMP_SUPPORT = "POSSIBLE_RAMP_SUPPORT"
    TRAINEE = "TRAINEE"
    NON_RAMP = "NON_RAMP"
    UNKNOWN = "UNKNOWN"


class FlightType(StrEnum):
    """Flight types inferred from the supplied arrival and departure times."""

    ARRIVAL_ONLY = "ARRIVAL_ONLY"
    DEPARTURE_ONLY = "DEPARTURE_ONLY"
    TURN = "TURN"


class BreakStatus(StrEnum):
    """Outcome of the bracketed between-assignment break evaluation."""

    NOT_APPLICABLE = "NOT_APPLICABLE"
    NOT_EVALUABLE_BETWEEN_ASSIGNMENTS = "NOT_EVALUABLE_BETWEEN_ASSIGNMENTS"
    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"


class OptimizationStatus(StrEnum):
    """High-level status exposed without leaking solver-specific constants."""

    OPTIMAL = "OPTIMAL"
    FEASIBLE = "FEASIBLE"
    INFEASIBLE = "INFEASIBLE"
    UNKNOWN = "UNKNOWN"


class StaffingStatus(StrEnum):
    """Operational staffing status for one flight."""

    PREFERRED_STAFFED = "PREFERRED_STAFFED"
    MINIMUM_STAFFED = "MINIMUM_STAFFED"
    BELOW_MINIMUM = "BELOW_MINIMUM"


class WarningSeverity(StrEnum):
    """Severity of a supervisor-facing schedule warning."""

    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class WarningCode(StrEnum):
    """Stable warning identifiers for reporting and future API clients."""

    MINIMUM_STAFFING_NOT_MET = "MINIMUM_STAFFING_NOT_MET"
    PUSH_QUALIFICATION_NOT_MET = "PUSH_QUALIFICATION_NOT_MET"
    CLOSE_QUALIFICATION_NOT_MET = "CLOSE_QUALIFICATION_NOT_MET"
    REQUIRED_BREAK_NOT_MET = "REQUIRED_BREAK_NOT_MET"
    EMERGENCY_LEAD_USED = "EMERGENCY_LEAD_USED"
    MANUAL_INTERVENTION_REQUIRED = "MANUAL_INTERVENTION_REQUIRED"


class IssueSeverity(StrEnum):
    """Severity for structured spreadsheet-import issues."""

    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"
