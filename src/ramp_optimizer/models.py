"""Solver-independent input and result models."""

from dataclasses import dataclass
from datetime import date, datetime

from ramp_optimizer.enums import (
    BreakStatus,
    FlightType,
    IssueSeverity,
    OperationalRole,
    OptimizationStatus,
    Qualification,
    StaffingStatus,
    WarningCode,
    WarningSeverity,
)


@dataclass(frozen=True, slots=True)
class Employee:
    """Stable employee identity and authoritative qualification data."""

    employee_id: str
    name: str
    qualifications: frozenset[Qualification] = frozenset()
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class EmployeeShift:
    """One bounded availability interval imported for an employee."""

    employee_id: str
    start: datetime
    end: datetime
    source_position: str
    normalized_role: OperationalRole
    source_row: int | None = None
    notes_present: bool = False
    swapboard: bool | None = None


@dataclass(frozen=True, slots=True)
class VacancyRecord:
    """A vacant schedule row that must never become a fictional employee."""

    source_row: int
    source_position: str | None
    start: datetime | None = None
    end: datetime | None = None


@dataclass(frozen=True, slots=True)
class Flight:
    """A flight whose type and work window will be derived from current times."""

    flight_number: str
    arrival_time: datetime | None = None
    departure_time: datetime | None = None
    heavy: bool = False


@dataclass(frozen=True, slots=True)
class OperationalDay:
    """Complete input for one operational-day optimization run."""

    operational_date: date
    employees: tuple[Employee, ...] = ()
    employee_shifts: tuple[EmployeeShift, ...] = ()
    flights: tuple[Flight, ...] = ()


@dataclass(frozen=True, slots=True)
class ImportIssue:
    """One privacy-conscious structural or row-level import problem."""

    severity: IssueSeverity
    code: str
    message: str
    source_row: int | None = None
    column: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduleImportResult:
    """Usable imports plus all issues discovered in the workbook."""

    shifts: tuple[EmployeeShift, ...] = ()
    vacancies: tuple[VacancyRecord, ...] = ()
    issues: tuple[ImportIssue, ...] = ()

    @property
    def has_fatal_issues(self) -> bool:
        return any(issue.severity is IssueSeverity.FATAL for issue in self.issues)

    @property
    def has_errors(self) -> bool:
        return any(
            issue.severity in {IssueSeverity.ERROR, IssueSeverity.FATAL}
            for issue in self.issues
        )


@dataclass(frozen=True, slots=True)
class ScheduleWarning:
    """Structured warning suitable for CLI and future API reporting."""

    code: WarningCode
    severity: WarningSeverity
    message: str
    flight_number: str | None = None
    employee_id: str | None = None


@dataclass(frozen=True, slots=True)
class FlightAssignmentResult:
    """Solved assignment and compliance facts for one flight."""

    flight_number: str
    flight_type: FlightType
    work_start: datetime
    work_end: datetime
    assigned_employee_ids: tuple[str, ...]
    staffing_count: int
    minimum_staff: int
    preferred_staff: int
    maximum_staff: int
    staffing_status: StaffingStatus
    push_covered: bool | None
    close_covered: bool | None
    express: bool
    heavy: bool
    warnings: tuple[ScheduleWarning, ...] = ()


@dataclass(frozen=True, slots=True)
class EmployeeScheduleResult:
    """Chronological assignments and transparent workload facts for an employee."""

    employee_id: str
    assigned_flight_numbers: tuple[str, ...]
    flight_count: int
    mainline_flight_count: int
    express_flight_count: int
    three_person_flight_count: int
    longest_consecutive_streak: int
    break_status: BreakStatus
    adjusted_workload: float


@dataclass(frozen=True, slots=True)
class FairnessMetrics:
    """Understandable schedule-wide fairness measurements."""

    participating_employee_count: int
    total_assignments: int
    average_flights: float
    highest_flight_count: int
    lowest_flight_count: int
    flight_count_spread: int
    maximum_consecutive_streak: int
    adjusted_workload_spread: float


@dataclass(frozen=True, slots=True)
class ObjectiveValue:
    """One named lexicographic objective and its fixed optimum value."""

    stage: int
    name: str
    value: int


@dataclass(frozen=True, slots=True)
class OptimizationAttemptSummary:
    """Audit summary for the Ramp-Agent-only or emergency-Lead attempt."""

    included_leads: bool
    status: OptimizationStatus
    minimum_staffed_flights: int
    qualification_compliant_flights: int
    lead_assignments: int = 0


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """Complete result returned by a future optimization run."""

    status: OptimizationStatus
    flight_results: tuple[FlightAssignmentResult, ...]
    employee_results: tuple[EmployeeScheduleResult, ...]
    fairness_metrics: FairnessMetrics
    attempts: tuple[OptimizationAttemptSummary, ...]
    objective_values: tuple[ObjectiveValue, ...]
    warnings: tuple[ScheduleWarning, ...] = ()
    emergency_lead_staffing_used: bool = False
    solver_runtime_seconds: float = 0.0
