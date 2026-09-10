# Version 1 API contract

Reviewed flight uploads, revision history, and input composition are documented in
[Flight imports](FLIGHT_IMPORTS.md), alongside the existing employee import flow.

Phase 2 Milestones 16 and 17 expose the completed Phase 1 optimizer through a small
FastAPI adapter with optional durable snapshots. The adapter parses structured JSON, maps it to the existing frozen
domain records, runs validation synchronously and queues optimizer execution,
and explicitly maps the complete public result back to JSON. It contains no
scheduling, classification, eligibility, readiness, warning, or report policy.

## Install and run

Python 3.12 or newer is required. From an activated virtual environment, install
the API and development extras:

```bash
python -m pip install -e ".[api,dev]"
```

Run the development server on localhost only:

```bash
python -m uvicorn ramp_optimizer_api.app:app --reload --host 127.0.0.1
```

The application factory is `ramp_optimizer_api.app:create_app`; the module-level
`app` is provided for Uvicorn. Importing either performs no optimization, network
request, or data-file write.

## Routes

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/api/v1/health` | Returns `{"status":"ok"}` without invoking the optimizer or external services. |
| `GET` | `/api/v1/version` | Returns API version `1` and the installed project version from package metadata. |
| `POST` | `/api/v1/operational-days/validate` | Maps and validates a request, returning all domain and flight-reference issues as an HTTP `200` validation result. |
| `POST` | `/api/v1/optimizations` | Validates and queues a background optimization job; returns `202`. |
| `POST` | `/api/v1/operational-days` | Validates and creates an immutable snapshot; returns `201`. |
| `GET` | `/api/v1/operational-days` | Lists summaries newest first; `limit` defaults to 20 and is capped at 100. |
| `GET` | `/api/v1/operational-days/{id}` | Returns metadata and the complete resolved input. |
| `POST` | `/api/v1/operational-days/{id}/optimizations` | Queues a verified snapshot for a local worker; returns `202`. |
| `GET` | `/api/v1/operational-days/{id}/optimization-runs` | Lists stored run summaries. |
| `GET` | `/api/v1/optimization-runs/{id}` | Returns stored run metadata and complete immutable result without rerunning. |

Persistent routes require an explicit `python -m alembic upgrade head` during setup. See `docs/PERSISTENCE.md` for database settings, pagination, identity, and error behavior.

Milestone 18A also provides `POST /api/v1/imports/teamwork-employee-schedule`,
`GET /api/v1/imports/{import_id}` and `/preview`, and POST `/corrections` and
`/confirm`. Uploads use multipart `workbook` plus a JSON text `metadata` field
containing the explicit operational date and authoritative roster. See
[the complete import contract](IMPORTS.md) for request examples, limits, privacy,
review issues, revision conflicts, and idempotent confirmation. OpenAPI exposes
the correction and response schemas; multipart metadata is documented there as JSON.

Operational-day responses add `optimization_eligible` and `optimization_blockers`.
An empty-flight snapshot reports `false` and `FLIGHT_DATA_REQUIRED`; stored imported
employee-only snapshots reject optimization with `409 FLIGHT_DATA_REQUIRED`.
Existing standalone optimizer and structured request semantics remain unchanged.

Interactive Swagger documentation is at `/docs`, ReDoc is at `/redoc`, and the
OpenAPI document is at `/openapi.json`. Only operational routes use the `/api/v1`
prefix; there are no unversioned duplicates.

## Request

Both POST routes accept the same closed schema. Unknown fields and structurally
invalid dates, datetimes, enum values, or types return HTTP `422`.

```json
{
  "operational_day": {
    "operational_date": "2035-04-15",
    "employees": [
      {
        "employee_id": "A001",
        "name": "Example Agent 1",
        "qualifications": ["PUSH", "CLOSE_OUT"],
        "enabled": true
      },
      {
        "employee_id": "A002",
        "name": "Example Agent 2",
        "qualifications": [],
        "enabled": true
      }
    ],
    "employee_shifts": [
      {
        "employee_id": "A001",
        "start": "2035-04-15T07:00:00-05:00",
        "end": "2035-04-15T11:00:00-05:00",
        "normalized_role": "RAMP_AGENT"
      },
      {
        "employee_id": "A002",
        "start": "2035-04-15T07:00:00-05:00",
        "end": "2035-04-15T11:00:00-05:00",
        "normalized_role": "RAMP_AGENT"
      }
    ],
    "flights": [
      {
        "arrival_flight_number": "SYN151",
        "arrival_time": "2035-04-15T09:00:00-05:00",
        "departure_flight_number": null,
        "departure_time": null,
        "gate": "S01",
        "heavy": false
      }
    ],
    "fixed_assignments": []
  },
  "config": {
    "solver_time_limit_seconds": 2.0
  }
}
```

`config` and each of its fields may be omitted. All 19 `OptimizerConfig` fields
are exposed, and omitted values come from the domain dataclass rather than a
separate API policy table. Background jobs use a separate wall-clock deadline;
the former 60-second synchronous HTTP limit no longer applies. The domain validates that
the submitted value is positive, finite, and otherwise internally consistent.

Employee qualifications are `PUSH` and `CLOSE_OUT`. Operational shift roles are
`RAMP_AGENT`, `RAMP_LEAD`, `POSSIBLE_RAMP_SUPPORT`, `TRAINEE`, `NON_RAMP`, and
`UNKNOWN`. The API does not trim, case-change, or otherwise repair authoritative
employee values. Duplicate qualification values are rejected rather than silently
collapsed when the API list becomes the domain model's immutable `frozenset`.

Dates use `YYYY-MM-DD`. Datetimes use ISO 8601/RFC 3339. Submitted offsets are
preserved, and naive datetimes remain naive. The Phase 1 validator is authoritative
for consistent awareness across the operational day; the API never assigns a
timezone or normalizes submitted offsets.

## Flight identity and fixed assignments

Flights retain the Phase 1 directional identity. An arrival uses its arrival
number, a departure uses its departure number, and a turn uses both. Arrival and
departure namespaces remain independent, so the same numeric number may appear
once in each direction. `3000` is Mainline and numbers greater than `3000` are
Express. Both turn legs must have the same service class.

A fixed assignment references this identity without repeating time, gate, or
heavy data:

```json
{
  "employee_id": "A001",
  "flight": {
    "arrival_flight_number": "SYN101",
    "departure_flight_number": null
  }
}
```

A turn reference supplies both directional numbers. Resolution calls the existing
Phase 1 flight-number parser, so prefixes and leading zeroes do not evade canonical
matching. The reference must have the same directional shape as the target and
must resolve to exactly one submitted flight. Missing and ambiguous matches are
stable structured issues. No generated UUID or adapter identifier becomes part of
domain identity.

## Validation behavior

The dedicated validation route returns HTTP `200` when JSON is structurally
understandable, including domain-invalid input:

```json
{
  "valid": false,
  "issues": [
    {
      "code": "DUPLICATE_EMPLOYEE_ID",
      "path": "employees[1].employee_id",
      "message": "duplicates employees[0].employee_id"
    }
  ]
}
```

Every issue discovered by configuration validation, operational-day validation,
and fixed-flight reference mapping is returned in deterministic order. A valid
request returns `{"valid":true,"issues":[]}`. Malformed JSON or a Pydantic schema
error returns the error envelope below with HTTP `422`.

## Optimization behavior

The optimization route maps the request, calls the existing `validate_or_raise`,
and returns HTTP `202` with a durable job. A local worker calls
`optimize_flight_assignments`. The job result endpoint returns HTTP `200` for all valid computational
and operational results: `OPTIMAL`, `FEASIBLE`, `READY`, `READY_WITH_WARNINGS`,
`MANUAL_INTERVENTION_REQUIRED`, valid partial schedules, and
`NO_USABLE_SCHEDULE`. A staffing shortage is an operational result rather than an
HTTP failure.

A shortened nested `result` for the example shortage is:

```json
{
  "status": "OPTIMAL",
  "operational_readiness": "MANUAL_INTERVENTION_REQUIRED",
  "emergency_staffing_status": "CRITICAL_SHORTAGE_REMAINS",
  "emergency_pass_disposition": "NOT_ENABLED",
  "emergency_leads_enabled": false,
  "emergency_lead_staffing_used": false,
  "solver_runtime_seconds": 0.01,
  "schedule_summary": {
    "total_flights": 1,
    "below_minimum_flights": 1,
    "warning_count": 2
  },
  "flight_results": [
    {
      "flight": {
        "arrival_flight_number": "SYN151",
        "arrival_time": "2035-04-15T09:00:00-05:00",
        "departure_flight_number": null,
        "departure_time": null,
        "gate": "S01",
        "heavy": false
      },
      "staffing_count": 2,
      "minimum_staff": 3,
      "minimum_shortfall": 1,
      "staffing_status": "BELOW_MINIMUM"
    }
  ],
  "warnings": [
    {
      "code": "MINIMUM_STAFFING_NOT_MET",
      "severity": "CRITICAL",
      "message": "...",
      "arrival_flight_number": "SYN151",
      "departure_flight_number": null,
      "employee_id": null
    }
  ]
}
```

The actual schema includes every field shown by OpenAPI. It preserves the complete
flight and employee assignment views, staffing and qualification coverage,
fairness and continuity metrics, the schedule summary, warnings, all objective
stage values and their individual proof states, every attempt audit record, and
emergency Lead interventions. Solver status and operational readiness are separate:
an `OPTIMAL` solver result can still require manual intervention, and a `FEASIBLE`
schedule can remain usable.

## Serialization

- Enums serialize to their public string values.
- Dates use `YYYY-MM-DD`; datetimes use ISO 8601 and preserve submitted awareness.
- Domain tuples and frozen sets become JSON arrays.
- Qualification arrays produced by reverse mapping are sorted by enum value.
- Domain-meaningful flight, assignment, objective, attempt, warning, and transition
  ordering is retained.
- Optional values are present and serialize consistently as JSON `null` when absent.
- Response schemas reject non-finite floats; NaN and infinity are never emitted.
- CP-SAT variables, solver constants, and other implementation objects are never
  exposed.

Every conversion is an independently testable function in
`ramp_optimizer_api.mapping`; routes do not recursively serialize dataclasses or
reimplement report formatting.

## Error envelope

API failures use one machine-readable shape:

```json
{
  "error": {
    "code": "OPTIMIZER_INPUT_INVALID",
    "message": "Operational-day input failed validation.",
    "details": [
      {
        "code": "DUPLICATE_EMPLOYEE_ID",
        "path": "employees[1].employee_id",
        "message": "duplicates employees[0].employee_id"
      }
    ]
  }
}
```

| HTTP | Error code | Meaning |
|---:|---|---|
| `422` | `REQUEST_VALIDATION_ERROR` | Malformed JSON or structural schema mismatch. |
| `422` | `OPTIMIZER_INPUT_INVALID` | Existing domain validation rejected optimization input. |
| `422` | `FIXED_ASSIGNMENT_REFERENCE_INVALID` | A fixed-flight reference is missing, malformed, unresolved, or ambiguous. |
| `422` | `SYNCHRONOUS_POLICY_VIOLATION` | The solver time limit exceeds 60 seconds. |
| `500` | `INTERNAL_SERVER_ERROR` | An unexpected defect was sanitized at the API boundary. |

The HTTP response never contains tracebacks, local paths, submitted payloads,
solver internals, or environment details. Normal staffing and proof warnings remain
inside successful optimization results.

## Deliberate limitations

Milestone 20 executes CPU-bound optimization in a local worker child process.
See [background jobs](BACKGROUND_JOBS.md) for submission, polling, progress,
cancellation, timeouts, results and worker startup. There is no WebSocket. Submitted
workbook bytes are not retained after bounded parsing; reviewed values persist.
Operational payloads are not logged, and the API makes no network calls.

This is a localhost development API. It has no authentication or authorization
and does not enable permissive CORS. Production authentication, authorization,
deployment controls, and production storage belong to later
milestones.

## Milestone 19 — Input-management API

Validated immutable operational-day drafts and complete-snapshot revisions are
available under `/api/v1`, with optimistic concurrency, idempotency, and preserved
lineage. Apply migration `20260910_0004`. See the [workflow, routes, validation and
persistence contract](INPUT_MANAGEMENT.md) and [verification record](INPUT_MANAGEMENT_VALIDATION.md).
