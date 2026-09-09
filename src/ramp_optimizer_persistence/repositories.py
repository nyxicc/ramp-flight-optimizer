"""Transaction-neutral repositories for immutable snapshots and results."""

from dataclasses import dataclass, fields
from datetime import date, datetime, timezone
import json
from typing import Callable
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from ramp_optimizer import (
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    OperationalDay,
    OperationalRole,
    OptimizerConfig,
    Qualification,
)
from ramp_optimizer_persistence.errors import (
    PersistenceIntegrityError,
    ResourceNotFoundError,
)
from ramp_optimizer_persistence.models import (
    EmployeeRow,
    FixedAssignmentRow,
    FlightRow,
    OperationalDayRow,
    OptimizationRunRow,
    ShiftRow,
)
from ramp_optimizer_persistence.serialization import (
    INPUT_SCHEMA_VERSION,
    RESULT_SCHEMA_VERSION,
    canonical_input_hash,
    config_snapshot_json,
    optimization_result_json,
)


IdProvider = Callable[[], str]
Clock = Callable[[], datetime]


@dataclass(frozen=True, slots=True)
class OperationalDaySummaryRecord:
    id: str
    operational_date: date
    created_at_utc: datetime
    input_schema_version: int
    input_hash: str
    employee_count: int
    shift_count: int
    flight_count: int
    fixed_assignment_count: int


@dataclass(frozen=True, slots=True)
class OperationalDayRecord(OperationalDaySummaryRecord):
    day: OperationalDay
    config: OptimizerConfig


@dataclass(frozen=True, slots=True)
class OptimizationRunSummaryRecord:
    id: str
    operational_day_id: str
    created_at_utc: datetime
    package_version: str
    api_version: str
    result_schema_version: int
    input_hash: str
    solver_status: str
    operational_readiness: str
    emergency_pass_disposition: str
    solver_runtime_seconds: float
    attempt_count: int
    objective_stage_count: int
    warning_count: int


@dataclass(frozen=True, slots=True)
class OptimizationRunRecord(OptimizationRunSummaryRecord):
    result: dict[str, object]


def create_operational_day(
    session: Session,
    day: OperationalDay,
    config: OptimizerConfig,
    *,
    id_provider: IdProvider | None = None,
    clock: Clock | None = None,
) -> OperationalDayRecord:
    """Stage one complete snapshot; the caller owns commit and rollback."""

    resource_id = (id_provider or _new_id)()
    created_at = _utc_now(clock)
    input_hash = canonical_input_hash(day, config)
    row = OperationalDayRow(
        id=resource_id,
        operational_date=day.operational_date,
        created_at_utc=_datetime_text(created_at),
        input_schema_version=INPUT_SCHEMA_VERSION,
        optimizer_config_json=config_snapshot_json(config),
        input_hash=input_hash,
        employee_count=len(day.employees),
        shift_count=len(day.employee_shifts),
        flight_count=len(day.flights),
        fixed_assignment_count=len(day.fixed_assignments),
    )
    row.employees = [
        EmployeeRow(
            ordinal=ordinal,
            employee_id=employee.employee_id,
            name=employee.name,
            enabled=employee.enabled,
            qualifications_json=json.dumps(
                sorted(item.value for item in employee.qualifications),
                separators=(",", ":"),
            ),
        )
        for ordinal, employee in enumerate(day.employees)
    ]
    row.shifts = [
        ShiftRow(
            ordinal=ordinal,
            employee_id=shift.employee_id,
            start_iso=shift.start.isoformat(),
            end_iso=shift.end.isoformat(),
            normalized_role=shift.normalized_role.value,
        )
        for ordinal, shift in enumerate(day.employee_shifts)
    ]
    flight_rows = [
        FlightRow(
            ordinal=ordinal,
            arrival_flight_number=flight.arrival_flight_number,
            arrival_time_iso=_optional_datetime_text(flight.arrival_time),
            departure_flight_number=flight.departure_flight_number,
            departure_time_iso=_optional_datetime_text(flight.departure_time),
            gate=flight.gate,
            heavy=flight.heavy,
        )
        for ordinal, flight in enumerate(day.flights)
    ]
    row.flights = flight_rows
    row.fixed_assignments = [
        FixedAssignmentRow(
            ordinal=ordinal,
            employee_id=assignment.employee_id,
            flight=flight_rows[_flight_index(day, assignment.flight)],
        )
        for ordinal, assignment in enumerate(day.fixed_assignments)
    ]
    session.add(row)
    session.flush()
    return _operational_day_record(row)


def get_operational_day(session: Session, resource_id: str) -> OperationalDayRecord:
    row = session.scalar(
        select(OperationalDayRow)
        .where(OperationalDayRow.id == resource_id)
        .options(
            selectinload(OperationalDayRow.employees),
            selectinload(OperationalDayRow.shifts),
            selectinload(OperationalDayRow.flights),
            selectinload(OperationalDayRow.fixed_assignments).selectinload(
                FixedAssignmentRow.flight
            ),
        )
    )
    if row is None:
        raise ResourceNotFoundError("Operational day was not found.")
    return _operational_day_record(row)


def list_operational_days(
    session: Session,
    *,
    limit: int,
    offset: int,
    operational_date: date | None = None,
) -> tuple[tuple[OperationalDaySummaryRecord, ...], int]:
    condition = (
        OperationalDayRow.operational_date == operational_date
        if operational_date is not None
        else None
    )
    count_statement = select(func.count()).select_from(OperationalDayRow)
    statement = select(OperationalDayRow)
    if condition is not None:
        count_statement = count_statement.where(condition)
        statement = statement.where(condition)
    total = int(session.scalar(count_statement) or 0)
    rows = session.scalars(
        statement.order_by(
            OperationalDayRow.created_at_utc.desc(),
            OperationalDayRow.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return tuple(_operational_day_summary(row) for row in rows), total


def create_optimization_run(
    session: Session,
    operational_day: OperationalDayRecord,
    result: dict[str, object],
    *,
    package_version: str,
    api_version: str,
    id_provider: IdProvider | None = None,
    clock: Clock | None = None,
) -> OptimizationRunRecord:
    """Stage a completed immutable result; the caller owns the transaction."""

    expected_hash = canonical_input_hash(operational_day.day, operational_day.config)
    if expected_hash != operational_day.input_hash:
        raise PersistenceIntegrityError("Operational-day snapshot integrity check failed.")
    row = OptimizationRunRow(
        id=(id_provider or _new_id)(),
        operational_day_id=operational_day.id,
        created_at_utc=_datetime_text(_utc_now(clock)),
        package_version=package_version,
        api_version=api_version,
        result_schema_version=RESULT_SCHEMA_VERSION,
        input_hash=operational_day.input_hash,
        solver_status=str(result["status"]),
        operational_readiness=str(result["operational_readiness"]),
        emergency_pass_disposition=str(result["emergency_pass_disposition"]),
        solver_runtime_seconds=float(result["solver_runtime_seconds"]),
        attempt_count=len(result["attempts"]),
        objective_stage_count=len(result["objective_values"]),
        warning_count=len(result["warnings"]),
        result_json=optimization_result_json(result),
    )
    session.add(row)
    session.flush()
    return _optimization_run_record(row)


def get_optimization_run(session: Session, resource_id: str) -> OptimizationRunRecord:
    row = session.get(OptimizationRunRow, resource_id)
    if row is None:
        raise ResourceNotFoundError("Optimization run was not found.")
    day = get_operational_day(session, row.operational_day_id)
    if row.input_hash != day.input_hash:
        raise PersistenceIntegrityError("Optimization-run input linkage is invalid.")
    return _optimization_run_record(row)


def list_optimization_runs_for_day(
    session: Session,
    operational_day_id: str,
    *,
    limit: int,
    offset: int,
) -> tuple[tuple[OptimizationRunSummaryRecord, ...], int]:
    condition = OptimizationRunRow.operational_day_id == operational_day_id
    total = int(
        session.scalar(
            select(func.count()).select_from(OptimizationRunRow).where(condition)
        )
        or 0
    )
    rows = session.scalars(
        select(OptimizationRunRow)
        .where(condition)
        .order_by(
            OptimizationRunRow.created_at_utc.desc(),
            OptimizationRunRow.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    ).all()
    return tuple(_optimization_run_summary(row) for row in rows), total


def _operational_day_record(row: OperationalDayRow) -> OperationalDayRecord:
    employees = tuple(
        Employee(
            employee_id=item.employee_id,
            name=item.name,
            qualifications=frozenset(
                Qualification(value) for value in json.loads(item.qualifications_json)
            ),
            enabled=item.enabled,
        )
        for item in sorted(row.employees, key=lambda value: value.ordinal)
    )
    shifts = tuple(
        EmployeeShift(
            employee_id=item.employee_id,
            start=datetime.fromisoformat(item.start_iso),
            end=datetime.fromisoformat(item.end_iso),
            normalized_role=OperationalRole(item.normalized_role),
        )
        for item in sorted(row.shifts, key=lambda value: value.ordinal)
    )
    ordered_flight_rows = sorted(row.flights, key=lambda value: value.ordinal)
    flights = tuple(_flight_from_row(item) for item in ordered_flight_rows)
    flight_by_id = {
        item.id: flight for item, flight in zip(ordered_flight_rows, flights, strict=True)
    }
    fixed_assignments = tuple(
        FixedAssignment(item.employee_id, flight_by_id[item.flight_id])
        for item in sorted(row.fixed_assignments, key=lambda value: value.ordinal)
    )
    config = OptimizerConfig(**json.loads(row.optimizer_config_json))
    day = OperationalDay(
        operational_date=row.operational_date,
        employees=employees,
        employee_shifts=shifts,
        flights=flights,
        fixed_assignments=fixed_assignments,
    )
    actual_hash = canonical_input_hash(day, config)
    counts = (
        len(employees),
        len(shifts),
        len(flights),
        len(fixed_assignments),
    )
    stored_counts = (
        row.employee_count,
        row.shift_count,
        row.flight_count,
        row.fixed_assignment_count,
    )
    if actual_hash != row.input_hash or counts != stored_counts:
        raise PersistenceIntegrityError("Operational-day snapshot integrity check failed.")
    summary = _operational_day_summary(row)
    return OperationalDayRecord(
        **_record_values(summary),
        day=day,
        config=config,
    )


def _operational_day_summary(row: OperationalDayRow) -> OperationalDaySummaryRecord:
    return OperationalDaySummaryRecord(
        id=row.id,
        operational_date=row.operational_date,
        created_at_utc=datetime.fromisoformat(row.created_at_utc),
        input_schema_version=row.input_schema_version,
        input_hash=row.input_hash,
        employee_count=row.employee_count,
        shift_count=row.shift_count,
        flight_count=row.flight_count,
        fixed_assignment_count=row.fixed_assignment_count,
    )


def _optimization_run_record(row: OptimizationRunRow) -> OptimizationRunRecord:
    try:
        result = json.loads(row.result_json)
    except (TypeError, ValueError) as error:
        raise PersistenceIntegrityError("Stored optimization result is invalid.") from error
    summary = _optimization_run_summary(row)
    if (
        not isinstance(result, dict)
        or result.get("status") != row.solver_status
        or result.get("operational_readiness") != row.operational_readiness
        or result.get("emergency_pass_disposition") != row.emergency_pass_disposition
        or result.get("solver_runtime_seconds") != row.solver_runtime_seconds
        or len(result.get("attempts", [])) != row.attempt_count
        or len(result.get("objective_values", [])) != row.objective_stage_count
        or len(result.get("warnings", [])) != row.warning_count
    ):
        raise PersistenceIntegrityError("Stored optimization result metadata is invalid.")
    return OptimizationRunRecord(**_record_values(summary), result=result)


def _optimization_run_summary(row: OptimizationRunRow) -> OptimizationRunSummaryRecord:
    return OptimizationRunSummaryRecord(
        id=row.id,
        operational_day_id=row.operational_day_id,
        created_at_utc=datetime.fromisoformat(row.created_at_utc),
        package_version=row.package_version,
        api_version=row.api_version,
        result_schema_version=row.result_schema_version,
        input_hash=row.input_hash,
        solver_status=row.solver_status,
        operational_readiness=row.operational_readiness,
        emergency_pass_disposition=row.emergency_pass_disposition,
        solver_runtime_seconds=row.solver_runtime_seconds,
        attempt_count=row.attempt_count,
        objective_stage_count=row.objective_stage_count,
        warning_count=row.warning_count,
    )


def _flight_from_row(row: FlightRow) -> Flight:
    return Flight(
        arrival_flight_number=row.arrival_flight_number,
        arrival_time=_parse_optional_datetime(row.arrival_time_iso),
        departure_flight_number=row.departure_flight_number,
        departure_time=_parse_optional_datetime(row.departure_time_iso),
        gate=row.gate,
        heavy=row.heavy,
    )


def _flight_index(day: OperationalDay, target: Flight) -> int:
    for index, flight in enumerate(day.flights):
        if flight is target or flight == target:
            return index
    raise PersistenceIntegrityError("Fixed assignment does not reference a day flight.")


def _new_id() -> str:
    return str(uuid4())


def _utc_now(clock: Clock | None) -> datetime:
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Clock provider must return an aware datetime.")
    return value.astimezone(timezone.utc)


def _datetime_text(value: datetime) -> str:
    return value.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _optional_datetime_text(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_optional_datetime(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value is not None else None


def _record_values(record: object) -> dict[str, object]:
    return {item.name: getattr(record, item.name) for item in fields(record)}
