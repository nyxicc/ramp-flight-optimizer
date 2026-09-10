import { useState } from 'react';
import { activeJob, human, type Day } from './api';
import { useOperations } from './useOperations';
import { Schedule, Workforce } from './Schedule';
import { Issues, Badge } from './ui';
import { EditInput, type Editor } from './EditInput';
import { ImportData } from './ImportData';
import { JobPanel } from './JobPanel';
import { Timeline } from './Timeline';

export function Dashboard() {
  const [date, setDate] = useState(() => {
    const requested = new URLSearchParams(location.search).get('date');
    if (requested && /^\d{4}-\d{2}-\d{2}$/.test(requested)) return requested;
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  });
  return (
    <Workspace
      key={date}
      date={date}
      onDate={(value) => {
        const url = new URL(location.href);
        url.searchParams.set('date', value);
        history.replaceState(null, '', url);
        setDate(value);
      }}
    />
  );
}
function Workspace({ date, onDate }: { date: string; onDate: (date: string) => void }) {
  const ops = useOperations(date);
  const [view, setView] = useState('schedule');
  const [importing, setImporting] = useState(false);
  const [editing, setEditing] = useState<Editor | null>(null);
  const [selectedEmployee, setSelectedEmployee] = useState<string | null>(null);
  const [restoreId, setRestoreId] = useState('');
  const day: Day = ops.version?.input.operational_day || {
    operational_date: date,
    flights: [],
    employees: [],
    employee_shifts: [],
    fixed_assignments: [],
  };
  const result = ops.result;
  const summary = result?.result.schedule_summary;
  const latest = ops.versions.at(-1);
  let root = ops.version;
  const ancestors = new Set<string>();
  while (root?.parent_version_id && !ancestors.has(root.id)) {
    ancestors.add(root.id);
    const parent = ops.versions.find((v) => v.id === root!.parent_version_id);
    if (!parent) break;
    root = parent;
  }
  const baseline = root?.input;
  const editable =
    !!ops.version &&
    latest?.id === ops.version.id &&
    !ops.busy &&
    !ops.recoveringJob &&
    !activeJob(ops.job?.status);
  const issues = [
    ...(ops.version?.warnings || []),
    ...(result?.result.warnings || []),
    ...(ops.error?.details.map((d) => ({ ...d, severity: 'ERROR' })) || []),
  ];
  const rampAgents = new Set(
    day.employee_shifts
      ?.filter(
        (s) =>
          s.normalized_role === 'RAMP_AGENT' &&
          day.employees?.some((e) => e.employee_id === s.employee_id && e.enabled),
      )
      .map((s) => s.employee_id),
  );
  const metrics: [string, number | string, string?][] = [
    ['Total flights', day.flights?.length || 0],
    ['Rostered ramp agents', rampAgents.size],
    ['Preferred staffed', summary?.preferred_staffed_flights ?? '—', 'good'],
    [
      'Below minimum',
      summary?.below_minimum_flights ?? '—',
      summary?.below_minimum_flights ? 'bad' : '',
    ],
    ['Heavy flights', day.flights?.filter((f) => f.heavy).length || 0],
    [
      'Break violations',
      summary?.employees_with_unsatisfied_break ?? '—',
      summary?.employees_with_unsatisfied_break ? 'bad' : '',
    ],
    [
      'Qualification conflicts',
      result
        ? result.result.flight_results.filter(
            (f) => f.push_covered === false || f.close_covered === false,
          ).length
        : '—',
    ],
    [
      'Unassigned agents',
      result
        ? [...rampAgents].filter(
            (id) => !result.result.flight_results.some((f) => f.assigned_employee_ids.includes(id)),
          ).length
        : '—',
    ],
  ];
  return (
    <>
      <header className="app-header">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            R / O
          </span>
          <div>
            <strong>Ramp Team-Flight Optimizer</strong>
            <small>SUPERVISOR WORKSPACE</small>
          </div>
        </div>
        <label className="date-control">
          Schedule day
          <input
            aria-label="Schedule day"
            type="date"
            required
            value={date}
            disabled={ops.busy}
            onChange={(e) => {
              if (/^\d{4}-\d{2}-\d{2}$/.test(e.target.value)) onDate(e.target.value);
            }}
          />
        </label>
        <div className="header-actions">
          <button disabled={ops.busy || ops.loading} onClick={() => setImporting(true)}>
            Import / load data
          </button>
          <button disabled={!editable} onClick={() => setEditing({ kind: 'settings' })}>
            Settings
          </button>
          <button
            className="primary"
            disabled={
              !ops.version ||
              !day.flights?.length ||
              !day.employees?.length ||
              ops.busy ||
              ops.recoveringJob ||
              activeJob(ops.job?.status) ||
              ops.loading
            }
            onClick={() => void ops.optimize()}
          >
            {ops.busy
              ? 'Working…'
              : activeJob(ops.job?.status)
                ? 'Optimization active'
                : 'Optimize Schedule'}
          </button>
        </div>
      </header>
      <main>
        <section className="metrics" aria-label="Operational summary">
          {metrics.map(([label, value, tone]) => (
            <div key={label}>
              <span>{label}</span>
              <strong
                className={`mono ${typeof value === 'number' && value > 0 ? tone || '' : ''}`}
              >
                {value}
              </strong>
            </div>
          ))}
        </section>
        {ops.error && (
          <div className="notice bad" role="alert">
            <strong>{ops.error.message}</strong>
            {ops.error.status === 409 && (
              <p>
                Another revision or state change may have occurred. Refresh inputs before retrying;
                unsaved edits remain in the editor.
              </p>
            )}
            <button disabled={ops.busy} onClick={() => void ops.refresh()}>
              Refresh inputs
            </button>
          </div>
        )}
        <div className="workspace-heading">
          <nav className="tabs" aria-label="Workspace view">
            {[
              ['schedule', 'Flight operations'],
              ['workforce', 'Workforce'],
              ['timeline', 'Timeline'],
            ].map(([key, label]) => (
              <button
                key={key}
                className={view === key ? 'active' : ''}
                aria-current={view === key ? 'page' : undefined}
                onClick={() => setView(key)}
              >
                {label}
              </button>
            ))}
          </nav>
          <div className="version-controls">
            {ops.version ? (
              <>
                <label>
                  Input version{' '}
                  <select
                    aria-label="Input version"
                    disabled={ops.busy}
                    value={ops.version.id}
                    onChange={(e) => {
                      ops.selectVersion(ops.versions.find((v) => v.id === e.target.value)!);
                      setSelectedEmployee(null);
                    }}
                  >
                    {ops.versions.map((v) => (
                      <option key={v.id} value={v.id}>
                        v{v.version_number}
                        {v.id === latest?.id ? ' · latest' : ''}
                      </option>
                    ))}
                  </select>
                </label>
                {ops.version.parent_version_id && (
                  <Badge tone="info" title={ops.version.reason || 'Revised input'}>
                    Revised
                  </Badge>
                )}
              </>
            ) : (
              <span>{ops.loading ? 'Loading inputs…' : 'No input loaded'}</span>
            )}
            <button disabled={ops.busy || ops.loading} onClick={() => void ops.refresh()}>
              Refresh
            </button>
          </div>
        </div>
        <JobPanel
          job={ops.job}
          result={result}
          error={ops.pollError}
          onCancel={() => void ops.cancel()}
          busy={ops.busy}
          recovering={ops.recoveringJob}
        />
        {ops.version && ops.version.id !== latest?.id && (
          <p className="notice info">
            Historical input version. Select the latest version to make changes.
          </p>
        )}
        {ops.version && (
          <div className="input-provenance">
            <span>
              Input v{ops.version.version_number} · {day.employee_shifts?.length || 0} imported
              shifts · {ops.version.reason || 'Loaded input'}
            </span>
            <details>
              <summary>Restore job tracking</summary>
              <form
                onSubmit={(e) => {
                  e.preventDefault();
                  void ops.restore(restoreId);
                }}
              >
                <label>
                  Job ID
                  <input
                    required
                    value={restoreId}
                    onChange={(e) => setRestoreId(e.target.value)}
                    placeholder="Paste a job ID for this input version"
                  />
                </label>
                <button disabled={ops.busy}>Restore</button>
              </form>
            </details>
          </div>
        )}
        {ops.loading ? (
          <div className="surface empty" role="status">
            Loading operational inputs…
          </div>
        ) : (
          <>
            {view === 'schedule' && (
              <Schedule
                day={day}
                result={result}
                baseline={baseline?.operational_day}
                editable={editable}
                onEdit={(index) => setEditing({ kind: 'flight', index })}
                onEmployee={(id) => {
                  setSelectedEmployee(id);
                  setView('workforce');
                }}
              />
            )}
            {view === 'timeline' && <Timeline day={day} result={result} />}
            <div className={`lower-workspace ${view === 'workforce' ? 'workforce-focused' : ''}`}>
              <Workforce
                day={day}
                baseline={baseline?.operational_day}
                result={result}
                selected={selectedEmployee}
                onSelect={setSelectedEmployee}
                editable={editable}
                onEdit={(index) => setEditing({ kind: 'shift', index })}
              />
              <Issues issues={issues} evaluated={!!ops.version} />
            </div>
          </>
        )}
        <footer className="app-footer">
          <span>Ramp operations · Supervisor review required</span>
          <span>
            {result
              ? `Readiness: ${human(result.result.operational_readiness)}`
              : 'Staffing, qualifications and breaks have not been evaluated'}
          </span>
        </footer>
      </main>
      {importing && (
        <ImportData
          date={date}
          snapshots={ops.snapshots}
          roster={day.employees || []}
          onLoad={ops.loadSnapshot}
          onClose={() => setImporting(false)}
        />
      )}
      {editing && ops.version && (
        <EditInput
          version={ops.version}
          baseline={baseline}
          target={editing}
          busy={ops.busy}
          error={
            ops.error
              ? [
                  ops.error.message,
                  ...ops.error.details.map((d) => `${d.path}: ${d.message}`),
                ].join(' ')
              : undefined
          }
          onSave={ops.revise}
          onClose={() => setEditing(null)}
        />
      )}
    </>
  );
}
