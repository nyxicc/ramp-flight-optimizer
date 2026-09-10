# Milestone 21 — Supervisor dashboard foundation

The `frontend` application is React + TypeScript, built with Vite. It uses the
existing FastAPI `/api/v1` contract; no optimizer, staffing eligibility, workload
formula, or break scheduler runs in the browser. The supervisor import workflow
adds an opt-in ramp-only workbook roster and ignores
decorative flight-log title dates. No solver behavior or database migration changes.

## Run locally

Use Python 3.12+ and Node.js 22.12+ (Node 22 LTS recommended). From the repository
root, activate the Python virtual environment and install API dependencies:

```powershell
.venv\Scripts\Activate.ps1
python -m pip install -e ".[api,dev]"
python -m alembic upgrade head
python -m uvicorn ramp_optimizer_api.app:app --host 127.0.0.1 --port 8000
```

In a second terminal, activate the same environment and launch the existing worker:

```powershell
python -m ramp_optimizer_api.local_worker
```

In a third terminal:

```powershell
cd frontend
npm ci
npm run dev
```

Open `http://127.0.0.1:5173`. Vite proxies `/api` to the local service on port 8000;
the same-origin boundary requires no CORS changes. The worker and API must use the
same `RAMP_OPTIMIZER_DATABASE_URL`. A queued job waits until a worker is running.

For a production build, run `npm run build`. Serve `frontend/dist` through a web
server with `/api` forwarded to FastAPI. `npm run preview` also proxies the local
API for build verification. Deployment, authentication and live airline feeds
remain outside this milestone. There is no remote service publication in this
repository workflow.

## Fictional demonstration

To avoid mixing demonstration inputs with real imported snapshots, use an
isolated database. Set this variable in both the API and worker terminals:

```powershell
New-Item -ItemType Directory -Force frontend/.local-demo
$env:RAMP_OPTIMIZER_DATABASE_URL = 'sqlite:///./frontend/.local-demo/ramp.db'
python -m alembic upgrade head
python scripts/seed_dashboard_demo.py
```

Open `http://127.0.0.1:5173/?date=2035-04-15`, then select Optimize Schedule.
The script uses the existing 24-flight fictional scenario and creates a validated
input version through the existing input-management service. It leaves an existing
day untouched. No demonstration data or mock responses are shipped in the UI.

## Supervisor workflow

1. Select an operating date. Date selection is bookmarkable with `?date=YYYY-MM-DD`.
   The latest saved input version loads automatically; the selector also exposes
   earlier versions. All version and snapshot pages are fetched.
2. Use **Import / load data** to load a confirmed import snapshot or existing input
   version, or upload the employee workbook and daily flight log. Employee imports read Ramp Agent names and shifts directly from the workbook.
   Only occupied Ramp Agent rows are included. An optional roster JSON array or
   the current input roster supplies existing verified qualifications. New workbook
   identities start without qualifications; verify them during review. Flight imports require an explicit airport time
   zone, day boundary and scheduled/estimated planning basis.
3. Inspect preview rows and validation messages. **Correct all** opens a batch editor
   that saves one reviewed revision, including per-flight heavy classification or
   per-agent shifts and verified qualifications. You can also correct employee matches, shifts,
   roles, flight times, gates, heavy classification, or exclusions. Confirm each
   reviewed revision, then combine the confirmed imports into a day. Keep import
   IDs to resume reviews. Workbook layouts and roster fields are documented in
   [employee imports](IMPORTS.md) and [flight imports](FLIGHT_IMPORTS.md).
4. Inspect the flight schedule and workforce. Search flights or gates; filter
   heavy, below-preferred or qualification-conflict flights; sort by flight number
   or operational time. Click a team member to inspect their workforce row.
5. Submit optimization. Job states include queued, running, cancelling, completed,
   failed, cancelled and timed out. Polling errors retain the last known status
   and retry. Cancelling requires confirmation and waits for server acknowledgement.
6. Review the server's operational readiness separately from job success. Partial
   results retain an explicit warning. Summary values show staffing compromises,
   qualification conflicts, breaks, Lead assignments, three-person teams and
   workload range. The workforce table supplies the workload distribution.
7. Edit flight times, heavy status, employee shifts, or fixed assignments. Saving
   creates a full immutable revision with the parent hash, an idempotency key and
   a supervisor reason. The server validates everything. Conflicts and validation
   details remain visible without discarding the editor. Original flight values
   and shifts can be restored into another revision after confirmation.

The last job ID is stored per input version on this device, allowing polling to
resume after reload. Browser storage contains IDs only, not rosters or schedules.
**Restore job tracking** accepts an existing job ID and verifies its input version.
The API has no job-list endpoint; cross-device job discovery is not implemented.
New input versions never display earlier results as if they were current.

## Design and scope

- Compact navy header, neutral work surfaces, 1px borders, small radii, locally
  bundled IBM Plex fonts and tabular time/flight values. No remote font requests.
- Flight table is primary; workforce and dedicated hard/soft issue lists are
  secondary. Sticky table headers and contained scrolling retain context.
- Green/amber/red represent reported satisfaction/warnings/failures, blue marks
  manual edits, and gray means unknown or not evaluated. Unknown metrics use a
  dash rather than claiming zero problems.
- Settings expose the saved backend configuration, including staffing preferences,
  work windows, break/reset duration, workload factors and Lead fallback policy.
- Native modal dialogs provide focus containment, Escape dismissal and focus
  restoration; controls have labels and visible keyboard focus. Tablet layouts
  stack workforce and issues. Dense tables scroll horizontally on small screens.
- The timeline places server-provided assignment windows within shift boundaries.
  Its axis is explicitly UTC to align mixed offsets; tooltips retain airport-local
  times. It does not invent break placement or recalculate streaks/overlaps.
- Aircraft type, station identity, exact break slots and live employee availability
  are absent from the current contract. Aircraft is shown as unavailable, no fake
  station selector is added, and employee status says rostered/disabled/no shift.
- Fixed assignments constrain inclusion; they are not an exclusive hand-picked
  team. The backend may add employees. Leads retain their fallback designation.
- Import source and revision audits are preserved by the backend. Override markers
  compare against the loaded lineage root; corrected source-import history is
  available through the existing import API rather than flattened into a false
  optimizer/manual distinction.

## Verification

```powershell
cd frontend
npm test
npm run build
```

React component/hook tests use a server-generated fictional shortage fixture.
They cover admission deduplication, retry idempotency, reload recovery, immutable
revision conflicts, partial results, version isolation, cancellation semantics,
job/readiness distinction, table filters and manual input changes. These are DOM
tests in jsdom, not browser screenshot or end-to-end visual tests.

Frontend CI runs locked installation, tests and production build. Existing Python
API, import, input-management and worker tests verify the unchanged integration
contracts. To regenerate types after a backend contract change:

```powershell
python scripts/export_frontend_contract.py
cd frontend
npx openapi-typescript openapi.json -o src/schema.d.ts
```

`src/schema.d.ts` is generated and committed. `openapi.json` is an ignored
intermediate. The frontend lockfile is committed; `.npmrc` uses legacy peer
resolution to avoid an npm 10 optional Vitest peer-resolution crash.

Milestone verification: 20 frontend tests passed; the production build passed;
198 selected backend API, import, input-management and job tests passed. A real
24-flight fictional job was submitted through the Vite proxy, processed by the
local worker, and returned `SUCCEEDED` with `MANUAL_INTERVENTION_REQUIRED` and the
correct input-version link. The local page and proxied health endpoint returned
successful responses. Browser visual QA was not performed.

### Simplified review follow-up

There is one selected schedule day. Dates in decorative flight-log titles are
ignored; actual dated flight times and overnight boundaries are still validated.
The workforce and timeline show Ramp Agents only. New dashboard imports opt into
`ramp_agents_only`; the legacy API mode remains available. Re-upload employee
workbooks imported before this change if their unmatched names are missing, since
the earlier importer deliberately did not retain those names. Notes remain discarded.
