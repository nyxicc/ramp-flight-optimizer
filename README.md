# Ramp Team Flight Optimizer

A portfolio project for generating ramp-team flight assignment recommendations
from synthetic operational data.

## Current scope

Milestones 1-7 are implemented. The repository currently provides:

- immutable employee, shift, flight, and result domain models;
- structured input validation;
- a privacy-conscious importer for TeamWork daily schedule exports;
- assignment-eligibility checks over separate availability intervals;
- deterministic flight classification, work-window, and service-category
  derivation;
- pure half-open interval overlap behavior;
- explainable employee-flight eligibility and validated candidate preprocessing;
- a limited CP-SAT optimizer for staffing, push and close-out qualification
  coverage, required between-assignment breaks, and raw flight-count fairness.

Shift-adjusted and weighted workload scoring, streak penalties, continuity, and
emergency Lead solving are not implemented yet.

## Employee and availability model

Employee identity is independent from daily schedule rows:

```python
from datetime import datetime

from ramp_optimizer import Employee, EmployeeShift, OperationalRole, Qualification

employee = Employee(
    employee_id="E001",
    name="Avery Stone",
    qualifications=frozenset({Qualification.PUSH}),
)

shift = EmployeeShift(
    employee_id="E001",
    start=datetime(2026, 9, 2, 5),
    end=datetime(2026, 9, 2, 13),
    normalized_role=OperationalRole.RAMP_AGENT,
)
```

One employee may have multiple independent shifts and source-position labels.
Those intervals are never merged into one continuous availability window.
Qualifications come from the employee roster or explicit application input;
schedule position text never grants a qualification.

The core shift contains no spreadsheet provenance. TeamWork source row,
original position text, note presence, and SwapBoard state live in immutable
`ShiftImportRecord` objects. `ScheduleImportResult.shifts` exposes only the core
shift values needed by eligibility and the optimizer.

## Flight movements and derived operational facts

One `Flight` represents one aircraft movement. A turn has two flight numbers
because its inbound arrival and outbound departure are different operational
flights. Only source facts are stored on the immutable input model:

```python
from datetime import datetime

from ramp_optimizer import Flight

arrival_only = Flight(
    arrival_flight_number="1428",
    arrival_time=datetime(2026, 9, 2, 9, 2),
)
departure_only = Flight(
    departure_flight_number="2690",
    departure_time=datetime(2026, 9, 2, 6, 0),
)
turn = Flight(
    arrival_flight_number="1428",
    arrival_time=datetime(2026, 9, 2, 9, 2),
    departure_flight_number="1814",
    departure_time=datetime(2026, 9, 2, 10, 10),
    gate="B4",
)
```

Flight type, work windows, parsed numbers, and Express status are derived by
pure functions rather than copied onto `Flight`:

```python
from ramp_optimizer import OptimizerConfig, derive_flight_operational_facts

facts = derive_flight_operational_facts(turn, OptimizerConfig())

assert facts.flight_type == "TURN"
assert facts.work_start == datetime(2026, 9, 2, 8, 52)
assert facts.work_end == datetime(2026, 9, 2, 10, 10)
assert facts.arrival_numeric_flight_number == 1428
assert facts.departure_numeric_flight_number == 1814
assert facts.express is False
```

The work-window formulas are:

- arrival-only: `[arrival - arrival_preparation, arrival + arrival_offload)`;
- departure-only: `[departure - departure_work, departure)`;
- turn: `[arrival - arrival_preparation, departure)`.

The default timing values are 10 minutes of arrival preparation, 20 minutes
of arrival offload, and 60 minutes of departure work. These half-open intervals
mean that two windows touching at one endpoint do not overlap. Full `datetime`
arithmetic handles midnight without guessing a date: a 00:30 departure on
September 3 has a default window beginning at 23:30 on September 2. A turn
crossing midnight must explicitly give its departure the following date.

Numeric parsing accepts values such as `1428`, `UA123`, and `OO3550`, preserves
the original display value on `Flight`, and rejects values without a terminal
numeric portion. A movement is Express exactly when its parsed number is
greater than or equal to `express_threshold` (3000 by default), so 2999 is
Mainline while 3000 is Express. Both directional numbers on a turn must resolve
to the same category; mixed Mainline/Express turns are invalid.

Arrival numbers are unique among arrivals, and departure numbers are unique
among departures, using the parsed numeric value. Thus `123`, `UA123`, and
`ua00123` are the same number for uniqueness. The same number may appear once
in each direction. For example, one turn may depart as 1814 and a later turn
may arrive as 1814. Gate is retained only for display; it does not affect
classification, timing, staffing, eligibility, or optimization.

This entire derivation layer is deterministic and solver-independent. It does
not assign employees or impose assignment-conflict constraints.

## Employee-flight eligibility

Eligibility answers whether an employee may legally work a flight; it does not
decide whether that employee should be assigned. The assessment derives the
approved work window from the timing layer and then checks, in deterministic
order, that the employee is enabled, has a shift, has an allowed normalized
role, fits the complete flight window inside one individual shift, and has no
overlapping fixed assignment.

```python
from ramp_optimizer import (
    Employee,
    EmployeeShift,
    OperationalRole,
    OptimizerConfig,
    assess_employee_flight_eligibility,
)

employee = Employee("E001", "Avery Stone")
shift = EmployeeShift(
    "E001",
    datetime(2026, 9, 2, 5),
    datetime(2026, 9, 2, 13),
    OperationalRole.RAMP_AGENT,
)
assessment = assess_employee_flight_eligibility(
    employee,
    (shift,),
    departure_only,
    OptimizerConfig(),
)

assert assessment.eligible
assert assessment.reasons == ()
```

`RAMP_AGENT` is the only role allowed by default. Trainee and possible-support
eligibility default to their `OptimizerConfig` settings and may be overridden
explicitly per call. Leads require the independent `include_leads=True` pass;
the emergency-Lead configuration does not enable them automatically. Non-ramp
and unknown roles remain excluded. Source position text is not consulted.
`PUSH` and `CLOSE_OUT` qualifications also do not filter generic eligibility.
They are separate crew-level requirements for departures and turns, while an
unqualified employee remains a legal candidate whenever the ordinary
eligibility checks pass.

Shift containment is inclusive at both availability boundaries: a flight may
begin exactly at shift start or end exactly at shift end. Separate shifts are
never merged to cover a work window that spans their gap.

A `FixedAssignment` records a manual or locked employee-flight pairing without
inventing a flight ID. A different fixed flight blocks a candidate only when
their half-open work windows overlap. Thus `[08:00, 09:00)` conflicts with
`[08:30, 09:30)` but not with `[09:00, 10:00)`. Fixed assignments are validated
for employee and flight references, duplicates, overlaps, and underlying
employee eligibility.

`build_candidate_assignments()` validates the complete day and configuration,
then returns only eligible, non-fixed employee-flight pairs in stable employee
order followed by flight order. This preprocessing makes illegal assignments
unrepresentable to the solver. It does not construct a solver, choose
assignments, enforce crew-level qualification coverage, or optimize staffing
and fairness.

## Staffing configuration

There is no global maximum-staff setting. Staffing requirements are derived
from the input flight's `heavy` flag without modifying the flight:

```python
from datetime import datetime

from ramp_optimizer import Flight, OptimizerConfig, staffing_requirements_for

config = OptimizerConfig()
normal = staffing_requirements_for(
    Flight(
        arrival_flight_number="UA123",
        arrival_time=datetime(2026, 9, 2, 8),
    ),
    config,
)
heavy = staffing_requirements_for(
    Flight(
        departure_flight_number="UA456",
        departure_time=datetime(2026, 9, 2, 9),
        heavy=True,
    ),
    config,
)

assert normal.maximum == 4
assert heavy.maximum == 5
```

## Staffing, qualification, break, and fairness optimizer

`optimize_flight_assignments()` is the primary limited CP-SAT scheduling entry
point. `optimize_minimum_staffing()` remains available as a backward-compatible
alias. The optimizer creates `x[employee, flight]` Boolean variables only for
eligible, non-fixed candidate pairs. Disabled, unavailable, role-ineligible,
fixed-conflicting, and already-fixed pairs therefore have no decision variable.
Fixed assignments are constants that always contribute to their flight's crew
and qualification coverage.

The hard constraints prevent one employee from working overlapping half-open
flight windows and cap each flight at the maximum returned by
`staffing_requirements_for()`. Fixed staffing above that maximum is rejected by
validation before model construction. No transition time or gate-distance rule
is applied.

Departures and turns each require at least one push-qualified employee and at
least one close-out-qualified employee. One dual-qualified employee may cover
both requirements. Arrival-only flights require neither qualification and
report both coverage fields as `None`. Coverage is derived only from the
authoritative `Employee.qualifications` collection; names, source positions,
normalized roles, flight numbers, and fixed status never grant qualifications.

### Required breaks

Each included Ramp Agent should receive an uninterrupted, flight-free gap of at
least `OptimizerConfig.required_break_minutes`, which defaults to 30 minutes.
Only a gap between two consecutive final assignments counts. Time before the
first assignment and after the last assignment never counts.

For chronological half-open assignments `[work_start_A, work_end_A)` and
`[work_start_B, work_end_B)`, the gap is `work_start_B - work_end_A`. A flight
ending at 09:00 followed by one starting at 09:30 satisfies the default rule;
09:29 or 09:00 does not. Full `datetime` arithmetic applies across midnight.

The bounding assignments must fit within one individual eligible shift. Gaps
between separate shifts are never combined. When an employee has assignments
in multiple shifts, a qualifying gap within any one continuous shift satisfies
the daily requirement. Fixed assignments are evaluated exactly like selected
assignments, and an intervening fixed or selected flight splits the surrounding
idle period into the actual consecutive gaps.

Employee break results use these statuses:

- `NOT_EVALUABLE_BETWEEN_ASSIGNMENTS`: the included Ramp Agent has fewer than
  two final assignments;
- `SATISFIED`: at least one qualifying consecutive gap exists;
- `UNSATISFIED`: at least two assignments exist but no qualifying gap does;
- `NOT_APPLICABLE`: reserved for employees outside the ordinary Ramp-Agent
  population, for whom this optimizer does not create employee results.

An unsatisfied break is recoverable and produces one critical employee-level
`REQUIRED_BREAK_NOT_MET` warning. It never makes the operational day infeasible.
Flight warnings remain first in stable flight order, followed by break warnings
in stable employee order.

### Objective hierarchy

Minimum staffing, qualification coverage, and break coverage are recoverable
rather than hard constraints, so a constrained day still returns its best
partial schedule with critical warnings for every known shortage. The optimizer
uses eleven sequential integer objective stages:

1. Maximize flights reaching minimum staffing.
2. Maximize minimum-staffed departures and turns covering both qualifications.
3. Maximize separate push and close-out coverage on minimum-staffed departures
   and turns.
4. Minimize total minimum-staffing shortfall.
5. Minimize the largest individual minimum shortfall.
6. Minimize known unsatisfied required breaks among included Ramp Agents.
7. Maximize flights reaching preferred staffing.
8. Minimize total preferred-staffing shortfall.
9. Maximize separate qualification coverage on below-minimum partial crews as
   the final operational tie-breaker.
10. Minimize the raw flight-count spread among fairness participants.
11. Minimize the total pairwise absolute flight-count difference among those
    participants.

The exact formulation uses one break stage rather than redundant achieved and
unsatisfied stages. Minimizing known unsatisfied breaks improves employees with
two or more assignments without rewarding extra flights merely to convert a
non-evaluable employee into a satisfied one. Break optimization occurs after
all minimum-staffing and high-priority qualification outcomes are fixed, but
before preferred staffing.

### Raw flight-count fairness

Fairness is evaluated only after all nine staffing, qualification, break, and
preferred-staffing outcomes are fixed. Its population contains each enabled
employee with at least one ordinary `RAMP_AGENT` shift who had at least one
legal assignment opportunity before solving or at least one fixed assignment.
An employee stays in this population even when the final result assigns them
zero flights.
Disabled employees, Leads excluded from the ordinary pass, non-ramp and unknown
roles, employees without a shift, and employees with neither an opportunity nor
a fixed assignment are excluded. Population order follows the input employee
order.

The model counts each selected or fixed aircraft movement once. Arrival-only,
departure-only, and turn movements therefore each add one; a turn's two flight
numbers do not make it two assignments. For every participant, the exact count
is the fixed-assignment count plus selected candidate variables. Exact maximum
and minimum equalities define the raw count spread. Stage 10 minimizes that
spread, then stage 11 minimizes the sum of absolute count differences over all
unordered employee pairs, which resolves avoidable middle-of-the-distribution
imbalance without floating-point solver expressions. Zero- and one-participant
populations both have a zero spread and zero pairwise difference.

Idle time and utilization are not objectives. Fairness cannot add staffing
beyond the already-fixed preferred outcome, and it never makes an illegal
assignment. Raw counts are not normalized for shift length or opportunity
count, and Express flights, three-person crews, duration, direction, heavy
status, and qualifications carry no fairness weight in this milestone.

Each proven optimum is fixed before solving the next stage. Sequential solves
preserve true priority without arbitrary giant weights, and all stages share
one total time budget. Qualification stages therefore cannot reduce the maximum
achievable number of minimum-staffed flights, and qualified fragments cannot
outrank operationally viable crews. For two simultaneous flights and five
available employees, the result remains `3 + 2`: one flight reaches the
three-person minimum, then the remaining two employees reduce total shortage
on the other flight. Such a partial schedule can still be mathematically
`OPTIMAL`. If a later objective times out or returns `UNKNOWN`, the last known
feasible schedule is retained and returned with non-optimal objective metadata.

```python
from ramp_optimizer import optimize_flight_assignments

result = optimize_flight_assignments(day, OptimizerConfig())
```

Results include stable assigned and fixed employee IDs, staffing limits and
shortfalls, flight timing and classification, qualification coverage, objective
values with proof status, warnings for known staffing, qualification, and break
shortages, and runtime. Qualification warnings use the structured
`PUSH_QUALIFICATION_NOT_MET` and `CLOSE_QUALIFICATION_NOT_MET` codes and coexist
with minimum-staffing and employee break warnings.

`OptimizationResult.employee_results` contains ordinary enabled Ramp Agents in
stable employee order. Each result reports chronologically ordered assignments,
raw total, Mainline, Express, and three-person-flight counts, plus break status.
The raw total is the count used by Milestone 7 fairness for employees in its
population; Mainline, Express, and three-person counts remain descriptive.
`longest_consecutive_streak` and `adjusted_workload` are explicitly `None`
because their later milestones have not been implemented. Leads and unrelated
roles are excluded from ordinary employee results, and emergency Lead
configuration does not enable a second solver pass.

`OptimizationResult.fairness_metrics` reports the fairness population size,
total and average assignment counts, highest and lowest counts, and their
spread. These values are reconstructed from the final assignments. Its
`maximum_consecutive_streak` and `adjusted_workload_spread` fields remain
explicitly `None`.

This optimizer is not operationally complete. It does not implement Milestone
8 shift-length adjustment, Milestone 9 Express and three-person workload
adjustment, Milestone 10 consecutive-flight penalties, Milestone 11 team
continuity, Milestone 12 emergency Lead solving, or later reporting and
integration milestones.

## TeamWork schedule import

```python
from ramp_optimizer import import_teamwork_schedule

result = import_teamwork_schedule("synthetic-schedule.xlsx", roster=(employee,))
```

The importer finds the `Schedule` worksheet and discovers its header row by
normalized column names. It requires `Date`, `Position`, `Employee`, `Start`, and
`End`; other known TeamWork columns are optional.

Important import rules:

- overnight shifts are represented with next-day end datetimes;
- the calculated start/end duration is authoritative, not `Hours`;
- `Hours` differences greater than 0.10 hours produce a warning;
- zero-length shifts and shifts longer than 18 hours are rejected by default;
- blank employees and configured vacancy placeholders become distinct vacancy
  records, never fictional employees;
- unmatched and ambiguous roster names produce privacy-conscious warnings;
- note contents are discarded and represented only by import provenance's
  `notes_present` flag;
- blank `Break` values do not imply a break was taken;
- unknown roles remain ineligible and produce a warning.

Position mapping is explicit configuration. Defaults classify ordinary Ramp
Agents and Leads, trainees, Ramp Instructors, customer service, and cabin
cleaning. `Bagroom`, `Airline-Specific Ramp`, and `Operations` deliberately map
to `UNKNOWN` pending business confirmation.

## Development

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
```

All tests and workbook fixtures use fictional data.
