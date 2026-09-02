# Ramp Team Flight Optimizer

A portfolio project for generating ramp-team flight assignment recommendations
from synthetic operational data. It is not affiliated with, approved by, or used
by United Airlines or any other airline.

## Current scope

The repository currently provides:

- immutable employee, shift, flight, and result domain models;
- structured input validation;
- a privacy-conscious importer for TeamWork daily schedule exports;
- assignment-eligibility checks over separate availability intervals.

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

## Staffing configuration

There is no global maximum-staff setting. Staffing requirements are derived
from the input flight's `heavy` flag without modifying the flight:

```python
from ramp_optimizer import Flight, OptimizerConfig, staffing_requirements_for

config = OptimizerConfig()
normal = staffing_requirements_for(Flight("UA123"), config)
heavy = staffing_requirements_for(Flight("UA456", heavy=True), config)

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
