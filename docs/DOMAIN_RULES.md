# Domain rules

This document summarizes the implemented Phase 1 rules. `OptimizerConfig` makes
the principal quantities configurable; values below are the defaults.

## Operational-day input

An `OperationalDay` contains one date and immutable tuples of employees, shifts,
flights, and fixed assignments. Employee IDs are compared after trimming and
case-folding and must be unique. A shift or fixed assignment must reference an
employee in the same day. All datetime values in a day must be consistently
timezone-aware or consistently timezone-naive; the public demo and benchmark
scenarios use aware datetimes.

Validation aggregates issues instead of stopping at the first malformed record.
It checks configuration bounds, data types, complete flight sides, flight-number
syntax, datetime compatibility, shift collisions, directional uniqueness, work
windows, fixed-assignment legality and conflicts, and integer bounds used by the
solver.

## Flight identity and number parsing

`Flight` is a frozen value object. Its arrival number/time, departure number/time,
gate, and heavy flag comprise the movement value referenced by a
`FixedAssignment` and returned in results.

Supported flight-number text is optional ASCII letters followed by terminal
digits, with surrounding whitespace ignored. The numeric suffix is used for
classification and directional uniqueness. Therefore `123`, `UA123`, and
`ua00123` collide as arrival numbers if used twice. Arrival and departure
namespaces are separate, so the same numeric value may appear once in each
direction.

## Movement types and scheduled times

ETA and ETD are scheduled times in this model.

| Movement | Required sides | Default half-open work window |
|---|---|---|
| Arrival-only | arrival number and ETA | `[ETA - 10 min, ETA + 20 min)` |
| Departure-only | departure number and ETD | `[ETD - 60 min, ETD)` |
| Turn | both complete sides; ETD later than ETA | `[ETA - 10 min, ETD)` |

The half-open convention means adjacent windows can touch without overlapping.
Turn arrival and departure times must have compatible awareness and may cross
midnight when the dates are explicit.

## Service class and heavy flights

The parsed numeric flight number determines service class:

- Mainline: number at or below `express_threshold` (`3000` by default);
- Express: number strictly greater than `express_threshold`.

Thus `3000` is Mainline and `3001` is Express. Both legs of a turn must resolve
to the same class; a mixed Mainline/Express turn is invalid rather than assigned
one side's class.

Every movement has minimum staffing `3`. Preferred staffing is `4` for an
ordinary movement and `5` when `heavy=True`; preferred is also the maximum.
Express and exact-three-person multipliers affect adjusted workload, not legal
staffing counts.

## Roles, shifts, and eligibility

An ordinary assignment candidate must satisfy all of the following:

- the employee is enabled;
- the employee has a matching shift;
- one eligible shift contains the entire work window—separate shifts are never
  merged across a gap;
- the shift's normalized role is allowed by policy;
- the work window does not overlap another fixed assignment for that employee.

Ramp Agents are eligible by default. Leads are excluded from normal Agent
staffing and enter only when the relevant explicit policy or emergency pass is
active. Trainee and possible-support roles are disabled by default and require
their own configuration flags. Non-ramp and unknown roles are not assignment
candidates. Qualifications do not determine basic eligibility; they are modeled
as staffing-quality requirements so shortages remain visible.

One employee cannot be assigned to overlapping work windows. Each fixed
assignment must be legal, is always honored, counts toward staffing and workload,
and cannot be duplicated or conflict with another fixed assignment.

## Qualifications

Departure-only movements and turns require the minimum-staffed team to include at
least one employee with `PUSH` and at least one with `CLOSE_OUT`. One person may
hold both. Arrival-only movements do not require these qualifications. The
lexicographic hierarchy first tries to make minimum-staffed flights fully
qualification-compliant, then preserves partial individual qualification coverage
where a full minimum crew cannot be formed.

## Protected breaks

The default required break is a gap of at least 30 minutes between two consecutive
assignments for the same included employee, with both assignments contained by
one eligible shift. Breaks are evaluated only for employees with at least two
assignments. They are reported as `SATISFIED`, `UNSATISFIED`,
`NOT_EVALUABLE_BETWEEN_ASSIGNMENTS`, or `NOT_APPLICABLE`; the objective minimizes
known unsatisfied requirements without manufacturing assignments merely to make
an employee evaluable.

## Fairness, streaks, workload, and continuity

Fairness first compares raw flight counts across employees who can participate,
using the range and then all pairwise differences. A gap of at least 40 minutes
resets a consecutive-flight streak; boundaries are computed within eligible shift
records rather than merged across shifts.

Adjusted workload uses exact scaled integers internally. Default public units are
`1.00` for Mainline, `0.80` for Express, and a `1.15` multiplier when a movement
is staffed by exactly three people. Shift-adjusted flight-count deviation is
optimized before adjusted-workload balance, so longer and shorter scheduled
availability can receive proportionate counts without losing earlier guarantees.

Continuity considers plausible chronological flight pairs within the default
120-minute horizon and maximizes retained employees only after all prior
operational and fairness stages. It never permits an overlap or damages a fixed
higher-priority optimum.

## Partial schedules and emergency Leads

Minimum and preferred staffing are soft operational targets inside a prioritized
hierarchy, so valid shortages produce a usable partial schedule with structured
warnings instead of making the mathematical model infeasible. Emergency Lead
recovery is opt-in and explicit. When an adopted Lead assignment supplies minimum
staffing or required qualification coverage, the result identifies the employee,
flight, reason, warning, attempt, and recovery disposition.
