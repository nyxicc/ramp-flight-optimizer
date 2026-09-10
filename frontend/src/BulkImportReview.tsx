import { useState, type FormEvent } from 'react';
import { ApiError, flightLabel, post, type ImportRecord } from './api';
import { Modal } from './ui';

export function BulkImportReview({
  record,
  onSaved,
  onClose,
}: {
  record: ImportRecord;
  onSaved: (record: ImportRecord) => void;
  onClose: () => void;
}) {
  const isFlight = record.import_type === 'DAILY_FLIGHT_LOG';
  const [flightRows, setFlightRows] = useState(() =>
    (record.preview.flight_rows || [])
      .filter((r) => !r.will_be_excluded)
      .map((r) => ({
        row_id: r.row_id,
        label: flightLabel(r.flight),
        arrival_time: r.flight.arrival_time || '',
        departure_time: r.flight.departure_time || '',
        heavy: r.flight.heavy,
        excluded: false,
      })),
  );
  const [employeeRows, setEmployeeRows] = useState(() =>
    record.preview.rows
      .filter((r) => r.normalized_role === 'RAMP_AGENT' && !r.vacancy && !r.excluded)
      .map((r) => ({
        row_id: r.row_id,
        employee_id: r.employee_id || '',
        name:
          r.employee_name ||
          record.preview.roster.find((e) => e.employee_id === r.employee_id)?.name ||
          'Unmatched employee',
        start: r.start || '',
        end: r.end || '',
        excluded: false,
        qualifications: [
          ...(record.preview.employees.find((e) => e.employee_id === r.employee_id)
            ?.qualifications || []),
        ],
      })),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  async function save(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      const payload = isFlight
        ? {
            flight_corrections: flightRows.map(
              ({ row_id, arrival_time, departure_time, heavy, excluded }) => ({
                row_id,
                heavy,
                excluded,
                ...(arrival_time ? { arrival_time } : {}),
                ...(departure_time ? { departure_time } : {}),
              }),
            ),
          }
        : {
            corrections: [
              ...employeeRows.map(
                ({ row_id, employee_id, start, end, excluded, qualifications }) =>
                  excluded
                    ? { row_id, excluded: true }
                    : {
                        row_id,
                        ...(employee_id ? { employee_id, qualifications } : {}),
                        ...(start ? { start } : {}),
                        ...(end ? { end } : {}),
                        excluded: false,
                      },
              ),
              ...record.preview.rows
                .filter((r) => r.normalized_role !== 'RAMP_AGENT' || r.vacancy)
                .map((r) => ({ row_id: r.row_id, excluded: true })),
            ],
          };
      onSaved(
        await post<ImportRecord>(`/imports/${record.import_id}/corrections`, {
          revision: record.preview.revision,
          ...payload,
        }),
      );
      onClose();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? [e.message, ...e.details.map((d) => d.message)].join(' ')
          : 'Could not save corrections.',
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title="Correct all review rows"
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <form className="editor" onSubmit={save}>
        <p>
          Review the values below, then save all corrections together. Missing or conflicting values
          will remain flagged for review.
        </p>
        {error && (
          <p className="notice bad" role="alert">
            {error}
          </p>
        )}
        {isFlight && (
          <label>
            Heavy classification for all flights
            <select
              aria-label="Heavy classification for all flights"
              defaultValue="keep"
              onChange={(e) => {
                if (e.target.value !== 'keep')
                  setFlightRows((rows) =>
                    rows.map((r) => ({ ...r, heavy: e.target.value === 'heavy' })),
                  );
              }}
            >
              <option value="keep">Keep each flight's current classification</option>
              <option value="normal">Set all to normal</option>
              <option value="heavy">Set all to heavy</option>
            </select>
          </label>
        )}
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>{isFlight ? 'Flight' : 'Ramp agent'}</th>
                <th>{isFlight ? 'Arrival' : 'Shift start'}</th>
                <th>{isFlight ? 'Departure' : 'Shift end'}</th>
                <th>{isFlight ? 'Heavy' : 'Qualifications'}</th>
                <th>Exclude</th>
              </tr>
            </thead>
            <tbody>
              {isFlight
                ? flightRows.map((r, index) => (
                    <tr key={r.row_id}>
                      <td>{r.label}</td>
                      <td>
                        <input
                          aria-label={`${r.label} arrival`}
                          value={r.arrival_time}
                          onChange={(e) =>
                            setFlightRows((rows) =>
                              rows.map((x, i) =>
                                i === index ? { ...x, arrival_time: e.target.value } : x,
                              ),
                            )
                          }
                        />
                      </td>
                      <td>
                        <input
                          aria-label={`${r.label} departure`}
                          value={r.departure_time}
                          onChange={(e) =>
                            setFlightRows((rows) =>
                              rows.map((x, i) =>
                                i === index ? { ...x, departure_time: e.target.value } : x,
                              ),
                            )
                          }
                        />
                      </td>
                      <td>
                        <input
                          aria-label={`${r.label} heavy`}
                          type="checkbox"
                          checked={r.heavy}
                          onChange={(e) =>
                            setFlightRows((rows) =>
                              rows.map((x, i) =>
                                i === index ? { ...x, heavy: e.target.checked } : x,
                              ),
                            )
                          }
                        />
                      </td>
                      <td>
                        <input
                          aria-label={`Exclude ${r.label}`}
                          type="checkbox"
                          checked={r.excluded}
                          onChange={(e) =>
                            setFlightRows((rows) =>
                              rows.map((x, i) =>
                                i === index ? { ...x, excluded: e.target.checked } : x,
                              ),
                            )
                          }
                        />
                      </td>
                    </tr>
                  ))
                : employeeRows.map((r, index) => (
                    <tr key={r.row_id}>
                      <td>
                        <strong>{r.name}</strong>
                        {!r.employee_id && (
                          <select
                            aria-label={`Match ${r.name}`}
                            value={r.employee_id}
                            onChange={(e) =>
                              setEmployeeRows((rows) =>
                                rows.map((x, i) =>
                                  i === index
                                    ? {
                                        ...x,
                                        employee_id: e.target.value,
                                        qualifications: [
                                          ...(record.preview.employees.find(
                                            (emp) => emp.employee_id === e.target.value,
                                          )?.qualifications || []),
                                        ],
                                      }
                                    : x,
                                ),
                              )
                            }
                          >
                            <option value="">Select employee</option>
                            {record.preview.roster.map((e) => (
                              <option key={e.employee_id} value={e.employee_id}>
                                {e.name}
                              </option>
                            ))}
                          </select>
                        )}
                      </td>
                      <td>
                        <input
                          aria-label={`${r.name} shift start`}
                          value={r.start}
                          onChange={(e) =>
                            setEmployeeRows((rows) =>
                              rows.map((x, i) =>
                                i === index ? { ...x, start: e.target.value } : x,
                              ),
                            )
                          }
                        />
                      </td>
                      <td>
                        <input
                          aria-label={`${r.name} shift end`}
                          value={r.end}
                          onChange={(e) =>
                            setEmployeeRows((rows) =>
                              rows.map((x, i) => (i === index ? { ...x, end: e.target.value } : x)),
                            )
                          }
                        />
                      </td>
                      <td>
                        {(['PUSH', 'CLOSE_OUT'] as const).map((q) => (
                          <label key={q} className="check">
                            <input
                              aria-label={`${r.name} ${q}`}
                              type="checkbox"
                              checked={r.qualifications.includes(q)}
                              onChange={(e) =>
                                setEmployeeRows((rows) =>
                                  rows.map((x) =>
                                    x.employee_id === r.employee_id && r.employee_id
                                      ? {
                                          ...x,
                                          qualifications: e.target.checked
                                            ? [...new Set([...x.qualifications, q])]
                                            : x.qualifications.filter((value) => value !== q),
                                        }
                                      : x,
                                  ),
                                )
                              }
                            />
                            {q === 'PUSH' ? 'Push' : 'Close'}
                          </label>
                        ))}
                      </td>
                      <td>
                        <input
                          aria-label={`Exclude ${r.name}`}
                          type="checkbox"
                          checked={r.excluded}
                          onChange={(e) =>
                            setEmployeeRows((rows) =>
                              rows.map((x, i) =>
                                i === index ? { ...x, excluded: e.target.checked } : x,
                              ),
                            )
                          }
                        />
                      </td>
                    </tr>
                  ))}
            </tbody>
          </table>
        </div>
        <p className="muted">
          Times retain their supplied dates and offsets. Qualifications must be verified; workbook
          names do not establish push or close qualification.
        </p>
        <footer className="modal-actions">
          <button type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button
            className="primary"
            disabled={busy || !(isFlight ? flightRows.length : employeeRows.length)}
          >
            {busy ? 'Saving…' : 'Save all corrections'}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
