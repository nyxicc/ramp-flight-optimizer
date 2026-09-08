"""Fast deterministic boundary and hostile-input matrices."""

from dataclasses import replace
from datetime import date, datetime, timedelta
from math import inf, nan

import pytest

from ramp_optimizer import (
    BreakStatus,
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    InputValidationError,
    OperationalDay,
    OperationalRole,
    OptimizationStatus,
    OptimizerConfig,
    Qualification,
    assess_employee_flight_eligibility,
    derive_work_window,
    intervals_overlap,
    optimize_flight_assignments,
    parse_numeric_flight_number,
    validate_config,
    validate_operational_day,
)
from tests.scenario_builders import (
    SYNTHETIC_DATE,
    arrival,
    at,
    departure,
    operational_day,
    synthetic_employee,
    synthetic_shift,
    turn,
)


def _one_person_config(**changes: object) -> OptimizerConfig:
    return replace(
        OptimizerConfig(),
        minimum_staff=1,
        normal_preferred_staff=1,
        heavy_preferred_staff=1,
        solver_time_limit_seconds=2.0,
        **changes,
    )


def _fixed_employee_day(
    flights: tuple[Flight, ...],
    *,
    shifts: tuple[EmployeeShift, ...] | None = None,
) -> OperationalDay:
    employee = synthetic_employee(
        "R001", qualifications=(Qualification.PUSH, Qualification.CLOSE_OUT)
    )
    return operational_day(
        employees=(employee,),
        shifts=shifts
        or (synthetic_shift("R001", start=at(0), end=at(0, day_offset=2)),),
        flights=flights,
        fixed=tuple(FixedAssignment("R001", flight) for flight in flights),
    )


def test_half_open_touching_assignments_and_one_minute_overlap() -> None:
    first = (at(8), at(9))

    assert not intervals_overlap(*first, at(9), at(10))
    assert intervals_overlap(*first, at(8, 59), at(10))


def test_shift_boundaries_are_inclusive_but_one_minute_overrun_is_ineligible() -> None:
    worker = synthetic_employee("R001")
    target = departure("FX101", at(9))  # [08:00, 09:00)
    exact = synthetic_shift("R001", start=at(8), end=at(9))
    too_short = synthetic_shift("R001", start=at(8), end=at(8, 59))

    assert assess_employee_flight_eligibility(
        worker, (exact,), target, OptimizerConfig()
    ).eligible
    assert not assess_employee_flight_eligibility(
        worker, (too_short,), target, OptimizerConfig()
    ).eligible


@pytest.mark.parametrize(
    ("event", "expected_start_date", "expected_end_date"),
    [
        (datetime(2026, 2, 1, 0, 10), date(2026, 1, 31), date(2026, 2, 1)),
        (datetime(2027, 1, 1, 0, 10), date(2026, 12, 31), date(2027, 1, 1)),
        (datetime(2028, 2, 29, 0, 10), date(2028, 2, 28), date(2028, 2, 29)),
    ],
)
def test_month_year_and_leap_day_arithmetic(
    event: datetime, expected_start_date: date, expected_end_date: date
) -> None:
    start, end = derive_work_window(
        departure("FX101", event), OptimizerConfig()
    )

    assert start.date() == expected_start_date
    assert end.date() == expected_end_date


def test_split_shift_gap_is_not_merged_and_shift_collisions_are_reported() -> None:
    worker = synthetic_employee("R001")
    target = departure("FX101", at(9, 30))  # spans 08:30 through 09:30
    split = (
        synthetic_shift("R001", start=at(8), end=at(9)),
        synthetic_shift("R001", start=at(9, 1), end=at(10)),
    )
    assert not assess_employee_flight_eligibility(
        worker, split, target, OptimizerConfig()
    ).eligible

    duplicate_day = operational_day(
        employees=(worker,), shifts=(split[0], split[0]), flights=()
    )
    overlap_day = operational_day(
        employees=(worker,),
        shifts=(split[0], synthetic_shift("R001", start=at(8, 59), end=at(10))),
        flights=(),
    )
    assert "DUPLICATE_EMPLOYEE_SHIFT" in {
        issue.code for issue in validate_operational_day(duplicate_day)
    }
    assert "OVERLAPPING_EMPLOYEE_SHIFTS" in {
        issue.code for issue in validate_operational_day(overlap_day)
    }


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("FX2999", 2999),
        ("fx03000", 3000),
        ("FX3001", 3001),
        ("00042", 42),
    ],
)
def test_flight_number_boundary_forms(value: str, expected: int) -> None:
    assert parse_numeric_flight_number(value) == expected


def test_directional_uniqueness_and_mixed_turn_rules() -> None:
    opposite_directions = OperationalDay(
        SYNTHETIC_DATE,
        flights=(arrival("FX123", at(8)), departure("fx00123", at(10))),
    )
    duplicate_arrivals = replace(
        opposite_directions,
        flights=(arrival("FX123", at(8)), arrival("fx00123", at(10))),
    )
    mixed_turn = OperationalDay(
        SYNTHETIC_DATE,
        flights=(turn("FX2999", at(8), "FX3001", at(9)),),
    )

    assert validate_operational_day(opposite_directions) == ()
    assert "DUPLICATE_ARRIVAL_FLIGHT_NUMBER" in {
        issue.code for issue in validate_operational_day(duplicate_arrivals)
    }
    assert "MIXED_TURN_SERVICE_CATEGORY" in {
        issue.code for issue in validate_operational_day(mixed_turn)
    }


POSITIVE_INTEGER_FIELDS = (
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


@pytest.mark.parametrize("field_name", POSITIVE_INTEGER_FIELDS)
@pytest.mark.parametrize("invalid_value", [0, -1, True])
def test_every_positive_integer_field_rejects_zero_negative_and_boolean(
    field_name: str, invalid_value: object
) -> None:
    issues = validate_config(replace(OptimizerConfig(), **{field_name: invalid_value}))

    assert any(issue.path == f"config.{field_name}" for issue in issues)


def _valid_positive_config(field_name: str, value: int) -> OptimizerConfig:
    changes: dict[str, object] = {field_name: value}
    if field_name == "minimum_staff":
        changes.update(
            normal_preferred_staff=max(value, 4),
            heavy_preferred_staff=max(value, 5),
        )
    elif field_name == "normal_preferred_staff":
        changes.update(
            minimum_staff=min(value, 3),
            heavy_preferred_staff=max(value, 5),
        )
    elif field_name == "heavy_preferred_staff":
        changes.update(
            minimum_staff=min(value, 3),
            normal_preferred_staff=min(value, 4),
        )
    elif field_name == "workload_scale" and value == 1:
        changes.update(
            express_workload_factor=1.0,
            three_person_workload_multiplier=1.0,
        )
    return replace(OptimizerConfig(), **changes)


@pytest.mark.parametrize("field_name", POSITIVE_INTEGER_FIELDS)
@pytest.mark.parametrize("valid_value", [1, 10_000])
def test_every_positive_integer_field_accepts_valid_boundaries(
    field_name: str, valid_value: int
) -> None:
    assert validate_config(_valid_positive_config(field_name, valid_value)) == ()


@pytest.mark.parametrize(
    ("changes", "path"),
    [
        ({"express_workload_factor": nan}, "config.express_workload_factor"),
        ({"express_workload_factor": inf}, "config.express_workload_factor"),
        ({"express_workload_factor": 0}, "config.express_workload_factor"),
        ({"express_workload_factor": -0.1}, "config.express_workload_factor"),
        ({"express_workload_factor": 1.01}, "config.express_workload_factor"),
        ({"express_workload_factor": 0.801}, "config.express_workload_factor"),
        ({"three_person_workload_multiplier": nan}, "config.three_person_workload_multiplier"),
        ({"three_person_workload_multiplier": 0}, "config.three_person_workload_multiplier"),
        ({"three_person_workload_multiplier": -1}, "config.three_person_workload_multiplier"),
        ({"three_person_workload_multiplier": 1.111}, "config.three_person_workload_multiplier"),
    ],
)
def test_workload_factor_torture_matrix(
    changes: dict[str, object], path: str
) -> None:
    assert any(
        issue.path == path
        for issue in validate_config(replace(OptimizerConfig(), **changes))
    )


def test_solver_configuration_accepts_tiny_positive_limit_and_rejects_bad_values() -> None:
    assert validate_config(
        replace(
            OptimizerConfig(),
            solver_time_limit_seconds=0.001,
            solver_random_seed=0,
            solver_num_search_workers=1,
        )
    ) == ()
    for changes in (
        {"solver_time_limit_seconds": 0},
        {"solver_time_limit_seconds": inf},
        {"solver_random_seed": True},
        {"solver_random_seed": -1},
        {"solver_num_search_workers": 0},
    ):
        assert validate_config(replace(OptimizerConfig(), **changes))


@pytest.mark.parametrize(
    ("employee_count", "expected_staffing"),
    [(0, 0), (2, 2), (3, 3), (4, 4), (8, 4)],
)
def test_staffing_boundaries_never_exceed_maximum(
    employee_count: int, expected_staffing: int
) -> None:
    employees = tuple(synthetic_employee(f"R{index:03}") for index in range(employee_count))
    result = optimize_flight_assignments(
        operational_day(
            employees=employees,
            shifts=tuple(
                synthetic_shift(worker.employee_id, start=at(7), end=at(11))
                for worker in employees
            ),
            flights=(arrival("FX101", at(9)),),
        ),
        replace(OptimizerConfig(), solver_time_limit_seconds=2.0),
    )

    assert result.status is OptimizationStatus.OPTIMAL
    assert result.flight_results[0].staffing_count == expected_staffing
    assert result.flight_results[0].staffing_count <= 4


def test_simultaneous_flights_compete_safely_for_five_people() -> None:
    employees = tuple(synthetic_employee(f"R{index:03}") for index in range(5))
    flights = (arrival("FX101", at(9)), arrival("FX102", at(9)))
    result = optimize_flight_assignments(
        operational_day(
            employees=employees,
            shifts=tuple(
                synthetic_shift(worker.employee_id, start=at(7), end=at(11))
                for worker in employees
            ),
            flights=flights,
        ),
        replace(OptimizerConfig(), solver_time_limit_seconds=2.0),
    )

    assert sorted(item.staffing_count for item in result.flight_results) == [2, 3]
    assert sum(item.staffing_count for item in result.flight_results) == 5


def test_fixed_assignments_at_and_above_maximum() -> None:
    target = arrival("FX101", at(9))
    employees = tuple(synthetic_employee(f"R{index:03}") for index in range(5))
    shifts = tuple(
        synthetic_shift(worker.employee_id, start=at(7), end=at(11))
        for worker in employees
    )
    at_max = operational_day(
        employees=employees,
        shifts=shifts,
        flights=(target,),
        fixed=tuple(FixedAssignment(worker.employee_id, target) for worker in employees[:4]),
    )
    above_max = replace(
        at_max,
        fixed_assignments=tuple(
            FixedAssignment(worker.employee_id, target) for worker in employees
        ),
    )

    assert validate_operational_day(at_max) == ()
    assert "FIXED_ASSIGNMENTS_EXCEED_MAXIMUM_STAFFING" in {
        issue.code for issue in validate_operational_day(above_max)
    }
    with pytest.raises(InputValidationError):
        optimize_flight_assignments(above_max)


@pytest.mark.parametrize(
    ("qualifications", "expected_push", "expected_close"),
    [
        (((), (), ()), False, False),
        (((Qualification.PUSH,), (), ()), True, False),
        (((Qualification.CLOSE_OUT,), (), ()), False, True),
        (((Qualification.PUSH,), (Qualification.CLOSE_OUT,), ()), True, True),
        (((Qualification.PUSH, Qualification.CLOSE_OUT), (), ()), True, True),
    ],
)
def test_qualification_crew_matrix(
    qualifications: tuple[tuple[Qualification, ...], ...],
    expected_push: bool,
    expected_close: bool,
) -> None:
    employees = tuple(
        synthetic_employee(f"R{index:03}", qualifications=values)
        for index, values in enumerate(qualifications, start=1)
    )
    target = departure("FX101", at(9))
    result = optimize_flight_assignments(
        operational_day(
            employees=employees,
            shifts=tuple(
                synthetic_shift(worker.employee_id, start=at(7), end=at(11))
                for worker in employees
            ),
            flights=(target,),
            fixed=tuple(FixedAssignment(worker.employee_id, target) for worker in employees),
        ),
        replace(OptimizerConfig(), solver_time_limit_seconds=2.0),
    )

    assert result.flight_results[0].push_covered is expected_push
    assert result.flight_results[0].close_covered is expected_close


@pytest.mark.parametrize(
    ("gap_minutes", "break_status", "longest_streak"),
    [
        (29, BreakStatus.UNSATISFIED, 2),
        (30, BreakStatus.SATISFIED, 2),
        (39, BreakStatus.SATISFIED, 2),
        (40, BreakStatus.SATISFIED, 1),
        (41, BreakStatus.SATISFIED, 1),
    ],
)
def test_break_and_streak_exact_gap_boundaries(
    gap_minutes: int,
    break_status: BreakStatus,
    longest_streak: int,
) -> None:
    first = arrival("FX101", at(8, 10))  # [08:00, 08:30)
    second = arrival(
        "FX102", at(8, 40) + timedelta(minutes=gap_minutes)
    )
    result = optimize_flight_assignments(
        _fixed_employee_day((first, second)), _one_person_config()
    )
    employee_result = result.employee_results[0]

    assert employee_result.break_status is break_status
    assert employee_result.longest_consecutive_streak == longest_streak


def test_zero_one_and_interrupted_break_sequences() -> None:
    empty_result = optimize_flight_assignments(
        _fixed_employee_day(()), _one_person_config()
    )
    one = arrival("FX101", at(8, 10))
    one_result = optimize_flight_assignments(
        _fixed_employee_day((one,)), _one_person_config()
    )
    interrupted = (
        one,
        arrival("FX102", at(8, 50)),
        arrival("FX103", at(9, 30)),
    )
    interrupted_result = optimize_flight_assignments(
        _fixed_employee_day(interrupted), _one_person_config()
    )

    assert empty_result.employee_results[0].break_status is BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS
    assert one_result.employee_results[0].break_status is BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS
    assert interrupted_result.employee_results[0].break_status is BreakStatus.UNSATISFIED


def test_separate_shifts_reset_streaks_and_do_not_form_a_break() -> None:
    first = arrival("FX101", at(8, 10))
    second = arrival("FX102", at(10, 10))
    shifts = (
        synthetic_shift("R001", start=at(8), end=at(8, 30)),
        synthetic_shift("R001", start=at(10), end=at(10, 30)),
    )
    result = optimize_flight_assignments(
        _fixed_employee_day((first, second), shifts=shifts), _one_person_config()
    )

    assert result.employee_results[0].break_status is BreakStatus.UNSATISFIED
    assert result.employee_results[0].longest_consecutive_streak == 1


@pytest.mark.parametrize(
    ("gap_minutes", "expected_pairs"),
    [(120, 1), (121, 0), (0, 1), (-1, 0)],
)
def test_continuity_horizon_touching_and_overlap_boundaries(
    gap_minutes: int, expected_pairs: int
) -> None:
    first = arrival("FX101", at(8, 10))
    second_start = at(8, 30) + timedelta(minutes=gap_minutes)
    second = arrival("FX102", second_start + timedelta(minutes=10))
    if gap_minutes < 0:
        employees = (synthetic_employee("R001"), synthetic_employee("R002"))
        day = operational_day(
            employees=employees,
            shifts=tuple(
                synthetic_shift(worker.employee_id, start=at(7), end=at(10))
                for worker in employees
            ),
            flights=(first, second),
            fixed=(
                FixedAssignment("R001", first),
                FixedAssignment("R002", second),
            ),
        )
    else:
        day = _fixed_employee_day((first, second))
    result = optimize_flight_assignments(day, _one_person_config())

    assert result.continuity_metrics is not None
    assert result.continuity_metrics.eligible_transition_count == expected_pairs
    assert result.continuity_metrics.total_retained_employee_transitions == expected_pairs
