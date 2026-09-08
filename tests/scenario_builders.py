"""Explicit, entirely fictional scenarios shared by Milestone 14 tests.

These builders create inputs only.  They intentionally do not reproduce any
optimizer calculation, and important scenario choices remain visible in the
canonical builder below.
"""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

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


SYNTHETIC_DATE = date(2026, 9, 2)


@dataclass(frozen=True, slots=True)
class SyntheticScenario:
    """One immutable optimizer input and its explicit configuration."""

    day: OperationalDay
    config: OptimizerConfig


def at(hour: int, minute: int = 0, *, day_offset: int = 0) -> datetime:
    return datetime.combine(SYNTHETIC_DATE, datetime.min.time()) + timedelta(
        days=day_offset, hours=hour, minutes=minute
    )


def synthetic_employee(
    employee_id: str,
    *,
    qualifications: tuple[Qualification, ...] = (),
    enabled: bool = True,
) -> Employee:
    return Employee(
        employee_id=employee_id,
        name=f"Synthetic {employee_id}",
        qualifications=frozenset(qualifications),
        enabled=enabled,
    )


def synthetic_shift(
    employee_id: str,
    *,
    start: datetime,
    end: datetime,
    role: OperationalRole = OperationalRole.RAMP_AGENT,
) -> EmployeeShift:
    return EmployeeShift(employee_id, start, end, role)


def arrival(
    number: str,
    arrival_time: datetime,
    *,
    heavy: bool = False,
    gate: str | None = None,
) -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=arrival_time,
        heavy=heavy,
        gate=gate,
    )


def departure(
    number: str,
    departure_time: datetime,
    *,
    heavy: bool = False,
    gate: str | None = None,
) -> Flight:
    return Flight(
        departure_flight_number=number,
        departure_time=departure_time,
        heavy=heavy,
        gate=gate,
    )


def turn(
    arrival_number: str,
    arrival_time: datetime,
    departure_number: str,
    departure_time: datetime,
    *,
    heavy: bool = False,
    gate: str | None = None,
) -> Flight:
    return Flight(
        arrival_flight_number=arrival_number,
        arrival_time=arrival_time,
        departure_flight_number=departure_number,
        departure_time=departure_time,
        heavy=heavy,
        gate=gate,
    )


def optimizer_config(**changes: object) -> OptimizerConfig:
    return replace(OptimizerConfig(), **changes)


def operational_day(
    *,
    employees: tuple[Employee, ...],
    shifts: tuple[EmployeeShift, ...],
    flights: tuple[Flight, ...],
    fixed: tuple[FixedAssignment, ...] = (),
    operational_date: date = SYNTHETIC_DATE,
) -> OperationalDay:
    return OperationalDay(
        operational_date=operational_date,
        employees=employees,
        employee_shifts=shifts,
        flights=flights,
        fixed_assignments=fixed,
    )


def canonical_full_day(
    *,
    emergency: str = "none",
    solver_time_limit_seconds: float = 3.0,
) -> SyntheticScenario:
    """Return the intentional 24-movement canonical fictional day.

    ``emergency`` may be ``none``, ``recoverable``, or ``insufficient``.
    Minimum crews are fixed to make safety invariants deterministic while the
    solver still chooses preferred additions and all fairness refinements.
    """

    if emergency not in {"none", "recoverable", "insufficient"}:
        raise ValueError("emergency must be none, recoverable, or insufficient")

    q = Qualification
    employees = (
        synthetic_employee("R001", qualifications=(q.PUSH, q.CLOSE_OUT)),
        synthetic_employee("R002", qualifications=(q.PUSH,)),
        synthetic_employee("R003", qualifications=(q.CLOSE_OUT,)),
        synthetic_employee("R004"),
        synthetic_employee("R005", qualifications=(q.PUSH, q.CLOSE_OUT)),
        synthetic_employee("R006", qualifications=(q.PUSH, q.CLOSE_OUT)),
        synthetic_employee("R007", qualifications=(q.PUSH, q.CLOSE_OUT)),
        synthetic_employee("R008", qualifications=(q.PUSH,)),
        synthetic_employee("R009", qualifications=(q.CLOSE_OUT,)),
        synthetic_employee("R010", qualifications=(q.PUSH, q.CLOSE_OUT)),
        synthetic_employee("R011"),
        synthetic_employee("R012", qualifications=(q.PUSH, q.CLOSE_OUT)),
        synthetic_employee("R013", qualifications=(q.CLOSE_OUT,)),
        synthetic_employee("R014", qualifications=(q.PUSH, q.CLOSE_OUT)),
        synthetic_employee("R015", qualifications=(q.PUSH,)),
        synthetic_employee("R016", qualifications=(q.CLOSE_OUT,)),
        synthetic_employee("R099", enabled=False),
        synthetic_employee("L001", qualifications=(q.PUSH, q.CLOSE_OUT)),
        synthetic_employee("L002", qualifications=(q.PUSH, q.CLOSE_OUT)),
        synthetic_employee("T001"),
        synthetic_employee("N001"),
    )
    shifts = (
        synthetic_shift("R001", start=at(4), end=at(14)),
        synthetic_shift("R002", start=at(4), end=at(12)),
        synthetic_shift("R003", start=at(5), end=at(13)),
        synthetic_shift("R004", start=at(5), end=at(9)),
        synthetic_shift("R005", start=at(4), end=at(14)),
        synthetic_shift("R006", start=at(4), end=at(12)),
        synthetic_shift("R007", start=at(8), end=at(18)),
        synthetic_shift("R008", start=at(14), end=at(0, day_offset=1)),
        synthetic_shift("R009", start=at(14), end=at(0, day_offset=1)),
        synthetic_shift("R010", start=at(14), end=at(22)),
        synthetic_shift("R011", start=at(14), end=at(20)),
        synthetic_shift("R012", start=at(20), end=at(4, day_offset=1)),
        synthetic_shift("R013", start=at(6), end=at(10)),
        synthetic_shift("R013", start=at(12), end=at(16)),
        synthetic_shift("R014", start=at(16), end=at(0, day_offset=1)),
        synthetic_shift("R015", start=at(18), end=at(0, day_offset=1)),
        synthetic_shift("R016", start=at(20), end=at(4, day_offset=1)),
        synthetic_shift("R099", start=at(4), end=at(14)),
        synthetic_shift(
            "L001", start=at(4), end=at(14), role=OperationalRole.RAMP_LEAD
        ),
        synthetic_shift(
            "L002",
            start=at(20),
            end=at(4, day_offset=1),
            role=OperationalRole.RAMP_LEAD,
        ),
        synthetic_shift(
            "T001", start=at(4), end=at(14), role=OperationalRole.TRAINEE
        ),
        synthetic_shift(
            "N001", start=at(4), end=at(14), role=OperationalRole.NON_RAMP
        ),
    )
    flights = (
        departure("FX101", at(5, 30), gate="F01"),
        arrival("FX3001", at(5, 10), gate="F02"),
        turn("FX102", at(6), "FX202", at(7), gate="F03"),
        departure("FX3002", at(6, 30), gate="F04"),
        arrival("FX103", at(7, 20), gate="F05"),
        departure("FX203", at(8), gate="F06"),
        turn("FX3003", at(8, 20), "FX3004", at(9, 20), heavy=True),
        arrival("FX104", at(9), gate="F08"),
        departure("FX204", at(10, 30), gate="F09"),
        arrival("FX3005", at(10, 10), gate="F10"),
        turn("FX105", at(11, 30), "FX205", at(12, 30), heavy=True),
        departure("FX3006", at(12), gate="F12"),
        arrival("FX106", at(14, 30), gate="F13"),
        departure("FX206", at(15, 20), gate="F14"),
        turn("FX3007", at(16, 10), "FX3008", at(17, 10), heavy=True),
        arrival("FX107", at(16, 50), gate="F16"),
        departure("FX207", at(18, 30), gate="F17"),
        arrival("FX3009", at(18, 10), gate="F18"),
        turn("FX108", at(19), "FX208", at(20), heavy=True),
        departure("FX3010", at(19, 30), gate="F20"),
        arrival("FX109", at(21), gate="F21"),
        departure("FX209", at(21, 30), gate="F22"),
        turn("FX3011", at(22, 30), "FX3012", at(23, 30), gate="F23"),
        arrival("FX110", at(23, 10), gate="F24"),
    )
    crews = (
        ("R001", "R002", "R005"),
        ("R003", "R004", "R006"),
        ("R001", "R003", "R005"),
        ("R002", "R004", "R006"),
        ("R001", "R003", "R013"),
        ("R002", "R005", "R006"),
        ("R001", "R002", "R003"),
        ("R005", "R006", "R007"),
        ("R001", "R002", "R003"),
        ("R005", "R006", "R007"),
        ("R001", "R003", "R005"),
        ("R002", "R006", "R007"),
        ("R007", "R008", "R013"),
        ("R009", "R010", "R011"),
        ("R007", "R008", "R010"),
        ("R009", "R011", "R014"),
        ("R008", "R009", "R010"),
        ("R011", "R014", "R015"),
        ("R008", "R009", "R010"),
        ("R011", "R014", "R015"),
        ("R008", "R009", "R012"),
        ("R010", "R014", "R015"),
        ("R008", "R012", "R014"),
        ("R009", "R015", "R016"),
    )
    fixed = tuple(
        FixedAssignment(employee_id, flight)
        for flight, crew in zip(flights, crews, strict=True)
        for employee_id in crew
    )

    if emergency == "recoverable":
        recovery = arrival("FX3990", at(2, day_offset=1), gate="F25")
        flights += (recovery,)
        fixed += (
            FixedAssignment("R012", recovery),
            FixedAssignment("R016", recovery),
        )
    elif emergency == "insufficient":
        recovery_arrival = arrival("FX3990", at(2, day_offset=1), gate="F25")
        recovery_departure = departure(
            "FX3991", at(2, 30, day_offset=1), gate="F26"
        )
        flights += (recovery_arrival, recovery_departure)
        fixed += (
            FixedAssignment("R012", recovery_arrival),
            FixedAssignment("R016", recovery_arrival),
        )

    return SyntheticScenario(
        operational_day(
            employees=employees,
            shifts=shifts,
            flights=flights,
            fixed=fixed,
        ),
        optimizer_config(
            allow_leads_for_minimum_staffing=emergency != "none",
            solver_time_limit_seconds=solver_time_limit_seconds,
        ),
    )


def small_ready_scenario(
    *,
    leads_enabled: bool = False,
    solver_time_limit_seconds: float = 2.0,
) -> SyntheticScenario:
    workers = (
        synthetic_employee(
            "R001", qualifications=(Qualification.PUSH, Qualification.CLOSE_OUT)
        ),
        synthetic_employee("R002"),
        synthetic_employee("R003"),
        synthetic_employee(
            "L001", qualifications=(Qualification.PUSH, Qualification.CLOSE_OUT)
        ),
    )
    target = departure("FX101", at(9))
    return SyntheticScenario(
        operational_day(
            employees=workers,
            shifts=(
                *(synthetic_shift(worker.employee_id, start=at(7), end=at(11))
                  for worker in workers[:3]),
                synthetic_shift(
                    "L001",
                    start=at(7),
                    end=at(11),
                    role=OperationalRole.RAMP_LEAD,
                ),
            ),
            flights=(target,),
        ),
        optimizer_config(
            allow_leads_for_minimum_staffing=leads_enabled,
            solver_time_limit_seconds=solver_time_limit_seconds,
        ),
    )


def small_shortage_scenario(
    *,
    leads_enabled: bool,
    lead_available: bool = True,
) -> SyntheticScenario:
    workers = (
        synthetic_employee("R001"),
        synthetic_employee("R002"),
        synthetic_employee(
            "L001", qualifications=(Qualification.PUSH, Qualification.CLOSE_OUT)
        ),
    )
    target = arrival("FX111", at(9))
    return SyntheticScenario(
        operational_day(
            employees=workers,
            shifts=(
                synthetic_shift("R001", start=at(7), end=at(11)),
                synthetic_shift("R002", start=at(7), end=at(11)),
                synthetic_shift(
                    "L001",
                    start=at(7) if lead_available else at(12),
                    end=at(11) if lead_available else at(16),
                    role=OperationalRole.RAMP_LEAD,
                ),
            ),
            flights=(target,),
        ),
        optimizer_config(
            allow_leads_for_minimum_staffing=leads_enabled,
            solver_time_limit_seconds=2.0,
        ),
    )
