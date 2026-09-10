import { useState, type FormEvent } from 'react';
import { ApiError, human, post, type ImportRecord } from './api';
import { Modal } from './ui';

export function ImportRowEditor({
  record,
  rowId,
  onSaved,
  onClose,
}: {
  record: ImportRecord;
  rowId: string;
  onSaved: (record: ImportRecord) => void;
  onClose: () => void;
}) {
  const flight = record.preview.flight_rows?.find((r) => r.row_id === rowId);
  const employee = record.preview.rows.find((r) => r.row_id === rowId);
  const [changes, setChanges] = useState<Record<string, string | boolean>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const values = flight ? { ...flight.flight, excluded: flight.excluded } : employee;
  if (!values) return null;
  const fields = flight ? ['arrival_time', 'departure_time', 'gate'] : ['start', 'end'];
  async function save(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const patch = {
        row_id: rowId,
        ...changes,
        ...(flight ? { heavy: changes.heavy ?? flight.flight.heavy } : {}),
      };
      const updated = await post<ImportRecord>(`/imports/${record.import_id}/corrections`, {
        revision: record.preview.revision,
        ...(flight ? { flight_corrections: [patch] } : { corrections: [patch] }),
      });
      onSaved(updated);
      onClose();
    } catch (e) {
      setError(
        e instanceof ApiError
          ? [e.message, ...e.details.map((d) => d.message)].join(' ')
          : 'Could not save the correction.',
      );
    } finally {
      setBusy(false);
    }
  }
  return (
    <Modal
      title={`Correct import row ${flight?.source_row || employee?.source_row}`}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <form className="editor" onSubmit={save}>
        {error && (
          <p className="notice bad" role="alert">
            {error}
          </p>
        )}
        <p className="muted">
          Corrections are recorded against this import revision. Review the updated validation
          issues before confirming.
        </p>
        {employee && (
          <label>
            Authoritative employee
            <select
              value={String(changes.employee_id ?? employee.employee_id ?? '')}
              onChange={(e) => setChanges({ ...changes, employee_id: e.target.value })}
            >
              <option value="" disabled>
                Select an employee
              </option>
              {record.preview.roster.map((e) => (
                <option key={e.employee_id} value={e.employee_id}>
                  {e.name} · {e.employee_id}
                </option>
              ))}
            </select>
          </label>
        )}
        <div className="form-grid">
          {fields.map((field) => {
            const original = (values as Record<string, unknown>)[field];
            if (
              (flight && field === 'arrival_time' && !flight.arrival_expected) ||
              (flight && field === 'departure_time' && !flight.departure_expected)
            )
              return null;
            return (
              <label key={field}>
                {human(field)}
                <input
                  value={String(changes[field] ?? original ?? '')}
                  onChange={(e) => setChanges({ ...changes, [field]: e.target.value })}
                  required={field !== 'gate'}
                />
                <small>
                  Original: {String(original ?? 'Not supplied')}
                  {field !== 'gate' && ' · Include date, time and UTC offset'}
                </small>
              </label>
            );
          })}
        </div>
        {employee && (
          <label>
            Operational role
            <select
              value={String(changes.normalized_role ?? employee.normalized_role)}
              onChange={(e) => setChanges({ ...changes, normalized_role: e.target.value })}
            >
              {[
                'RAMP_AGENT',
                'RAMP_LEAD',
                'POSSIBLE_RAMP_SUPPORT',
                'TRAINEE',
                'NON_RAMP',
                'UNKNOWN',
              ].map((role) => (
                <option key={role} value={role}>
                  {human(role)}
                </option>
              ))}
            </select>
          </label>
        )}
        {flight && (
          <label>
            Flight status
            <select
              aria-label="Flight status"
              value={String(changes.status ?? flight.status)}
              onChange={(e) => setChanges({ ...changes, status: e.target.value })}
            >
              <option value="NORMAL">Normal — include planned ramp service</option>
              <option value="TERMINATING">Terminating — arrival only</option>
              <option value="AOG">AOG — treated as normal</option>
              <option value="CANCELLED">Cancelled — exclude from schedule</option>
              <option value="UNKNOWN" disabled>
                Unrecognized workbook status
              </option>
            </select>
          </label>
        )}
        {flight && (
          <label className="check">
            <input
              type="checkbox"
              checked={Boolean(changes.heavy ?? flight.flight.heavy)}
              onChange={(e) => setChanges({ ...changes, heavy: e.target.checked })}
            />
            Heavy flight — leave unchecked for regular staffing
          </label>
        )}
        <label className="check">
          <input
            type="checkbox"
            checked={Boolean(changes.excluded ?? values.excluded)}
            onChange={(e) => {
              if (!e.target.checked || confirm('Exclude this source row from the confirmed input?'))
                setChanges({ ...changes, excluded: e.target.checked });
            }}
          />
          Exclude row from confirmed input
        </label>
        <footer className="modal-actions">
          <button type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy || (!flight && !Object.keys(changes).length)}>
            {busy ? 'Validating…' : 'Save correction'}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
