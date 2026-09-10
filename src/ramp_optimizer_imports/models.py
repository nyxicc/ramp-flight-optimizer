"""Immutable import records and adapter/repository extension contracts."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Protocol

from ramp_optimizer.config import OptimizerConfig, TeamWorkImportConfig
from ramp_optimizer.enums import IssueSeverity, OperationalRole, Qualification
from ramp_optimizer.models import Employee, OperationalDay, ScheduleReviewRow
from ramp_optimizer_imports.enums import ImportStatus, ImportType
from ramp_optimizer_imports.flight_models import FlightCorrection, FlightReviewRow, FlightTimePolicy


@dataclass(frozen=True, slots=True)
class ReviewIssue:
    code: str
    severity: IssueSeverity
    message: str
    source_row: int | None = None
    field: str | None = None
    blocks_confirmation: bool = True
    remediation: str | None = None


class ImportError(ValueError):
    """Only stable, sanitized application errors cross infrastructure boundaries."""

    def __init__(self, code: str, status_code: int = 422, issues: tuple[ReviewIssue, ...] = ()):
        self.code = code
        self.status_code = status_code
        self.issues = issues
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ReviewRow:
    row_id: str
    values: ScheduleReviewRow


@dataclass(frozen=True, slots=True)
class RowCorrection:
    row_id: str
    employee_id: str | None = None
    start: datetime | None = None
    end: datetime | None = None
    normalized_role: OperationalRole | None = None
    excluded: bool | None = None
    vacancy: bool | None = None
    enabled: bool | None = None
    qualifications: frozenset[Qualification] | None = None


@dataclass(frozen=True, slots=True)
class ImportPreview:
    revision: int
    operational_date: date
    roster: tuple[Employee, ...]
    employees: tuple[Employee, ...]
    rows: tuple[ReviewRow, ...]
    source_issues: tuple[ReviewIssue, ...]
    issues: tuple[ReviewIssue, ...]
    config: OptimizerConfig
    import_config: TeamWorkImportConfig
    corrections: tuple[RowCorrection, ...] = ()
    flight_rows: tuple[FlightReviewRow, ...] = ()
    flight_policy: FlightTimePolicy | None = None
    flight_corrections: tuple[FlightCorrection, ...] = ()
    detected_date: date | None = None
    operational_date_correction: tuple[date, date] | None = None

    @property
    def confirmation_eligible(self) -> bool:
        return not any(issue.blocks_confirmation for issue in self.issues)

    @property
    def status(self) -> ImportStatus:
        if any(issue.severity == IssueSeverity.FATAL for issue in self.issues):
            return ImportStatus.REJECTED
        return (
            ImportStatus.READY_TO_CONFIRM
            if self.confirmation_eligible
            else ImportStatus.REVIEW_REQUIRED
        )


@dataclass(frozen=True, slots=True)
class ImportRecord:
    import_id: str
    import_type: ImportType
    status: ImportStatus
    original_filename: str
    media_type: str
    byte_size: int
    sha256: str
    schema_version: int
    created_at: datetime
    updated_at: datetime
    preview: ImportPreview
    confirmed_at: datetime | None = None
    confirmed_operational_day_id: str | None = None


class ImportAdapter(Protocol):
    """An explicit adapter seam; no assumptions about a future flight format."""

    import_type: ImportType
    schema_version: int

    def parse(
        self,
        content: bytes,
        import_id: str,
        operational_date: date,
        roster: tuple[Employee, ...],
        config: OptimizerConfig,
        *,
        ramp_agents_only: bool = False,
    ) -> ImportPreview: ...
    def revalidate(self, preview: ImportPreview) -> ImportPreview: ...
    def correct(
        self, preview: ImportPreview, corrections: tuple[RowCorrection, ...]
    ) -> ImportPreview: ...
    def snapshot(self, preview: ImportPreview) -> OperationalDay: ...


class ImportRepository(Protocol):
    """One transaction. Implementations must serialize claim before reading."""

    def get(self, import_id: str) -> ImportRecord: ...
    def get_revision(self, import_id: str, revision: int) -> ImportRecord: ...
    def claim_confirmed(self, import_id: str) -> ImportRecord: ...
    def claim(self, import_id: str, revision: int) -> ImportRecord: ...
    def create(self, record: ImportRecord) -> None: ...
    def revise(self, record: ImportRecord) -> None: ...
    def confirm(self, record: ImportRecord) -> None: ...
    def create_day(self, day: OperationalDay, config: OptimizerConfig) -> str: ...
    def composition(self, employee_id: str, flight_id: str) -> str | None: ...
    def compose(
        self, employee_id: str, flight_id: str, day: OperationalDay, config: OptimizerConfig
    ) -> str: ...
    def readiness(self, day_id: str) -> dict[str, str | bool]: ...
