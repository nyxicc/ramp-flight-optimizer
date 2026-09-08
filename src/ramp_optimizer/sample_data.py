"""Deterministic, timezone-aware fictional scenarios for demos and benchmarks."""

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta, timezone

from ramp_optimizer.config import OptimizerConfig
from ramp_optimizer.enums import OperationalRole, Qualification
from ramp_optimizer.models import (
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    OperationalDay,
)


SAMPLE_DATE = date(2035, 4, 15)
SAMPLE_TIMEZONE = timezone(timedelta(hours=-5), name="UTC-05:00")
SAMPLE_SCENARIO_NAMES = ("normal", "shortage", "emergency-lead")


@dataclass(frozen=True, slots=True)
class SampleScenario:
    """One named fictional optimizer input and its explicit configuration."""

    name: str
    description: str
    day: OperationalDay
    config: OptimizerConfig


def build_sample_scenario(
    name: str,
    *,
    solver_time_limit_seconds: float | None = None,
) -> SampleScenario:
    """Build a named public scenario without shared mutable state."""

    builders = {
        "normal": build_normal_scenario,
        "shortage": build_staffing_shortage_scenario,
        "emergency-lead": build_emergency_lead_scenario,
    }
    try:
        builder = builders[name]
    except KeyError as error:
        allowed = ", ".join(SAMPLE_SCENARIO_NAMES)
        raise ValueError(f"unknown sample scenario {name!r}; choose from {allowed}") from error
    return builder(solver_time_limit_seconds=solver_time_limit_seconds)


def build_normal_scenario(
    *,
    solver_time_limit_seconds: float | None = None,
) -> SampleScenario:
    """Return a fictional 24-movement day that is ready without Lead recovery."""

    q = Qualification
    employees = (
        _employee("A001", q.PUSH, q.CLOSE_OUT),
        _employee("A002", q.PUSH),
        _employee("A003", q.CLOSE_OUT),
        _employee("A004"),
        _employee("A005", q.PUSH, q.CLOSE_OUT),
        _employee("A006", q.PUSH, q.CLOSE_OUT),
        _employee("A007", q.PUSH, q.CLOSE_OUT),
        _employee("A008", q.PUSH),
        _employee("A009", q.CLOSE_OUT),
        _employee("A010", q.PUSH, q.CLOSE_OUT),
        _employee("A011"),
        _employee("A012", q.PUSH, q.CLOSE_OUT),
        _employee("A013", q.CLOSE_OUT),
        _employee("A014", q.PUSH, q.CLOSE_OUT),
        _employee("A015", q.PUSH),
        _employee("A016", q.CLOSE_OUT),
        _employee("A099", enabled=False),
        _employee("L001", q.PUSH, q.CLOSE_OUT, label="Fictional Lead"),
        _employee("L002", q.PUSH, q.CLOSE_OUT, label="Fictional Lead"),
        _employee("T001", label="Fictional Trainee"),
        _employee("N001", label="Fictional Non-Ramp Employee"),
    )
    shifts = (
        _shift("A001", 4, 14),
        _shift("A002", 4, 12),
        _shift("A003", 5, 13),
        _shift("A004", 5, 9),
        _shift("A005", 4, 14),
        _shift("A006", 4, 12),
        _shift("A007", 8, 18),
        _shift("A008", 14, 24),
        _shift("A009", 14, 24),
        _shift("A010", 14, 22),
        _shift("A011", 14, 20),
        _shift("A012", 20, 28),
        _shift("A013", 6, 10),
        _shift("A013", 12, 16),
        _shift("A014", 16, 24),
        _shift("A015", 18, 24),
        _shift("A016", 20, 28),
        _shift("A099", 4, 14),
        _shift("L001", 4, 14, OperationalRole.RAMP_LEAD),
        _shift("L002", 20, 28, OperationalRole.RAMP_LEAD),
        _shift("T001", 4, 14, OperationalRole.TRAINEE),
        _shift("N001", 4, 14, OperationalRole.NON_RAMP),
    )
    flights = (
        _departure("SYN101", 5, 30, gate="D01"),
        _arrival("SYN3101", 5, 10, gate="D02"),
        _turn("SYN102", 6, 0, "SYN202", 7, 0, gate="D03"),
        _departure("SYN3102", 6, 30, gate="D04"),
        _arrival("SYN103", 7, 20, gate="D05"),
        _departure("SYN203", 8, 0, gate="D06"),
        _turn(
            "SYN3103", 8, 20, "SYN3203", 9, 20, gate="D07", heavy=True
        ),
        _arrival("SYN104", 9, 0, gate="D08"),
        _departure("SYN204", 10, 30, gate="D09"),
        _arrival("SYN3105", 10, 10, gate="D10"),
        _turn("SYN105", 11, 30, "SYN205", 12, 30, gate="D11", heavy=True),
        _departure("SYN3106", 12, 0, gate="D12"),
        _arrival("SYN106", 14, 30, gate="D13"),
        _departure("SYN206", 15, 20, gate="D14"),
        _turn(
            "SYN3107", 16, 10, "SYN3207", 17, 10, gate="D15", heavy=True
        ),
        _arrival("SYN107", 16, 50, gate="D16"),
        _departure("SYN207", 18, 30, gate="D17"),
        _arrival("SYN3109", 18, 10, gate="D18"),
        _turn("SYN108", 19, 0, "SYN208", 20, 0, gate="D19", heavy=True),
        _departure("SYN3110", 19, 30, gate="D20"),
        _arrival("SYN109", 21, 0, gate="D21"),
        _departure("SYN209", 21, 30, gate="D22"),
        _turn("SYN3111", 22, 30, "SYN3211", 23, 30, gate="D23"),
        _arrival("SYN110", 23, 10, gate="D24"),
    )
    minimum_crews = (
        ("A001", "A002", "A005"),
        ("A003", "A004", "A006"),
        ("A001", "A003", "A005"),
        ("A002", "A004", "A006"),
        ("A001", "A003", "A013"),
        ("A002", "A005", "A006"),
        ("A001", "A002", "A003"),
        ("A005", "A006", "A007"),
        ("A001", "A002", "A003"),
        ("A005", "A006", "A007"),
        ("A001", "A003", "A005"),
        ("A002", "A006", "A007"),
        ("A007", "A008", "A013"),
        ("A009", "A010", "A011"),
        ("A007", "A008", "A010"),
        ("A009", "A011", "A014"),
        ("A008", "A009", "A010"),
        ("A011", "A014", "A015"),
        ("A008", "A009", "A010"),
        ("A011", "A014", "A015"),
        ("A008", "A009", "A012"),
        ("A010", "A014", "A015"),
        ("A008", "A012", "A014"),
        ("A009", "A015", "A016"),
    )
    fixed = tuple(
        FixedAssignment(employee_id, flight)
        for flight, crew in zip(flights, minimum_crews, strict=True)
        for employee_id in crew
    )
    config = _config_with_time_limit(
        OptimizerConfig(), solver_time_limit_seconds
    )
    return SampleScenario(
        name="normal",
        description=(
            "Fictional ready 24-movement day with overlapping work, mixed service "
            "classes, heavy turns, qualifications, breaks, fairness, and continuity."
        ),
        day=OperationalDay(
            SAMPLE_DATE,
            employees=employees,
            employee_shifts=shifts,
            flights=flights,
            fixed_assignments=fixed,
        ),
        config=config,
    )


def build_staffing_shortage_scenario(
    *,
    solver_time_limit_seconds: float | None = None,
) -> SampleScenario:
    """Return a valid fictional day whose single flight remains understaffed."""

    employees = (_employee("S001"), _employee("S002"))
    target = _arrival("SYN151", 9, 0, gate="S01")
    config = _config_with_time_limit(
        OptimizerConfig(), solver_time_limit_seconds
    )
    return SampleScenario(
        name="shortage",
        description=(
            "Fictional valid day with two available Agents for a three-person "
            "minimum, demonstrating a usable partial schedule and warnings."
        ),
        day=OperationalDay(
            SAMPLE_DATE,
            employees=employees,
            employee_shifts=tuple(_shift(item.employee_id, 7, 11) for item in employees),
            flights=(target,),
        ),
        config=config,
    )


def build_emergency_lead_scenario(
    *,
    solver_time_limit_seconds: float | None = None,
) -> SampleScenario:
    """Return a fictional shortage that one explicitly enabled Lead can recover."""

    employees = (
        _employee("E001"),
        _employee("E002"),
        _employee("EL01", Qualification.PUSH, Qualification.CLOSE_OUT, label="Fictional Lead"),
    )
    target = _arrival("SYN171", 9, 0, gate="E01")
    base = replace(OptimizerConfig(), allow_leads_for_minimum_staffing=True)
    config = _config_with_time_limit(base, solver_time_limit_seconds)
    return SampleScenario(
        name="emergency-lead",
        description=(
            "Fictional day where the Agent-only pass is short and an explicitly "
            "enabled Lead supplies the missing minimum-staffing position."
        ),
        day=OperationalDay(
            SAMPLE_DATE,
            employees=employees,
            employee_shifts=(
                _shift("E001", 7, 11),
                _shift("E002", 7, 11),
                _shift("EL01", 7, 11, OperationalRole.RAMP_LEAD),
            ),
            flights=(target,),
        ),
        config=config,
    )


def _config_with_time_limit(
    config: OptimizerConfig,
    solver_time_limit_seconds: float | None,
) -> OptimizerConfig:
    return (
        config
        if solver_time_limit_seconds is None
        else replace(
            config,
            solver_time_limit_seconds=solver_time_limit_seconds,
        )
    )


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(
        SAMPLE_DATE,
        datetime.min.time(),
        tzinfo=SAMPLE_TIMEZONE,
    ) + timedelta(hours=hour, minutes=minute)


def _employee(
    employee_id: str,
    *qualifications: Qualification,
    enabled: bool = True,
    label: str = "Fictional Agent",
) -> Employee:
    return Employee(
        employee_id=employee_id,
        name=f"{label} {employee_id}",
        qualifications=frozenset(qualifications),
        enabled=enabled,
    )


def _shift(
    employee_id: str,
    start_hour: int,
    end_hour: int,
    role: OperationalRole = OperationalRole.RAMP_AGENT,
) -> EmployeeShift:
    return EmployeeShift(employee_id, _at(start_hour), _at(end_hour), role)


def _arrival(
    number: str,
    hour: int,
    minute: int,
    *,
    gate: str,
    heavy: bool = False,
) -> Flight:
    return Flight(
        arrival_flight_number=number,
        arrival_time=_at(hour, minute),
        gate=gate,
        heavy=heavy,
    )


def _departure(
    number: str,
    hour: int,
    minute: int,
    *,
    gate: str,
    heavy: bool = False,
) -> Flight:
    return Flight(
        departure_flight_number=number,
        departure_time=_at(hour, minute),
        gate=gate,
        heavy=heavy,
    )


def _turn(
    arrival_number: str,
    arrival_hour: int,
    arrival_minute: int,
    departure_number: str,
    departure_hour: int,
    departure_minute: int,
    *,
    gate: str,
    heavy: bool = False,
) -> Flight:
    return Flight(
        arrival_flight_number=arrival_number,
        arrival_time=_at(arrival_hour, arrival_minute),
        departure_flight_number=departure_number,
        departure_time=_at(departure_hour, departure_minute),
        gate=gate,
        heavy=heavy,
    )
