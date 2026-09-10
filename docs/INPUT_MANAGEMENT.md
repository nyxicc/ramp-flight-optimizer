# Milestone 19: immutable input management

All examples describe fictional operations. Apply Alembic `20260910_0004` before
using these routes. No authentication, user identity, jobs, or dashboard is added.

## Routes

| Method | Route | Result |
| --- | --- | --- |
| POST | `/api/v1/operational-days/{day}/drafts` | Create an empty draft or copy a source; 201 |
| GET | `/api/v1/operational-days/{day}/versions` | Versions in ascending version-number order; 200 |
| GET | `/api/v1/operational-day-versions/{version_id}` | Complete immutable version; 200 |
| POST | `/api/v1/operational-day-versions/{version_id}/revisions` | Validate and append a complete replacement snapshot; 201 |

`day` is an ISO date; IDs are UUIDs. Listing accepts `limit` (1–100, default 20)
and `offset` (nonnegative, default 0), and returns an array of version resources.
Use the highest version number as the current editing head for that date.

## Create and revise

Create a fictional empty draft:

```http
POST /api/v1/operational-days/2026-09-10/drafts
Content-Type: application/json

{"idempotency_key":"fictional-day-20260910","reason":"Initial supervisor draft"}
```

Alternatively provide `source_snapshot_id`, the ID of a confirmed employee,
flight, or combined import snapshot, or `source_version_id`. Sources must have
the same operational date. Specify at most one. Copying an older version is an
explicit new draft at the current date's next number, with that source as parent.
An import-based draft records `source_snapshot_id` and has no version parent.
Sources are never modified.

The response includes `id`, `operational_date`, `version_number`,
`parent_version_id`, `source_snapshot_id`, `created_at`, `content_hash`, `input`,
`reason`, `valid: true`, and `warnings`. `input` is the existing full
`OptimizationRequest`, including all optimizer configuration defaults. For an
empty draft, `input.operational_day` contains the date and four empty arrays:
`employees`, `employee_shifts`, `flights`, and `fixed_assignments`.

Fetch the version, edit its `input`, and POST:

```javascript
// Fictional dashboard-client example; version is the GET response.
const input = structuredClone(version.input);
input.operational_day.employees.push({
  employee_id: "SYN019", name: "Fictional Avery", enabled: true,
  qualifications: ["PUSH", "CLOSE_OUT"]
});
input.operational_day.employee_shifts.push({
  employee_id: "SYN019", start: "2026-09-10T07:00:00",
  end: "2026-09-10T15:00:00", normalized_role: "RAMP_AGENT"
});
await fetch(`/api/v1/operational-day-versions/${version.id}/revisions`, {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({idempotency_key: "fictional-edit-001",
    expected_parent_hash: version.content_hash, input,
    reason: "Correct fictional staffing input"})
});
```

The version resource in the 201 response contains the complete new snapshot,
the requested parent ID, the next number, and a new UUID. It retains all supplied
employees, shifts, flights, assignments and configuration. The creation time is
UTC. Missing staff or flights can produce stored warnings; `valid` means input
validation passed, not that a fully staffed schedule is guaranteed.

## Input operations

The atomic complete-snapshot revision is intentionally the single edit contract.
It supports coordinated edits without intermediate invalid versions:

| Operation | Change to the copied `input.operational_day` |
| --- | --- |
| Enable / disable employee | Set employee `enabled` to true / false |
| Correct shift | Change the matching shift's explicit `start` / `end` datetime |
| Manage qualifications | Replace employee `qualifications` with a unique subset of `PUSH`, `CLOSE_OUT` |
| Add flight | Append a complete flight with explicit directional number/time pairs |
| Edit flight / timing | Replace the matching flight's fields; update dependent fixed references when identity changes |
| Remove flight | Omit it and remove its fixed assignments in the same revision |
| Mark / unmark heavy | Set flight `heavy` to true / false |
| Add fixed assignment | Append `{employee_id, flight: {arrival_flight_number, departure_flight_number}}` |
| Remove fixed assignment | Omit the matching employee/directional-flight pair |

Employees already present in the parent must remain present; disable them instead
of deleting them. Disabled employees and their qualifications remain reproducible.
Shifts are discrete records; do not combine shifts to imply coverage across gaps.
Fixed assignments retain the existing directional-reference rules, including
rejection of missing or ambiguous matches. No status, service class or clock time
is guessed. Mainline/Express classification and heavy staffing limits come from
Phase 1. Unknown fields (including invented flight status values) are rejected.

## Concurrency and idempotency

Keys are required, 1–128 characters, scoped to an operational date across draft
creation and revisions. Retrying the same parsed request returns the same resource
with 201, even after the parent has become stale. Reusing a key with different
content returns 409 `IDEMPOTENCY_KEY_REUSED`. Each edit supplies both the parent ID
in the URL and its canonical hash in `expected_parent_hash`. Editing any version
other than the latest for that date, or supplying the wrong hash, returns 409
`STALE_INPUT_VERSION`. A uniqueness race is rolled back and retried once; a
remaining contention returns `CONCURRENT_INPUT_REVISION`.

Example conflict response:

```json
{"error":{"code":"STALE_INPUT_VERSION","message":"Stale parent version; retrieve the latest version","details":[]}}
```

Reload and reconcile changes after a stale conflict. Never overwrite history.
JSON object-key order does not affect request fingerprints or input hashes.
Array order remains meaningful, preserving the established Phase 1 ordering and
tie-breaking behavior. Qualifications serialize in sorted order. Hashes reuse the
existing canonical input serializer and include the complete optimizer config;
IDs, timestamps, lineage and revision reasons are not part of the content hash.

## Validation policy

Pydantic rejects malformed types, unsupported enums, duplicate qualifications,
unknown fields, invalid UUIDs, and invalid hash syntax with the existing 422 error
envelope. Mapping resolves fixed assignments into Phase 1 domain references.
Every candidate then passes `validate_or_raise`; **all Phase 1 validation issues
block persistence**. Codes and paths are preserved in `error.details`, including
duplicate directional flight identities, inconsistent times/service categories,
overlapping shifts or fixed work, ineligible employees, inadequate shift coverage,
and fixed staffing above the flight maximum. No validator finding is downgraded.

API boundary findings also block creation: route/source date mismatch, employee
removal, more than 1000 records in any collection, and calendar boundaries. Shifts
start on the operational date and end on it or the next date. Flight timestamps
fall on that date or the next date. Supply full ISO datetimes; timezone awareness
must be consistent under Phase 1 rules. These calendar restrictions are API input
policy and do not alter Phase 1 optimization.

Stored warnings are `FLIGHT_DATA_REQUIRED`, `EMPLOYEE_DATA_REQUIRED`, and
`INSUFFICIENT_ELIGIBLE_STAFF`. The latter reuses Phase 1 eligibility and staffing
requirements and is a local pool assessment, not a schedule feasibility proof.
Qualification coverage is a crew-level optimization finding in Phase 1, not an
individual fixed-assignment qualification requirement. Removing a qualification
may therefore be accepted; the existing optimizer reports missing crew coverage.
No new individual qualification restriction is imposed.

Missing resources return 404, stale/reused keys return 409, and invalid input
returns 422. Input request bodies share the bounded request reader and configured
upload-byte cap plus 256 KiB allowance; exceeding it returns 413 `UPLOAD_TOO_LARGE`.
Revision reasons are limited to 1000 characters. No new 400 case is needed.

## Persistence and optimization

`input_versions` references the existing immutable normalized `operational_days`
snapshot using the same UUID. It records date, monotonically increasing number,
parent/source foreign keys, request fingerprint/key, reason, and validation JSON.
Date/number and date/key unique constraints provide indexed lookup and protect
concurrent inserts. Foreign keys restrict deletion of referenced history. ORM
guards reject updates/deletes of version metadata and changes to associated input
rows. No HTTP update-in-place or delete route exists. Direct administrator SQL is
outside the API; existing snapshot hash checks detect content corruption on reads.

Snapshot insertion and lineage insertion share one transaction; validation errors,
late repository failures and losing insert races leave no partial snapshot.
Existing input-hash verification applies on every version read. Upgrade leaves
Milestone 17/18 tables and records untouched. Downgrade to `20260909_0003` removes
only an empty lineage table; it refuses populated version history, matching the
prior milestone's history-preservation policy.

The version UUID is also a stored operational-day UUID. Use the existing
`POST /api/v1/operational-days/{id}/optimizations` route to optimize that exact
version through a background job, or submit its `input` to `/api/v1/optimizations`.
Since Milestone 20 these POST routes return 202 job resources; see
[background jobs](BACKGROUND_JOBS.md) for worker startup and result retrieval.
Result qualification and staffing warnings retain the established Phase 1 behavior.
