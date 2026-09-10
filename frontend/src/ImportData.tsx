import { useState, type FormEvent } from 'react';
import {
  api,
  ApiError,
  flightLabel,
  human,
  post,
  time,
  type ImportRecord,
  type Schema,
} from './api';
import { Badge, Modal } from './ui';
import { ImportRowEditor } from './ImportRowEditor';
import { BulkImportReview } from './BulkImportReview';
import { importIssues } from './importIssues';

export function ImportData({
  date,
  snapshots,
  roster,
  onLoad,
  onClose,
}: {
  date: string;
  snapshots: Schema['OperationalDaySummaryResponse'][];
  roster: Schema['EmployeeRequest'][];
  onLoad: (id: string) => Promise<boolean>;
  onClose: () => void;
}) {
  const [correction, setCorrection] = useState<string | null>(null);
  const [bulk, setBulk] = useState(false);
  const [tab, setTab] = useState('saved');
  const [employee, setEmployee] = useState<ImportRecord | null>(null);
  const [flight, setFlight] = useState<ImportRecord | null>(null);
  const [review, setReview] = useState<ImportRecord | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [lookup, setLookup] = useState('');
  async function work(fn: () => Promise<void>) {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await fn();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? [e.message, ...e.details.map((d) => d.message)].join(' ')
          : e instanceof Error
            ? e.message
            : 'Import could not be completed.',
      );
    } finally {
      setBusy(false);
    }
  }
  function accept(record: ImportRecord) {
    setReview(record);
    if (record.import_type === 'DAILY_FLIGHT_LOG') setFlight(record);
    else setEmployee(record);
  }
  async function upload(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    await work(async () => {
      const payload = new FormData();
      payload.set('workbook', form.get('workbook')!);
      const rosterFile = form.get('roster') as File | null;
      const metadata =
        tab === 'employees'
          ? {
              operational_date: date,
              ramp_agents_only: true,
              roster: rosterFile?.size ? JSON.parse(await rosterFile.text()) : roster,
            }
          : {
              operational_date: date,
              time_policy: {
                airport_timezone: form.get('timezone'),
                operational_day_start: `${form.get('dayStart')}:00`,
                planning_basis: form.get('basis'),
              },
            };
      payload.set('metadata', JSON.stringify(metadata));
      accept(
        await api<ImportRecord>(
          tab === 'employees' ? '/imports/teamwork-employee-schedule' : '/imports/daily-flight-log',
          { method: 'POST', body: payload },
        ),
      );
    });
  }
  return (
    <Modal
      title="Import / load operational data"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <div className="import-body">
        <nav className="tabs" aria-label="Data source">
          {[
            ['saved', 'Saved inputs'],
            ['employees', 'Employee schedule'],
            ['flights', 'Flight log'],
          ].map(([value, label]) => (
            <button
              disabled={busy}
              key={value}
              className={tab === value ? 'active' : ''}
              onClick={() => setTab(value)}
            >
              {label}
            </button>
          ))}
        </nav>
        {error && (
          <div role="alert" className="notice bad">
            {error}
          </div>
        )}
        {tab === 'saved' ? (
          <div className="snapshot-list">
            {snapshots.map((s) => (
              <div key={s.id}>
                <div>
                  <strong>
                    {s.employee_count} employees · {s.shift_count} shifts · {s.flight_count} flights
                  </strong>
                  <small className="muted">
                    {new Date(s.created_at_utc).toLocaleString()} · {s.id.slice(0, 8)}
                  </small>
                  {!s.optimization_eligible && (
                    <Badge tone="warn">{s.optimization_blockers?.map(human).join(', ')}</Badge>
                  )}
                </div>
                <button
                  disabled={busy || !s.optimization_eligible}
                  onClick={() =>
                    void work(async () => {
                      if (await onLoad(s.id)) onClose();
                      else
                        throw new Error(
                          'This snapshot could not be loaded. Select a confirmed import snapshot or refresh the current input version.',
                        );
                    })
                  }
                >
                  Load snapshot
                </button>
              </div>
            ))}
            {!snapshots.length && (
              <p className="empty">
                No saved inputs for {date}. Upload an employee schedule and flight log, review both,
                then combine them.
              </p>
            )}
          </div>
        ) : (
          <form onSubmit={upload} className="editor">
            <label>
              {tab === 'employees'
                ? 'TeamWork employee schedule (.xlsx)'
                : 'Daily flight log (.xlsx)'}
              <input type="file" name="workbook" accept=".xlsx" required />
            </label>
            {tab === 'employees' ? (
              <label>
                Qualifications roster (.json, optional)
                <input type="file" name="roster" accept=".json" />
                <small>
                  {roster.length
                    ? `Optional: use the ${roster.length} employees in the current input.`
                    : 'Names and shifts come from the employee workbook. An optional roster supplies verified qualifications; only Ramp Agents are included.'}
                </small>
              </label>
            ) : (
              <div className="form-grid">
                <label>
                  Airport time zone
                  <input name="timezone" required defaultValue="America/Chicago" />
                </label>
                <label>
                  Day starts
                  <input type="time" name="dayStart" defaultValue="00:00" required />
                </label>
                <label>
                  Planning basis
                  <select name="basis">
                    <option value="SCHEDULED">Scheduled times</option>
                    <option value="ESTIMATED">Estimated times</option>
                  </select>
                </label>
              </div>
            )}
            <button className="primary" disabled={busy}>
              {busy ? 'Processing…' : 'Upload for review'}
            </button>
          </form>
        )}
        <form
          className="toolbar"
          onSubmit={(e) => {
            e.preventDefault();
            void work(async () => accept(await api<ImportRecord>(`/imports/${lookup}`)));
          }}
        >
          <input
            aria-label="Existing import ID"
            placeholder="Resume an existing import by ID"
            value={lookup}
            onChange={(e) => setLookup(e.target.value)}
            required
          />
          <button disabled={busy}>Open import</button>
        </form>
        {review && (
          <section className="import-review">
            <div className="section-heading">
              <h3>{review.original_filename}</h3>
              <Badge>{human(review.status)}</Badge>
              <span>Revision {review.preview.revision}</span>
              <button disabled={busy || !!review.confirmed_at} onClick={() => setBulk(true)}>
                Correct all
              </button>
            </div>
            <p className="mono muted">Import ID: {review.import_id}</p>
            {review.preview.operational_date !== date && (
              <p className="notice bad">
                This import is for {review.preview.operational_date}, not the selected operating
                date.
              </p>
            )}
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Source row</th>
                    <th>{review.import_type === 'DAILY_FLIGHT_LOG' ? 'Flight' : 'Employee'}</th>
                    <th>Start / arrival</th>
                    <th>End / departure</th>
                    <th>Status</th>
                    <th>Review</th>
                  </tr>
                </thead>
                <tbody>
                  {review.preview.rows
                    .filter((r) => r.normalized_role === 'RAMP_AGENT' && !r.vacancy)
                    .map((r) => (
                      <tr key={r.row_id}>
                        <td>{r.source_row}</td>
                        <td>
                          {r.employee_name ||
                            review.preview.roster.find((e) => e.employee_id === r.employee_id)
                              ?.name ||
                            r.employee_id ||
                            'Unmatched'}
                        </td>
                        <td>{time(r.start)}</td>
                        <td>{time(r.end)}</td>
                        <td>{r.excluded ? 'Excluded' : human(r.match_status)}</td>
                        <td>
                          <button
                            disabled={busy || !!review.confirmed_at}
                            onClick={() => setCorrection(r.row_id)}
                          >
                            Correct
                          </button>
                        </td>
                      </tr>
                    ))}
                  {review.preview.flight_rows?.map((r) => (
                    <tr key={r.row_id}>
                      <td>{r.source_row}</td>
                      <td>{flightLabel(r.flight)}</td>
                      <td>{time(r.flight.arrival_time)}</td>
                      <td>{time(r.flight.departure_time)}</td>
                      <td>
                        {r.will_be_excluded
                          ? 'Excluded'
                          : r.confirmation_eligible
                            ? 'Ready for review'
                            : 'Needs correction'}
                      </td>
                      <td>
                        <button
                          disabled={busy || !!review.confirmed_at}
                          onClick={() => setCorrection(r.row_id)}
                        >
                          Correct
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ul className="review-issues">
              {importIssues(review).map((issue, i) => (
                <li key={i}>
                  <Badge tone={issue.blocks_confirmation ? 'bad' : 'warn'}>
                    {human(issue.severity)}
                  </Badge>{' '}
                  {issue.source_row ? `Row ${issue.source_row}: ` : ''}
                  {issue.message}
                  {issue.remediation && <p>{issue.remediation}</p>}
                </li>
              ))}
            </ul>
            {!importIssues(review).length && <p>No import validation issues reported.</p>}
            <p className="muted">
              Review all rows before confirming. Use Correct to resolve blocking issues; saved
              corrections are retained in the import audit history.
            </p>
            <button
              className="primary"
              disabled={
                busy ||
                importIssues(review).some(issue => issue.blocks_confirmation) ||
                !!review.confirmed_at ||
                review.preview.operational_date !== date
              }
              onClick={() =>
                void work(async () =>
                  accept(
                    await post<ImportRecord>(`/imports/${review.import_id}/confirm`, {
                      revision: review.preview.revision,
                    }),
                  ),
                )
              }
            >
              Confirm reviewed import
            </button>
          </section>
        )}
        <section className="combine">
          <h3>Compose operational day</h3>
          <p>
            Employee schedule:{' '}
            <Badge tone={employee?.confirmed_at ? 'good' : 'neutral'}>
              {employee?.confirmed_at ? 'Confirmed' : 'Required'}
            </Badge>{' '}
            Flight log:{' '}
            <Badge tone={flight?.confirmed_at ? 'good' : 'neutral'}>
              {flight?.confirmed_at ? 'Confirmed' : 'Required'}
            </Badge>
          </p>
          <button
            className="primary"
            disabled={
              busy ||
              !employee?.confirmed_at ||
              !flight?.confirmed_at ||
              employee.preview.operational_date !== date ||
              flight.preview.operational_date !== date
            }
            onClick={() =>
              void work(async () => {
                const combined = await post<Schema['ImportReadinessResponse']>('/imports/combine', {
                  employee_import_id: employee!.import_id,
                  flight_import_id: flight!.import_id,
                });
                if (await onLoad(combined.operational_day_id)) onClose();
                else
                  throw new Error(
                    'The combined snapshot was saved but could not be loaded. Close this dialog and refresh inputs to retry.',
                  );
              })
            }
          >
            Combine & load day
          </button>
        </section>
        {bulk && review && (
          <BulkImportReview record={review} onSaved={accept} onClose={() => setBulk(false)} />
        )}
        {correction && review && (
          <ImportRowEditor
            record={review}
            rowId={correction}
            onSaved={accept}
            onClose={() => setCorrection(null)}
          />
        )}
      </div>
    </Modal>
  );
}
