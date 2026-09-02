"""Tests for deterministic employee-to-flight eligibility."""

from dataclasses import replace
from datetime import datetime

import pytest

import ramp_optimizer.eligibility as eligibility_module
from ramp_optimizer import (
    EligibilityReason,
    Employee,
    EmployeeShift,
    FixedAssignment,
    Flight,
    FlightOperationalFacts,
    FlightType,
    OperationalRole,
    OptimizerConfig,
    Qualification,
    ShiftImportRecord,
    assess_employee_flight_eligibility,
    employee_is_available_for_interval,
)

DAY = (2026, 9, 2)
CONFIG = OptimizerConfig()


def at(hour: int, minute: int = 0, *, day: int = 2) -> datetime:
    return datetime(2026, 9, day, hour, minute)


def employee(
    employee_id: str = "E001",
    *,
    enabled: bool = True,
    qualifications: frozenset[Qualification] = frozenset(),
) -> Employee:
    return Employee(employee_id, "Avery Stone", qualifications, enabled)


def shift(
    start: datetime,
    end: datetime,
    role: OperationalRole = OperationalRole.RAMP_AGENT,
    employee_id: str = "E001",
) -> EmployeeShift:
    return EmployeeShift(employee_id, start, end, role)


def departure(
    number: str = "100", at_time: datetime = datetime(2026, 9, 2, 9)
) -> Flight:
    return Flight(departure_flight_number=number, departure_time=at_time)


def assess(
    *,
    candidate: Employee | None = None,
    shifts: tuple[EmployeeShift, ...] | None = None,
    flight: Flight | None = None,
    fixed: tuple[FixedAssignment, ...] = (),
    config: OptimizerConfig = CONFIG,
    **policy: bool,
):
    return assess_employee_flight_eligibility(
        candidate or employee(),
        shifts
        if shifts is not None
        else (shift(at(5), at(13)),),
        flight or departure(),
        config,
        fixed,
        **policy,
    )


def test_complete_interval_must_fit_inside_one_shift() -> None:
    shifts = (
        shift(at(5), at(9)),
        shift(at(10), at(14)),
    )

    assert employee_is_available_for_interval(employee(), shifts, at(6), at(8))
    assert not employee_is_available_for_interval(employee(), shifts, at(8), at(11))


def test_interval_ending_exactly_at_shift_end_is_available() -> None:
    shifts = (shift(at(5), at(9)),)

    assert employee_is_available_for_interval(employee(), shifts, at(8), at(9))


def test_overnight_shift_uses_full_datetimes() -> None:
    overnight = shift(at(22), at(6, day=3))

    assert employee_is_available_for_interval(
        employee(), (overnight,), at(1, day=3), at(2, day=3)
    )


def test_enabled_employee_is_eligible_and_disabled_reason_is_specific() -> None:
    enabled = assess()
    disabled = assess(candidate=employee(enabled=False), shifts=())

    assert enabled.eligible
    assert enabled.reasons == ()
    assert not disabled.eligible
    assert disabled.reasons == (EligibilityReason.EMPLOYEE_DISABLED,)


@pytest.mark.parametrize(
    ("shift_start", "shift_end", "eligible"),
    [
        (at(5), at(13), True),
        (at(8), at(13), True),
        (at(5), at(9), True),
        (at(8, 1), at(13), False),
        (at(5), at(8, 59), False),
    ],
)
def test_full_work_window_shift_containment(
    shift_start: datetime, shift_end: datetime, eligible: bool
) -> None:
    assessment = assess(shifts=(shift(shift_start, shift_end),))

    assert assessment.eligible is eligible
    assert assessment.reasons == (
        () if eligible else (EligibilityReason.OUTSIDE_SHIFT,)
    )


def test_two_shifts_are_not_merged_across_a_gap() -> None:
    assessment = assess(
        shifts=(shift(at(5), at(8, 30)), shift(at(8, 45), at(13)))
    )

    assert assessment.reasons == (EligibilityReason.OUTSIDE_SHIFT,)


def test_later_separate_shift_can_independently_contain_flight() -> None:
    assessment = assess(
        shifts=(shift(at(5), at(7)), shift(at(8), at(13)))
    )

    assert assessment.eligible


def test_overnight_shift_contains_overnight_flight() -> None:
    overnight_flight = departure("101", at(0, 30, day=3))
    assessment = assess(
        shifts=(shift(at(22), at(6, day=3)),),
        flight=overnight_flight,
    )

    assert assessment.eligible


def test_no_employee_shift_has_specific_reason() -> None:
    assessment = assess(shifts=())

    assert assessment.reasons == (EligibilityReason.NO_EMPLOYEE_SHIFT,)


@pytest.mark.parametrize(
    ("role", "expected"),
    [
        (OperationalRole.RAMP_AGENT, True),
        (OperationalRole.RAMP_LEAD, False),
        (OperationalRole.TRAINEE, False),
        (OperationalRole.POSSIBLE_RAMP_SUPPORT, False),
        (OperationalRole.NON_RAMP, False),
        (OperationalRole.UNKNOWN, False),
    ],
)
def test_every_operational_role_under_default_policy(
    role: OperationalRole, expected: bool
) -> None:
    assessment = assess(shifts=(shift(at(5), at(13), role),))

    assert assessment.eligible is expected
    if not expected:
        assert assessment.reasons == (
            EligibilityReason.INELIGIBLE_OPERATIONAL_ROLE,
        )


@pytest.mark.parametrize(
    ("role", "policy_name"),
    [
        (OperationalRole.RAMP_LEAD, "include_leads"),
        (OperationalRole.TRAINEE, "allow_trainees"),
        (
            OperationalRole.POSSIBLE_RAMP_SUPPORT,
            "allow_possible_ramp_support",
        ),
    ],
)
def test_special_roles_require_their_explicit_override(
    role: OperationalRole, policy_name: str
) -> None:
    employee_shift = shift(at(5), at(13), role)

    assert not assess(shifts=(employee_shift,)).eligible
    assert assess(shifts=(employee_shift,), **{policy_name: True}).eligible


@pytest.mark.parametrize(
    ("role", "config_field", "override_name"),
    [
        (
            OperationalRole.TRAINEE,
            "allow_trainees_for_assignments",
            "allow_trainees",
        ),
        (
            OperationalRole.POSSIBLE_RAMP_SUPPORT,
            "allow_possible_ramp_support_for_assignments",
            "allow_possible_ramp_support",
        ),
    ],
)
def test_configured_role_policy_and_explicit_overrides(
    role: OperationalRole, config_field: str, override_name: str
) -> None:
    employee_shift = shift(at(5), at(13), role)
    enabled_config = replace(CONFIG, **{config_field: True})

    assert not assess(shifts=(employee_shift,)).eligible
    assert assess(shifts=(employee_shift,), config=enabled_config).eligible
    assert not assess(
        shifts=(employee_shift,),
        config=enabled_config,
        **{override_name: False},
    ).eligible
    assert assess(
        shifts=(employee_shift,),
        config=CONFIG,
        **{override_name: True},
    ).eligible


@pytest.mark.parametrize("role", [OperationalRole.NON_RAMP, OperationalRole.UNKNOWN])
def test_non_ramp_and_unknown_remain_ineligible_with_all_overrides(
    role: OperationalRole,
) -> None:
    assessment = assess(
        shifts=(shift(at(5), at(13), role),),
        include_leads=True,
        allow_trainees=True,
        allow_possible_ramp_support=True,
    )

    assert not assessment.eligible


def test_source_position_and_qualifications_do_not_override_normalized_role() -> None:
    roster_employee = employee(
        qualifications=frozenset({Qualification.PUSH, Qualification.CLOSE_OUT})
    )
    record = ShiftImportRecord(
        shift=shift(at(5), at(13), OperationalRole.NON_RAMP),
        source_row=2,
        source_position="Ramp Agent",
    )

    assessment = assess(candidate=roster_employee, shifts=(record.shift,))

    assert assessment.reasons == (EligibilityReason.INELIGIBLE_OPERATIONAL_ROLE,)


@pytest.mark.parametrize(
    "qualifications",
    [
        frozenset(),
        frozenset({Qualification.PUSH}),
        frozenset({Qualification.CLOSE_OUT}),
        frozenset({Qualification.PUSH, Qualification.CLOSE_OUT}),
    ],
)
def test_qualifications_do_not_filter_generic_eligibility(
    qualifications: frozenset[Qualification],
) -> None:
    roster_employee = employee(qualifications=qualifications)

    assessment = assess(candidate=roster_employee)

    assert assessment.eligible
    assert roster_employee.qualifications == qualifications


def test_overlapping_fixed_assignment_blocks_employee() -> None:
    fixed_flight = departure("101", at(9))
    target = departure("102", at(9, 30))

    assessment = assess(
        flight=target,
        fixed=(FixedAssignment("E001", fixed_flight),),
    )

    assert assessment.reasons == (EligibilityReason.OVERLAPS_FIXED_ASSIGNMENT,)


def test_endpoint_touching_fixed_assignment_does_not_block_employee() -> None:
    fixed_flight = departure("101", at(9))
    target = departure("102", at(10))

    assessment = assess(
        flight=target,
        fixed=(FixedAssignment("E001", fixed_flight),),
    )

    assert assessment.eligible


def test_separated_or_other_employee_fixed_assignment_is_irrelevant() -> None:
    target = departure("102", at(11))
    fixed_values = (
        FixedAssignment("E001", departure("101", at(9))),
        FixedAssignment("E002", departure("103", at(10, 30))),
    )

    assert assess(flight=target, fixed=fixed_values).eligible


def test_fixed_target_is_recognized_and_does_not_overlap_itself() -> None:
    target = departure()

    assessment = assess(
        flight=target,
        fixed=(FixedAssignment("E001", target),),
    )

    assert assessment.eligible
    assert assessment.fixed_to_target
    assert assessment.reasons == ()


@pytest.mark.parametrize(
    "flight",
    [
        departure("100"),
        Flight(
            departure_flight_number="100",
            departure_time=at(9),
            gate="B4",
        ),
        Flight(
            departure_flight_number="100",
            departure_time=at(9),
            heavy=True,
        ),
        departure("3000"),
    ],
)
def test_gate_heavy_and_service_category_do_not_change_basic_eligibility(
    flight: Flight,
) -> None:
    assert assess(flight=flight).eligible


def test_assessment_uses_approved_timing_derivation(monkeypatch) -> None:
    target = departure()
    calls: list[Flight] = []

    def fake_derivation(flight: Flight, config: OptimizerConfig):
        calls.append(flight)
        return FlightOperationalFacts(
            flight_type=FlightType.DEPARTURE_ONLY,
            work_start=at(8),
            work_end=at(9),
            arrival_numeric_flight_number=None,
            departure_numeric_flight_number=100,
            express=False,
        )

    monkeypatch.setattr(
        eligibility_module, "derive_flight_operational_facts", fake_derivation
    )

    assert assess(flight=target).eligible
    assert calls == [target]
