# Milestone 18B: reviewed daily flight logs

Flight imports extend the Milestone 18A lifecycle. The optimizer consumes the
existing `Flight` and `OperationalDay` dataclasses, never an uploaded workbook.
Employee import endpoints and immutable employee snapshots remain supported.

## Supported structure

The inspected format is XLSX with a title/date above a single header row. Arrival
and departure columns are paired on the same row. The supported ordered header is:

| Arrival side and auxiliary columns | Departure side and auxiliary columns |
| --- | --- |
| FLT #, CTY, ETA, NOTES, OO, A/C #, CONF | FLT #, CTY, ETD, MST, STATUS, GATE |

The inspected workbook has one visible worksheet, string-valued cells, a merged
title, and a merged late-arrival divider. It has no formulas, hidden rows/columns,
or comments. No operational row data is reproduced here. Sheet names are not used
for detection: exactly one matching header within the first 50 rows is required.
Header whitespace/case is normalized, but order, duplicate directional labels,
and all thirteen columns are significant. Multiple matches or changed layouts
produce `UNSUPPORTED_FLIGHT_LOG_LAYOUT` (422). An additional populated column
beside a data row is rejected. Blank rows and single-cell `NOTE:`, `TOTAL`, and
`SUBTOTAL` decorations are skipped; other nonempty malformed rows require review.
Hidden data, if supplied in a future workbook, is read as data rather than omitted.

Each main-section row represents one turn when both directional sides exist.
Arrival-only and departure-only rows are supported; a partially populated side
requires its missing number/time to be corrected. No matching across rows occurs.
Below a `LATE ARRIVALS` divider or when explicitly marked terminating, a row
without a departure number is arrival-only. An accompanying onward ETD is ignored
without a warning; no onward flight number
is inferred from a status suffix. A reviewer can explicitly add a departure
number and timestamp through corrections if that movement belongs in demand.

## Mapping and time policy

The upload requires an IANA airport time zone, an explicit operational-day start
clock time, an operational date, and a planning basis (`ESTIMATED` or `SCHEDULED`).
There is no machine-local-time fallback. ETA/ETD are the only selected planning
fields. The `tzdata` dependency supplies IANA zones on systems without a zone database.
The operator must verify what those labels mean in the producing system;
the workbook alone does not prove whether they are estimates or schedule values.
The declared basis is retained in each immutable preview. Actual times are not
mixed in and no auxiliary timing column overrides ETA/ETD.

Time-only values before the declared day-start cutoff use the next calendar date.
Explicit datetimes preserve their supplied date and are converted to the airport
zone. A reversed time-only turn rolls departure forward one calendar day with a
warning. Equal times or explicitly reversed datetimes remain invalid. Explicit
dates outside the selected or adjacent calendar dates block confirmation.
Decorative workbook-title dates are ignored. The selected schedule day is the
single date used to interpret time-only flight rows.
Changing the operational date creates an audited revision and **does not shift
any flight timestamp**; timestamp corrections are separate, explicit changes.

Native Excel times/datetimes, numeric serials using the workbook's date epoch,
HH:MM[:SS], AM/PM time strings, and ISO datetimes are accepted. Numeric values are
interpreted as Excel serial values, not HHMM shorthand. Invalid values are not
guessed. DST gaps and ambiguous wall times require an explicit datetime offset.
Normalized fixed-offset datetimes preserve elapsed-time arithmetic through DST.
Combining inputs localizes naive employee shifts in the flight import's airport
zone; ambiguous employee wall times reject composition and require a corrected
employee import. No confirmed snapshot is altered to perform this localization.

Flight-number normalization removes whitespace, uppercases airline prefixes, and
removes leading numeric zeroes. Integral numeric cells are supported. Canonical
numbers use the domain parser's supported letters-plus-digits representation.
Suffix letters, multiple codeshare numbers, malformed numbers, and missing values
require correction. Raw formula text and malformed source strings are discarded.
Canonical differences are recorded as `normalized_fields`. Duplicate identity
follows domain rules (including numeric identity across prefixes): exact or
conflicting repetitions block until explicitly corrected or excluded.

CTY maps to origin/destination (uppercase three-letter codes). Gate whitespace and
case are normalized; gates are optional. Origin/destination/status are review
metadata around `Flight`; they do not introduce a competing flight model.
`A/C #` is an identifier, not a supported aircraft-to-heavy mapping. OO, CONF, and
MST are not interpreted as staffing, aircraft class, or planning times.

Heavy defaults to false without a warning. A supervisor must explicitly check
Heavy to request heavy-flight staffing. An explicit boolean correction
records the reviewed value. Express, flight type, staffing, and work windows
always use existing domain logic. Boundary tests call the authoritative classifier
and verify that equality with the configured Express threshold is not Express.

## Status and review policy

Blank status means normal. TERM (optionally followed by digits) normalizes to
TERMINATING; its numeric suffix is discarded. A terminating row with departure
demand requires review. AOG is treated as normal and does not affect optimization.
Unrecognized statuses still require review.
CANCELLED/CANCELED/CXL are explicitly supported and excluded from demand even if
`excluded` is false. Restoring such demand requires an explicit status correction.
No delayed, diversion, ferry, or charter interpretation is guessed from notes.

The existing severity enum remains unchanged: ERROR and FATAL block, WARNING does
not. Row results provide source row, canonical domain fields, source-side presence,
normalized metadata, current issues, correctable fields, eligibility/exclusion,
and derived movement type, Express, and work window when derivable. Normalization
notices are metadata, not a new severity. Issues never include raw source rows.
Cancelled/excluded rows remain reviewable audit data but do not create demand.

Corrections support directional flight numbers/timestamps, origin, destination,
gate, status, heavy, exclusion, and the import operational date. Invalid patch
types/fields are rejected. Each accepted request appends a revision, stores
original normalized values and reviewed changes, and reruns domain validation.
There is no actor identity in the current unauthenticated persistence model.
Confirmed imports are immutable: replacements require a new import and snapshot.
Revision GET exposes earlier review state; stale correction/confirmation IDs
return 409. Retrying confirmation of the current confirmed revision returns the
same snapshot. Snapshot creation and confirmation share one transaction.

## API flow (fictional examples)

All paths below are relative to `/api/v1`:

| Method/path | Purpose |
| --- | --- |
| POST `/imports/daily-flight-log` | Multipart `workbook` plus JSON `metadata` |
| GET `/imports/{id}` or `/imports/{id}/preview` | Current structured review |
| GET `/imports/{id}/revisions/{revision}` | Immutable historical review |
| POST `/imports/{id}/corrections` | Employee or flight correction request |
| POST `/imports/{id}/confirm` | Confirm `{ "revision": 2 }` |
| POST `/imports/combine` | Combine two confirmed imports into a new day |
| GET `/imports/operational-days/{day_id}/readiness` | Confirmed-input readiness |
| GET `/operational-days/{day_id}` | Retrieve normalized input including flights |
| POST `/operational-days/{day_id}/optimizations` | Optimize stored domain input |

Fictional flight metadata:

```json
{
  "operational_date": "2032-06-15",
  "time_policy": {
    "airport_timezone": "America/Chicago",
    "operational_day_start": "03:00:00",
    "planning_basis": "ESTIMATED"
  }
}
```

Flight correction (replace the fictional UUID with the returned row ID):

```json
{
  "revision": 1,
  "flight_corrections": [{
    "row_id": "00000000-0000-4000-8000-000000000001",
    "arrival_flight_number": "ZZ101",
    "arrival_time": "2032-06-15T10:00:00-05:00",
    "heavy": false
  }]
}
```

Combine request uses `employee_import_id` and `flight_import_id` UUIDs. Both must
be confirmed and have identical operational dates and optimizer configuration.
The unique pair is idempotent and retains both original snapshots. Readiness
requires both confirmed inputs, at least one shift, and at least one included
flight. It describes the selected immutable snapshot, not every unrelated draft
import on that date. Unresolved blocking issues cannot enter a confirmed input.
Employee-only and flight-only imported snapshots cannot be optimized. Existing
structured-input optimization endpoints retain their original behavior.

Uploads return 201; review/correction/confirmation/composition return 200. Unknown
resources/revisions return 404, stale/immutable/unconfirmable/conflicting inputs
409, unsupported file types 415, oversized bodies 413, invalid workbook/layout or
correction values 422. Errors retain the established sanitized error envelope.
Unexpected parser failures return `FLIGHT_LOG_PARSE_FAILED` (500) without source details.

## Storage, migration, and security

Migration `20260909_0003` extends the import-type constraint and adds
`import_compositions`, with foreign keys to immutable days and imports and a
unique employee/flight import pair. Existing revision JSON gains optional fields;
old employee JSON remains readable. No workbook binary column is added. SQLite
parent-table replacement preserves revision history with a temporary relational
copy, avoiding foreign-key cascade loss. Clean/employee-only downgrade is tested;
downgrade with flight history deliberately refuses rather than deleting it.

Only normalized review data, hashes, counts, and provenance are persisted. Flight
uploads retain a generic display filename. Raw workbook bytes are discarded;
notes and auxiliary operational identifiers are not retained. Upload resources
close on success and failure. No parser temporary files or user-controlled output
paths exist; multipart spool files are owned and closed by the request lifecycle.
Formulas are never evaluated or trusted via cache; formula planning fields require
explicit corrections. Macros, scripts, embedded objects, and links are not run.

Default limits: 10 MiB upload; 16 worksheets; 5,000 rows per sheet; 64 columns;
100,000 cells across the archive; 256 ZIP members; 64 MiB expanded total; 16 MiB
per member; 100:1 maximum compression ratio. The outer multipart allowance is
256 KiB beyond upload size. Existing `RAMP_IMPORT_*` environment overrides apply,
including `RAMP_IMPORT_MAX_WORKSHEETS`. Archive paths, duplicate/encrypted members,
DTD/entities, macros, dimensions, CRC, and expansion are checked before parsing.

## Limitations and adapting the format

The workbook does not establish an airport zone, the source system's time basis,
the auxiliary abbreviations, or a heavy mapping. These remain operator-supplied
policy or explicit review; this milestone does not assert an operational answer.
There is no cross-row pairing, suffix/codeshare expansion, inferred onward flight,
or interpretation of arbitrary irregular-operation notes. A timezone or cutoff
change requires a new upload so raw cells are reparsed under one explicit policy.

For a future layout, add a dedicated adapter and sanitized synthetic workbook
tests. Reuse normalization, revision persistence, confirmation, and the domain
classifier/validator. Do not add workbook parsing to API handlers or the optimizer,
and do not copy an operational workbook into fixtures or documentation.
