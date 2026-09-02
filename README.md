# Ramp Team Flight Optimizer

A portfolio project for generating ramp-team flight assignment recommendations
from synthetic operational data. It is not affiliated with, approved by, or used
by United Airlines or any other airline.

## Current scope

The repository currently provides:

- immutable employee, shift, flight, and result domain models;
- structured input validation;
- a privacy-conscious importer for TeamWork daily schedule exports;
- assignment-eligibility checks over separate availability intervals;
- deterministic flight classification, work-window, and service-category
  derivation;
- pure half-open interval overlap behavior.

The CP-SAT scheduling engine is not implemented yet.

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
shift values needed by eligibility and the future optimizer.

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
among departures, using case-insensitive normalized comparisons. The same
number may appear once in each direction. For example, one turn may depart as
1814 and a later turn may arrive as 1814. Gate is retained only for display; it
does not affect classification, timing, staffing, eligibility, or optimization.

This entire derivation layer is deterministic and solver-independent. It does
not assign employees or impose assignment-conflict constraints.

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
