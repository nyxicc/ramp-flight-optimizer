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
    """Movement types inferred from consistently populated directional sides."""

    ARRIVAL_ONLY = "ARRIVAL_ONLY"
    DEPARTURE_ONLY = "DEPARTURE_ONLY"
    TURN = "TURN"


class EligibilityReason(StrEnum):
    """Stable explanations for an ineligible employee-flight pair."""

    EMPLOYEE_DISABLED = "EMPLOYEE_DISABLED"
    NO_EMPLOYEE_SHIFT = "NO_EMPLOYEE_SHIFT"
    INELIGIBLE_OPERATIONAL_ROLE = "INELIGIBLE_OPERATIONAL_ROLE"
    OUTSIDE_SHIFT = "OUTSIDE_SHIFT"
    OVERLAPS_FIXED_ASSIGNMENT = "OVERLAPS_FIXED_ASSIGNMENT"


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


class EmergencyStaffingStatus(StrEnum):
    """Operational outcome of the optional emergency-Lead fallback."""

    NORMAL_SCHEDULE = "NORMAL_SCHEDULE"
    LEAD_ASSISTED_SCHEDULE = "LEAD_ASSISTED_SCHEDULE"
    CRITICAL_SHORTAGE_REMAINS = "CRITICAL_SHORTAGE_REMAINS"


class OperationalReadinessStatus(StrEnum):
    """Operational meaning of a returned computational result."""

    READY = "READY"
    READY_WITH_WARNINGS = "READY_WITH_WARNINGS"
    MANUAL_INTERVENTION_REQUIRED = "MANUAL_INTERVENTION_REQUIRED"
    NO_USABLE_SCHEDULE = "NO_USABLE_SCHEDULE"


class EmergencyPassDisposition(StrEnum):
    """Unambiguous disposition of the optional emergency solve."""

    NOT_ENABLED = "NOT_ENABLED"
    NOT_NEEDED = "NOT_NEEDED"
    NOT_ATTEMPTED_NO_USABLE_SCHEDULE = "NOT_ATTEMPTED_NO_USABLE_SCHEDULE"
    ATTEMPTED_AND_ADOPTED = "ATTEMPTED_AND_ADOPTED"
    ATTEMPTED_ADOPTED_WITH_REMAINING_SHORTAGE = (
        "ATTEMPTED_ADOPTED_WITH_REMAINING_SHORTAGE"
    )
    ATTEMPTED_NOT_ADOPTED_UNUSABLE = "ATTEMPTED_NOT_ADOPTED_UNUSABLE"
    ATTEMPTED_NOT_ADOPTED_WORSE = "ATTEMPTED_NOT_ADOPTED_WORSE"
    ATTEMPTED_NOT_ADOPTED_NO_IMPROVEMENT = (
        "ATTEMPTED_NOT_ADOPTED_NO_IMPROVEMENT"
    )


class EmergencyLeadReason(StrEnum):
    """Measurable critical value supplied by one Lead assignment."""

    MINIMUM_STAFFING = "MINIMUM_STAFFING"
    PUSH_QUALIFICATION = "PUSH_QUALIFICATION"
    CLOSE_QUALIFICATION = "CLOSE_QUALIFICATION"


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
    LEAD_STAFFING_REQUIRED = "LEAD_STAFFING_REQUIRED"
    LEAD_QUALIFICATION_REQUIRED = "LEAD_QUALIFICATION_REQUIRED"
    CRITICAL_SHORTAGE_REMAINS = "CRITICAL_SHORTAGE_REMAINS"
    MANUAL_INTERVENTION_REQUIRED = "MANUAL_INTERVENTION_REQUIRED"
    SOLVER_RESULT_NOT_PROVEN_OPTIMAL = "SOLVER_RESULT_NOT_PROVEN_OPTIMAL"
    EMERGENCY_RECOVERY_NOT_ADOPTED = "EMERGENCY_RECOVERY_NOT_ADOPTED"
    NO_USABLE_SCHEDULE = "NO_USABLE_SCHEDULE"


class IssueSeverity(StrEnum):
    """Severity for structured spreadsheet-import issues."""

    WARNING = "WARNING"
    ERROR = "ERROR"
    FATAL = "FATAL"
