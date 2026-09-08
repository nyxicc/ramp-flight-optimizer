"""Independent public-result verification for synthetic optimizer scenarios.

The checks intentionally use no private optimizer helpers.  They reconstruct
facts from public inputs and immutable result records so a defect cannot pass
merely because a test repeats the optimizer's internal assertion path.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from itertools import combinations
import re

import pytest

from ramp_optimizer import (
    BreakStatus,
    EmergencyPassDisposition,
    EmergencyStaffingStatus,
    Flight,
    FlightType,
    OperationalDay,
    OperationalReadinessStatus,
    OperationalRole,
    OptimizationResult,
    OptimizationStatus,
    OptimizerConfig,
    Qualification,
    StaffingStatus,
    WarningCode,
    WarningSeverity,
    format_optimization_report,
)


_NUMBER = re.compile(r"[A-Za-z]*(\d+)\Z")


def flight_identity(flight: Flight) -> tuple[str | None, str | None]:
    return flight.arrival_flight_number, flight.departure_flight_number


def semantic_projection(result: OptimizationResult) -> dict[str, object]:
    """Drop runtime and presentation-order accidents from a result comparison."""

    return {
        "flights": {
            flight_identity(item.flight): (
                frozenset(item.assigned_employee_ids),
                item.staffing_count,
                item.minimum_met,
                item.preferred_met,
                item.push_covered,
                item.close_covered,
            )
            for item in result.flight_results
        },
        "employees": {
            item.employee_id: (
                frozenset(flight_identity(flight) for flight in item.assigned_flights),
                item.flight_count,
                item.mainline_flight_count,
                item.express_flight_count,
                item.three_person_flight_count,
                item.longest_consecutive_streak,
                item.break_status,
                item.adjusted_workload,
            )
            for item in result.employee_results
        },
        "fairness": result.fairness_metrics,
        "continuity": (
            result.continuity_metrics.eligible_transition_count,
            result.continuity_metrics.total_retained_employee_transitions,
        )
        if result.continuity_metrics is not None
        else None,
        "objectives": tuple(
            (item.name, item.value, item.proven_optimal)
            for item in result.objective_values
        ),
        "warnings": tuple(
            (
                item.code,
                item.severity,
                item.employee_id,
                item.arrival_flight_number,
                item.departure_flight_number,
            )
            for item in result.warnings
        ),
        "readiness": result.operational_readiness,
        "emergency": (
            result.emergency_staffing_status,
            result.emergency_pass_disposition,
            tuple(
                (item.employee_id, flight_identity(item.flight), item.reasons)
                for item in result.lead_assignments
            ),
        ),
        "summary": result.schedule_summary,
    }


def _manual_facts(
    flight: Flight, config: OptimizerConfig
) -> tuple[FlightType, object, object, bool]:
    if flight.arrival_time is not None and flight.departure_time is not None:
        flight_type = FlightType.TURN
        start = flight.arrival_time - timedelta(
            minutes=config.arrival_preparation_minutes
        )
        end = flight.departure_time
        numbers = (flight.arrival_flight_number, flight.departure_flight_number)
    elif flight.arrival_time is not None:
        flight_type = FlightType.ARRIVAL_ONLY
        start = flight.arrival_time - timedelta(
            minutes=config.arrival_preparation_minutes
        )
        end = flight.arrival_time + timedelta(
            minutes=config.arrival_offload_minutes
        )
        numbers = (flight.arrival_flight_number,)
    else:
        assert flight.departure_time is not None
        flight_type = FlightType.DEPARTURE_ONLY
        start = flight.departure_time - timedelta(
            minutes=config.departure_work_minutes
        )
        end = flight.departure_time
        numbers = (flight.departure_flight_number,)
    parsed = []
    for number in numbers:
        assert number is not None
        match = _NUMBER.fullmatch(number.strip())
        assert match is not None
        parsed.append(int(match.group(1)))
    categories = {value > config.express_threshold for value in parsed}
    assert len(categories) == 1
    return flight_type, start, end, categories.pop()


def _allowed_role(
    role: OperationalRole,
    config: OptimizerConfig,
    *,
    include_leads: bool,
) -> bool:
    return (
        role is OperationalRole.RAMP_AGENT
        or role is OperationalRole.RAMP_LEAD
        and include_leads
        or role is OperationalRole.TRAINEE
        and config.allow_trainees_for_assignments
        or role is OperationalRole.POSSIBLE_RAMP_SUPPORT
        and config.allow_possible_ramp_support_for_assignments
    )


def _containing_shifts(
    day: OperationalDay,
    config: OptimizerConfig,
    employee_id: str,
    start: object,
    end: object,
    *,
    include_leads: bool,
) -> tuple[object, ...]:
    return tuple(
        shift
        for shift in day.employee_shifts
        if shift.employee_id.casefold() == employee_id.casefold()
        and _allowed_role(
            shift.normalized_role, config, include_leads=include_leads
        )
        and shift.start <= start
        and end <= shift.end
    )


def _selected_emergency_pass(result: OptimizationResult) -> bool:
    return any(
        attempt.included_leads and attempt.selected_as_final
        for attempt in result.attempts
    )


def assert_result_invariants(
    day: OperationalDay,
    config: OptimizerConfig,
    result: OptimizationResult,
) -> None:
    """Reconstruct assignment, legality, operational, and reporting invariants."""

    assert result.status in {OptimizationStatus.OPTIMAL, OptimizationStatus.FEASIBLE}
    assert tuple(item.flight for item in result.flight_results) == day.flights
    assert len(result.flight_results) == len(day.flights)
    employee_by_id = {employee.employee_id: employee for employee in day.employees}
    assert len(employee_by_id) == len(day.employees)
    facts = {
        flight: _manual_facts(flight, config)
        for flight in day.flights
    }
    result_by_flight = {item.flight: item for item in result.flight_results}
    assert len(result_by_flight) == len(day.flights)
    include_leads = _selected_emergency_pass(result)
    lead_pairs = {
        (item.employee_id, item.flight) for item in result.lead_assignments
    }

    assigned_windows: dict[str, list[tuple[object, object]]] = defaultdict(list)
    expected_defect_warnings: set[
        tuple[WarningCode, str | None, str | None, str | None]
    ] = set()
    for item in result.flight_results:
        flight_type, start, end, express = facts[item.flight]
        crew = item.assigned_employee_ids
        assert len(crew) == len(set(crew)) == item.staffing_count
        assert set(crew) <= set(employee_by_id)
        assert item.flight_type is flight_type
        assert item.work_start == start
        assert item.work_end == end
        assert item.express is express
        assert item.heavy is item.flight.heavy
        preferred = (
            config.heavy_preferred_staff
            if item.flight.heavy
            else config.normal_preferred_staff
        )
        assert item.minimum_staff == config.minimum_staff
        assert item.preferred_staff == preferred
        assert item.maximum_staff == preferred
        assert item.staffing_count <= preferred
        assert item.minimum_met is (item.staffing_count >= config.minimum_staff)
        assert item.minimum_shortfall == max(
            0, config.minimum_staff - item.staffing_count
        )
        assert item.preferred_met is (item.staffing_count >= preferred)
        assert item.preferred_shortfall == preferred - item.staffing_count
        expected_status = (
            StaffingStatus.PREFERRED_STAFFED
            if item.preferred_met
            else StaffingStatus.MINIMUM_STAFFED
            if item.minimum_met
            else StaffingStatus.BELOW_MINIMUM
        )
        assert item.staffing_status is expected_status

        qualifications = frozenset(
            qualification
            for employee_id in crew
            for qualification in employee_by_id[employee_id].qualifications
        )
        if flight_type is FlightType.ARRIVAL_ONLY:
            assert item.push_covered is None
            assert item.close_covered is None
        else:
            assert item.push_covered is (Qualification.PUSH in qualifications)
            assert item.close_covered is (
                Qualification.CLOSE_OUT in qualifications
            )

        if not item.minimum_met:
            expected_defect_warnings.add(
                (
                    WarningCode.MINIMUM_STAFFING_NOT_MET,
                    item.flight.arrival_flight_number,
                    item.flight.departure_flight_number,
                    None,
                )
            )
        if item.push_covered is False:
            expected_defect_warnings.add(
                (
                    WarningCode.PUSH_QUALIFICATION_NOT_MET,
                    item.flight.arrival_flight_number,
                    item.flight.departure_flight_number,
                    None,
                )
            )
        if item.close_covered is False:
            expected_defect_warnings.add(
                (
                    WarningCode.CLOSE_QUALIFICATION_NOT_MET,
                    item.flight.arrival_flight_number,
                    item.flight.departure_flight_number,
                    None,
                )
            )

        for employee_id in crew:
            employee = employee_by_id[employee_id]
            assert employee.enabled
            containing = _containing_shifts(
                day,
                config,
                employee_id,
                start,
                end,
                include_leads=include_leads,
            )
            assert containing
            if all(
                shift.normalized_role is OperationalRole.RAMP_LEAD
                for shift in containing
            ):
                assert (employee_id, item.flight) in lead_pairs
            assigned_windows[employee_id].append((start, end))

    for fixed in day.fixed_assignments:
        assert fixed.employee_id in result_by_flight[fixed.flight].assigned_employee_ids
        assert fixed.employee_id in result_by_flight[fixed.flight].fixed_employee_ids
    for windows in assigned_windows.values():
        for (first_start, first_end), (second_start, second_end) in combinations(
            windows, 2
        ):
            assert not (
                first_start < second_end and second_start < first_end
            )

    employee_results = {item.employee_id: item for item in result.employee_results}
    assert len(employee_results) == len(result.employee_results)
    for employee_id, item in employee_results.items():
        assigned = tuple(
            flight
            for flight in day.flights
            if employee_id in result_by_flight[flight].assigned_employee_ids
        )
        chronological = tuple(
            sorted(
                assigned,
                key=lambda flight: (
                    facts[flight][1],
                    facts[flight][2],
                    day.flights.index(flight),
                ),
            )
        )
        assert item.assigned_flights == chronological
        assert item.flight_count == len(chronological)
        assert item.mainline_flight_count == sum(
            not facts[flight][3] for flight in chronological
        )
        assert item.express_flight_count == sum(
            facts[flight][3] for flight in chronological
        )
        assert item.three_person_flight_count == sum(
            result_by_flight[flight].staffing_count == 3
            for flight in chronological
        )

        shifts_by_flight = {
            flight: _containing_shifts(
                day,
                config,
                employee_id,
                facts[flight][1],
                facts[flight][2],
                include_leads=include_leads,
            )
            for flight in chronological
        }
        assert all(len(shifts) == 1 for shifts in shifts_by_flight.values())
        if len(chronological) < 2:
            expected_break = BreakStatus.NOT_EVALUABLE_BETWEEN_ASSIGNMENTS
            has_lead_shift = any(
                shift.employee_id == employee_id
                and shift.normalized_role is OperationalRole.RAMP_LEAD
                for shift in day.employee_shifts
            )
            if not chronological and has_lead_shift:
                expected_break = BreakStatus.NOT_APPLICABLE
        else:
            expected_break = BreakStatus.UNSATISFIED
            for earlier, later in zip(chronological, chronological[1:]):
                gap = facts[later][1] - facts[earlier][2]
                if gap < timedelta(minutes=config.required_break_minutes):
                    continue
                if _containing_shifts(
                    day,
                    config,
                    employee_id,
                    facts[earlier][1],
                    facts[later][2],
                    include_leads=include_leads,
                ):
                    expected_break = BreakStatus.SATISFIED
                    break
        assert item.break_status is expected_break
        if expected_break is BreakStatus.UNSATISFIED:
            expected_defect_warnings.add(
                (WarningCode.REQUIRED_BREAK_NOT_MET, None, None, employee_id)
            )

        grouped: dict[object, list[Flight]] = defaultdict(list)
        for flight in chronological:
            grouped[shifts_by_flight[flight][0]].append(flight)
        longest = 0
        for group in grouped.values():
            current = 0
            previous: Flight | None = None
            for flight in group:
                if (
                    previous is None
                    or facts[flight][1] - facts[previous][2]
                    >= timedelta(minutes=config.consecutive_reset_minutes)
                ):
                    current = 1
                else:
                    current += 1
                longest = max(longest, current)
                previous = flight
        assert item.longest_consecutive_streak == longest

        workload = Decimal("0")
        for flight in chronological:
            contribution = (
                Decimal(str(config.express_workload_factor))
                if facts[flight][3]
                else Decimal("1")
            )
            if result_by_flight[flight].staffing_count == 3:
                contribution *= Decimal(
                    str(config.three_person_workload_multiplier)
                )
            workload += contribution
        assert item.adjusted_workload == pytest.approx(float(workload))

    participant_results = tuple(
        item
        for item in result.employee_results
        if item.proportional_target_flight_count is not None
    )
    fairness = result.fairness_metrics
    assert fairness is not None
    counts = tuple(item.flight_count for item in participant_results)
    assert fairness.participating_employee_count == len(participant_results)
    assert fairness.total_assignments == sum(counts)
    assert fairness.average_flights == pytest.approx(
        sum(counts) / len(counts) if counts else 0.0
    )
    assert fairness.highest_flight_count == max(counts, default=0)
    assert fairness.lowest_flight_count == min(counts, default=0)
    assert fairness.flight_count_spread == (
        max(counts, default=0) - min(counts, default=0)
    )
    assert fairness.maximum_consecutive_streak == max(
        (item.longest_consecutive_streak for item in participant_results),
        default=0,
    )
    workload_values = tuple(
        item.adjusted_workload for item in participant_results
    )
    assert all(value is not None for value in workload_values)
    assert fairness.adjusted_workload_spread == pytest.approx(
        max(workload_values, default=0) - min(workload_values, default=0)
    )
    total_shift_minutes = sum(
        item.scheduled_shift_minutes or 0 for item in participant_results
    )
    assert fairness.total_participating_shift_minutes == total_shift_minutes
    for item in participant_results:
        assert item.scheduled_shift_minutes is not None
        target = (
            fairness.total_assignments
            * item.scheduled_shift_minutes
            / total_shift_minutes
        )
        assert item.proportional_target_flight_count == pytest.approx(target)
        assert item.shift_adjusted_deviation == pytest.approx(
            abs(item.flight_count - target)
        )
    assert fairness.total_shift_adjusted_deviation == pytest.approx(
        sum(item.shift_adjusted_deviation or 0 for item in participant_results)
    )

    continuity = result.continuity_metrics
    assert continuity is not None
    participant_ids = {item.employee_id for item in participant_results}
    ordered_indices = sorted(
        range(len(day.flights)),
        key=lambda index: (
            facts[day.flights[index]][1],
            facts[day.flights[index]][2],
            index,
        ),
    )
    expected_transitions = []
    horizon = timedelta(minutes=config.continuity_horizon_minutes)
    for position, earlier_index in enumerate(ordered_indices):
        earlier = day.flights[earlier_index]
        for later_index in ordered_indices[position + 1 :]:
            later = day.flights[later_index]
            if facts[later][1] < facts[earlier][2]:
                continue
            if facts[later][1] - facts[earlier][2] > horizon:
                break
            retained = tuple(
                employee.employee_id
                for employee in day.employees
                if employee.employee_id in participant_ids
                and employee.employee_id
                in result_by_flight[earlier].assigned_employee_ids
                and employee.employee_id
                in result_by_flight[later].assigned_employee_ids
            )
            expected_transitions.append((earlier, later, retained))
    actual_transitions = tuple(
        (
            item.previous_flight,
            item.next_flight,
            item.retained_employee_ids,
        )
        for item in continuity.transitions
    )
    assert actual_transitions == tuple(expected_transitions)
    retained_counts = tuple(len(item[2]) for item in expected_transitions)
    assert continuity.eligible_transition_count == len(expected_transitions)
    assert continuity.total_retained_employee_transitions == sum(retained_counts)
    assert continuity.average_retained_employees_per_transition == pytest.approx(
        sum(retained_counts) / len(retained_counts)
        if retained_counts
        else 0.0
    )
    assert continuity.strongest_retention_count == max(retained_counts, default=0)
    if expected_transitions:
        strongest_index = retained_counts.index(max(retained_counts))
        strongest = continuity.strongest_transition
        assert strongest is not None
        assert (
            strongest.previous_flight,
            strongest.next_flight,
            strongest.retained_employee_ids,
        ) == expected_transitions[strongest_index]
    else:
        assert continuity.strongest_transition is None

    actual_defect_warnings = {
        (
            warning.code,
            warning.arrival_flight_number,
            warning.departure_flight_number,
            warning.employee_id,
        )
        for warning in result.warnings
        if warning.code
        in {
            WarningCode.MINIMUM_STAFFING_NOT_MET,
            WarningCode.PUSH_QUALIFICATION_NOT_MET,
            WarningCode.CLOSE_QUALIFICATION_NOT_MET,
            WarningCode.REQUIRED_BREAK_NOT_MET,
        }
    }
    assert actual_defect_warnings == expected_defect_warnings
    warning_identities = tuple(
        (
            warning.code,
            warning.employee_id,
            warning.arrival_flight_number,
            warning.departure_flight_number,
        )
        for warning in result.warnings
    )
    assert len(warning_identities) == len(set(warning_identities))

    summary = result.schedule_summary
    assert summary is not None
    required = tuple(
        item
        for item in result.flight_results
        if item.flight_type is not FlightType.ARRIVAL_ONLY
    )
    assert summary.total_flights == len(result.flight_results)
    assert summary.minimum_staffed_flights == sum(
        item.minimum_met for item in result.flight_results
    )
    assert summary.below_minimum_flights == sum(
        not item.minimum_met for item in result.flight_results
    )
    assert summary.preferred_staffed_flights == sum(
        item.preferred_met for item in result.flight_results
    )
    assert summary.qualification_required_flights == len(required)
    assert summary.qualification_compliant_flights == sum(
        bool(item.push_covered) and bool(item.close_covered) for item in required
    )
    assert summary.missing_push_flights == sum(
        not bool(item.push_covered) for item in required
    )
    assert summary.missing_close_out_flights == sum(
        not bool(item.close_covered) for item in required
    )
    assert summary.total_assignments == sum(
        item.staffing_count for item in result.flight_results
    )
    assert summary.total_assignments == sum(
        item.flight_count for item in result.employee_results
    )
    assert summary.emergency_lead_assignments == len(result.lead_assignments)
    assert summary.warning_count == len(result.warnings)
    assert summary.critical_warning_count == sum(
        warning.severity is WarningSeverity.CRITICAL
        for warning in result.warnings
    )
    assert summary.all_objectives_proven_optimal is (
        bool(result.objective_values)
        and all(item.proven_optimal for item in result.objective_values)
    )

    operational_defect = any(
        not item.minimum_met
        or item.flight_type is not FlightType.ARRIVAL_ONLY
        and (not item.push_covered or not item.close_covered)
        for item in result.flight_results
    ) or any(
        item.break_status is BreakStatus.UNSATISFIED
        for item in result.employee_results
    )
    expected_readiness = (
        OperationalReadinessStatus.MANUAL_INTERVENTION_REQUIRED
        if operational_defect
        else OperationalReadinessStatus.READY_WITH_WARNINGS
        if result.warnings
        else OperationalReadinessStatus.READY
    )
    assert result.operational_readiness is expected_readiness
    assert result.emergency_staffing_status is (
        EmergencyStaffingStatus.CRITICAL_SHORTAGE_REMAINS
        if any(
            not item.minimum_met
            or item.flight_type is not FlightType.ARRIVAL_ONLY
            and (not item.push_covered or not item.close_covered)
            for item in result.flight_results
        )
        else EmergencyStaffingStatus.LEAD_ASSISTED_SCHEDULE
        if result.lead_assignments
        else EmergencyStaffingStatus.NORMAL_SCHEDULE
    )
    assert sum(attempt.selected_as_final for attempt in result.attempts) == 1
    selected = next(attempt for attempt in result.attempts if attempt.selected_as_final)
    assert selected.status is result.status
    assert selected.minimum_staffed_flights == summary.minimum_staffed_flights
    assert selected.lead_assignments == summary.emergency_lead_assignments
    assert selected.usable_schedule

    report = format_optimization_report(result)
    assert result.operational_readiness.value in report
    assert result.status.value in report
    assert result.emergency_pass_disposition.value in report
    assert f"Flights: {summary.total_flights}" in report
    for warning in result.warnings:
        assert warning.code.value in report
        assert warning.message in report
    for assignment in result.lead_assignments:
        assert assignment.message in report


def lexicographic_values(result: OptimizationResult) -> tuple[int, ...]:
    """Normalize objective directions so larger tuple values are better."""

    minimize = {
        "total_minimum_shortfall",
        "largest_minimum_shortfall",
        "known_unsatisfied_required_breaks",
        "total_emergency_lead_assignments",
        "total_preferred_shortfall",
        "raw_flight_count_spread",
        "total_pairwise_flight_count_difference",
        "maximum_consecutive_flight_streak",
        "total_employee_longest_streaks",
        "total_shift_adjusted_flight_count_deviation",
        "adjusted_workload_spread",
        "total_pairwise_adjusted_workload_difference",
    }
    return tuple(
        -item.value if item.name in minimize else item.value
        for item in result.objective_values
    )
