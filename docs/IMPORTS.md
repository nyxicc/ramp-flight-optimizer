# Reviewed employee-schedule imports (Milestone 18A)

Milestone 18B extends this lifecycle for flights and confirmed-input composition;
see [Flight imports](FLIGHT_IMPORTS.md). The employee contract below remains supported.

Milestone 18A adds a synchronous, review-first TeamWork `.xlsx` import workflow.
Uploading never creates optimizer input automatically. A client uploads a workbook
and authoritative roster, reviews a persisted preview, submits explicit corrections,
and confirms the current revision. Confirmation creates an immutable employee-only
operational-day snapshot. No flights or qualifications are inferred, and none of
these operations invoke optimization.

Flight-log parsing is deliberately deferred to **Milestone 18B**. Implementation
requires inspection of the real flight-log structure and creation of a sanitized,
fictional structural fixture first. No flight-log type, columns, worksheet names,
PDF/OCR support, or format assumptions are introduced here.

## Setup and architecture

```bash
python -m pip install -e ".[api,dev]"
python -m alembic upgrade head
```

The only added dependency is `python-multipart` in the API extra, for multipart
form uploads. `openpyxl` remains the existing workbook dependency.

```text
FastAPI schemas / routes / bounded multipart body
  -> ImportService (immutable records, lifecycle, explicit transactions)
     -> TeamWorkAdapter -> existing teamwork_import parser -> domain validation
     -> ImportRepository protocol -> SQLImportRepository -> SQLAlchemy
                                  -> existing operational-day repository
```

`ramp_optimizer_imports` does not depend on FastAPI, Pydantic, SQLAlchemy, or the
persistence package. Its repository and adapter protocols are explicit extension
seams. An adapter parses, revalidates, applies corrections, and produces a domain
snapshot. The initial adapter is TeamWork only; future adapters can reuse the
transaction/lifecycle infrastructure and add their own typed reviewed payloads.
There is no dynamic plugin discovery. The optimizer never imports these adapters.

The existing path-based `import_teamwork_schedule` function remains available and
now also accepts a binary file-like object. Existing `shifts`, `shift_records`,
vacancies, and issue vocabulary are retained. An additive `review_rows` tuple keeps
safe values even when the low-level parser cannot accept a shift. Text dates and
times accept ISO forms (`2035-04-15`, `05:00`); locale-ambiguous strings are rejected.

## HTTP contract

All resource IDs are UUIDs. All import endpoints use the existing error envelope.

| Method | Path | Result |
|---|---|---|
| POST | `/api/v1/imports/teamwork-employee-schedule` | `201`, import and initial preview |
| GET | `/api/v1/imports/{import_id}` | `200`, current metadata and preview |
| GET | `/api/v1/imports/{import_id}/preview` | `200`, same metadata and persisted preview |
| POST | `/api/v1/imports/{import_id}/corrections` | `200`, new immutable revision |
| POST | `/api/v1/imports/{import_id}/confirm` | `200`, confirmed import with day linkage |

The upload is `multipart/form-data` with a file part **`workbook`** and a text part
**`metadata`** containing JSON. It is not a JSON file part. Metadata is limited to
256 KiB. Its shape is:

```json
{
  "operational_date": "2035-04-15",
  "roster": [
    {"employee_id": "SYN001", "name": "Fictional Avery", "qualifications": ["PUSH"], "enabled": true}
  ],
  "config": {"solver_time_limit_seconds": 30}
}
```

`operational_date` and `roster` are required. The optional `config` uses the existing
optimizer configuration schema and defaults. Roster identities and configuration
are validated before persistence. The roster is authoritative for stable IDs,
names, enabled state, and qualifications. Matching uses the existing normalized
name policy; it never generates an ID from a name or creates permanent employees.
All roster employees are retained in the confirmed snapshot, even without a shift.

The response includes upload SHA-256, sanitized filename, media type, actual size,
schema version, UTC timestamps, status, issue counts, accepted shift/vacancy/
unresolved row counts, and eventual operational-day linkage. `preview` includes:

- Revision, explicit operational date, and detected source dates.
- Authoritative roster and reviewed day-specific employee values.
- Ordered rows with UUID row IDs, source rows/dates, matched employee IDs, parsed
  shift times, safe imported Hours, normalized roles, vacancy/exclusion flags, match status, SwapBoard,
  formula/missing-field indicators, and `notes_present` only.
- Matched employee IDs and unmatched, ambiguous, and vacancy row IDs.
- Current issues and immutable original parser issues (`source_issues`).
- Confirmation eligibility and blockers; optimization eligibility and blockers.
- Resolved optimizer configuration, role mappings, and discarded-field indicators.

Row IDs are UUIDv5 of the import UUID and source worksheet row. They stay stable
across revisions, are distinct between imports, and never use display names.
Meaningful list order is preserved. Preview contents are deterministic for identical
bytes, roster, and settings, excluding resource identities and UTC audit timestamps.
GET never reparses the workbook.

## Lifecycle and corrections

The persisted states are `REVIEW_REQUIRED`, `READY_TO_CONFIRM`, `REJECTED`, and
`CONFIRMED`. Parsing completes before the initial record is committed, so there is
no externally visible intermediate upload state. Structural parser failures create
terminal `REJECTED` records. Invalid file envelopes/archives return a sanitized
error before creating a resource. Recoverable row problems remain reviewable.

Corrections may move either reviewable state to `REVIEW_REQUIRED` or
`READY_TO_CONFIRM`. Only `READY_TO_CONFIRM` can transition to `CONFIRMED`.
`REJECTED` cannot be edited; upload a corrected workbook. `CONFIRMED` is terminal.
Warnings such as `HOURS_DURATION_MISMATCH` or an unknown, non-ramp-eligible role
may remain. An unmatched/ambiguous active employee, missing position, unresolved
required formula, invalid time/date, or domain validation error blocks confirmation.

```json
{
  "revision": 1,
  "corrections": [
    {
      "row_id": "11111111-1111-4111-8111-111111111111",
      "employee_id": "SYN001",
      "start": "2035-04-15T05:00:00",
      "end": "2035-04-15T13:00:00",
      "normalized_role": "RAMP_AGENT",
      "excluded": false,
      "vacancy": false,
      "enabled": true,
      "qualifications": ["PUSH", "CLOSE_OUT"]
    }
  ]
}
```

Use the row ID returned in the preview. Omitted fields retain their reviewed
values; explicit nulls, empty patches, duplicate targets, invalid enums, unknown
employee IDs, and contradictory overrides are rejected. `qualifications: []`
explicitly clears qualifications. Enabled/qualification edits apply to the matched
employee throughout this operational-day snapshot; they do not edit the original
roster. These day-level overrides persist even if the targeting row is later
reassigned or excluded. Overrides require an active matched row, and conflicting
edits for the same employee/property in one request are rejected together.

`vacancy: true` clears the row's employee link. Restoring an occupied row requires
`vacancy: false` and an authoritative employee ID (together or in a later revision).
Excluded rows produce no shifts and do not block confirmation. Duplicate occupied
rows start excluded, matching the existing parser policy; restoring one revalidates
duplicate/overlap rules. Duplicate vacancies remain separate vacancies. Hours
mismatch warnings are recalculated from the retained numeric Hours value after
shift edits; original source warnings remain available in the audit preview.

Syntactically valid shift corrections are persisted as a new review revision even
if validation finds errors; all current issues appear in that preview. Bad target
references/structural corrections return `422` without a new revision. Correcting
an unavailable date/time interval requires explicit start and end datetimes; no
invalid raw cell text is retained. Corrected start dates must equal the explicit
operational date, end must follow start, and maximum duration is the existing
TeamWork default of 18 hours. Overnight shifts are supported. Domain checks still
enforce whole-minute duration, datetime awareness, duplicate and overlapping shifts.

Every correction and confirmation requires the current integer revision. A stale
revision returns `409 IMPORT_REVISION_CONFLICT`. A conditional database write
claims the revision before reading, serializing competing writers. Corrections
append immutable canonical preview/correction JSON; they never overwrite revision 1.

## Confirmation and optimization eligibility

Send `{"revision": 2}` to `/confirm` after reviewing revision 2. The server
revalidates eligibility and calls the existing domain validator. In one transaction
it creates the immutable operational day, stores the unique foreign-key linkage,
and marks the import confirmed. Failure rolls back all three operations. Repeating
confirmation with the same current revision returns the same resource/linkage.
No optimization is invoked.

An employee schedule with no flights is a valid snapshot. It reports
`optimization_eligible: false` and `FLIGHT_DATA_REQUIRED`. Attempting to optimize
the stored imported snapshot returns `409 FLIGHT_DATA_REQUIRED`. This is distinct
from malformed employee data. Existing standalone Phase 1 empty-day semantics
remain unchanged. Operational-day summaries now add optimization eligibility and
blocker fields, with existing fields unchanged.

There is no mutable draft. To combine reviewed employees with separately supplied
structured flights, create a **new** operational day through the existing JSON
API, copying the confirmed employee/shift values and supplying real structured
flight data. The original imported snapshot cannot be changed; a broader linked
revision workflow is deferred.

## File safety, limits, and retention

Only `.xlsx` is supported. `.xls`, PDF, CSV, unsupported MIME types, encrypted ZIP
members, OLE/encrypted workbooks, malformed ZIP/XML, and renamed arbitrary data are
rejected. Accepted MIME types are the standard XLSX MIME type,
`application/octet-stream`, `application/zip`, and `application/x-zip-compressed`.
A missing MIME type is treated as octet-stream. Extension and MIME checks do not
replace ZIP/signature and workbook checks.

| Setting (`RAMP_IMPORT_` prefix) | Default |
|---|---:|
| `MAX_BYTES` | 10 MiB |
| `MAX_MEMBERS` | 256 |
| `MAX_UNCOMPRESSED_BYTES` | 64 MiB |
| `MAX_MEMBER_BYTES` | 16 MiB |
| `MAX_COMPRESSION_RATIO` | 100 |
| `MAX_ROWS` | 5,000 per worksheet |
| `MAX_COLUMNS` | 64 per worksheet |
| `MAX_CELLS` | 100,000 physical cells per worksheet |

Positive-integer environment overrides are read when constructing the API.
Tests and embedded clients can inject `UploadLimits`. Limits do not belong to
`OptimizerConfig`. The outer multipart body is bounded to `MAX_BYTES + 256 KiB`
(including metadata and framing), before multipart parsing. The file is then read
in chunks of at most 64 KiB, stopping at `MAX_BYTES + 1`, computing SHA-256 in that
same bounded read. A client-provided size is never trusted.

ZIP inspection validates member count, declared and actual sizes, compression
ratios, CRC, paths, required package members, and worksheet coordinates/dimensions
before openpyxl. XML worksheet bounds are checked by content even for renamed
internal parts, so client-controlled ZIP member names cannot bypass them. No
archive tree is extracted. DTD/entity declarations and macro
binary parts are rejected. Workbooks load read-only with `data_only=False` and
`keep_links=False`; openpyxl does not calculate formulas, run macros, or access
external resources. Required formula cells always require reviewed replacements,
even if a cached result exists. Formula text/objects become a marker, never stored
formula text. Formulas in ignored columns do not execute.

The workflow retains **no workbook bytes in the database or permanent file store**.
Starlette may temporarily spool its bounded multipart file using OS-managed random
temporary files; request/form cleanup and explicit upload closure remove those
resources, including failures. The workbook parser itself uses an in-memory byte
stream. Client filenames are never filesystem paths: directory components are
removed and control characters replaced for bounded display metadata.

Only safe parsed values, source issue codes/messages, and reviewed values persist.
Notes reduce to a boolean before review; unmatched names never enter messages or
persisted preview data. Authoritative roster names necessarily appear in review
data and the confirmed snapshot. No workbook bytes, raw exceptions, paths, SQL,
tracebacks, or note text appear in API errors or application logs.

These are conservative resource bounds, not a full malware scanner or production
upload system. XML/container checks and openpyxl still consume CPU/memory within
the configured bounds. Deployments need transport limits, authentication, and
operational controls separately; this milestone remains a local development API.

## Persistence, hashes, and error codes

Migration `20260909_0002` follows the published Milestone 17 migration unchanged.
`import_jobs` stores safe audit metadata, closed status/type values, current
revision, issue/count summaries, and a unique optional operational-day foreign key.
`import_revisions` has a composite `(import_id, revision)` primary key, parent
foreign key, UTC creation time, canonical JSON, and a SHA-256 integrity hash.
Confirmation linkage cannot reference a missing day, cannot be shared by imports,
and must agree with terminal status and confirmation timestamp. No tables are
created on application import.

JSON uses sorted keys, compact separators, sorted qualification sets, preserved
row ordering, ISO dates/times, public enum strings, and rejects nonfinite numbers.
Reads verify preview hashes and stored counters. The upload hash covers original
file bytes; the preview hash covers a particular reviewed revision. **Duplicate
uploads always create separate import resources**, even with identical bytes,
because roster/configuration/review outcomes may differ. Filename is never an
idempotency key.

`alembic downgrade 20260908_0001` removes import records/revisions while retaining
operational days, including already confirmed snapshots. `downgrade base` removes
all persistence tables. Upgrade, downgrade, clean upgrade, schema parity, SQLite
foreign keys, linkage uniqueness, and concurrent idempotency are tested.

| HTTP | Codes/conditions |
|---:|---|
| 413 | `UPLOAD_TOO_LARGE`, `IMPORT_METADATA_TOO_LARGE` |
| 415 | `UNSUPPORTED_FILE_TYPE` |
| 422 | `UPLOAD_EMPTY`, `UPLOAD_FILENAME_REQUIRED`, `INVALID_XLSX_CONTAINER`, `SUSPICIOUS_XLSX_ARCHIVE`, `REQUEST_VALIDATION_ERROR`, `INVALID_IMPORT_CORRECTIONS` |
| 404 | `RESOURCE_NOT_FOUND` |
| 409 | `IMPORT_REVISION_CONFLICT`, `IMPORT_ALREADY_CONFIRMED`, `IMPORT_NOT_CONFIRMABLE`, `INVALID_IMPORT_TRANSITION`, `FLIGHT_DATA_REQUIRED` |
| 500 | Existing sanitized database/integrity/internal error envelopes |

Preview issues reuse parser/domain codes, including `MISSING_SCHEDULE_WORKSHEET`,
`MISSING_REQUIRED_HEADERS`, `DUPLICATE_SCHEDULE_ROW`, `UNMATCHED_EMPLOYEE`,
`AMBIGUOUS_EMPLOYEE`, `UNKNOWN_POSITION`, `HOURS_DURATION_MISMATCH`,
`IMPLAUSIBLY_LONG_SHIFT`, and domain duplicate/overlap codes. New current-review
issues include `FORMULA_VALUE_UNAVAILABLE`, `INVALID_SHIFT_TIME`,
`INVALID_SHIFT_RANGE`, and `INVALID_DATE`. Each issue has severity, safe message,
source row/field when applicable, a confirmation-blocking flag, and optional guidance.
Original parser errors stay in `source_issues` after correction; eligibility uses
revalidated current `issues` only.

## Verification

`tests/import_fixtures.py` generates fictional workbooks in memory; the established
parser tests also generate fictional workbooks in temporary directories. No actual
employee workbook is committed. Tests exercise API envelope handling, all review
operations, formula policies, native/text/overnight values, unresolved matches,
vacancies, role/qualification boundaries, transactional rollback, concurrent edits
and confirmations, migration constraints, canonical persistence and tamper checks,
bounded reads, archive attacks, temporary-resource closure, and absence of a
distinctive fictional note secret from parser results, responses, database data,
errors, and captured logs. Static dependency tests and optimizer spies protect the
domain and optimization boundaries.

The pre-change baseline was **742 passed, 0 failed, 0 skipped in 204.37 seconds**
with Python 3.12.0, pytest 8.4.2, OR-Tools 9.15.6755. See `IMPORTS_VALIDATION.md`
for the completed milestone verification record.
