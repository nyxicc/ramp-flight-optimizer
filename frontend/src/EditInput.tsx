import { useState, type FormEvent } from 'react';
import { flightKey, flightLabel, human, type Input, type Version } from './api';
import { Modal } from './ui';

export type Editor = { kind: 'flight' | 'shift'; index: number } | { kind: 'settings' };
export function EditInput({
  version,
  baseline,
  target,
  busy,
  error,
  onSave,
  onClose,
}: {
  version: Version;
  baseline?: Input;
  target: Editor;
  busy: boolean;
  error?: string;
  onSave: (input: Input, reason: string) => Promise<boolean | undefined>;
  onClose: () => void;
}) {
  const [input, setInput] = useState<Input>(() => structuredClone(version.input));
  const [reason, setReason] = useState('');
  const day = input.operational_day;
  const flight = target.kind === 'flight' ? day.flights?.[target.index] : undefined;
  const shift = target.kind === 'shift' ? day.employee_shifts?.[target.index] : undefined;
  const originalFlight =
    flight && baseline?.operational_day.flights?.find((f) => flightKey(f) === flightKey(flight));
  const originalShift =
    target.kind === 'shift' ? baseline?.operational_day.employee_shifts?.[target.index] : undefined;
  const title = flight
    ? `Edit flight ${flightLabel(flight)}`
    : shift
      ? `Edit shift · ${day.employees?.find((e) => e.employee_id === shift.employee_id)?.name || shift.employee_id}`
      : 'Optimization settings';
  function update(change: (draft: Input) => void) {
    setInput((prev) => {
      const next = structuredClone(prev);
      change(next);
      return next;
    });
  }
  async function submit(e: FormEvent) {
    e.preventDefault();
    if (await onSave(input, reason.trim())) onClose();
  }
  return (
    <Modal
      title={title}
      onClose={() => {
        if (!busy) onClose();
      }}
    >
      <form onSubmit={submit} className="editor">
        {error && (
          <p className="notice bad" role="alert">
            {error}
          </p>
        )}
        <p className="muted">
          Changes create a new input version. Existing optimization results remain attached to their
          original input.
        </p>
        {flight && target.kind === 'flight' && (
          <>
            <div className="form-grid">
              {(['arrival_time', 'departure_time'] as const).map(
                (field) =>
                  flight[field] && (
                    <label key={field}>
                      {human(field)}
                      <input
                        required
                        value={flight[field] || ''}
                        onChange={(e) =>
                          update((d) => {
                            d.operational_day.flights![target.index][field] = e.target.value;
                          })
                        }
                      />
                      <small>
                        Include date, time and UTC offset. Original:{' '}
                        {originalFlight?.[field] || 'No earlier value'}
                      </small>
                    </label>
                  ),
              )}
              <label className="check">
                <input
                  type="checkbox"
                  checked={flight.heavy || false}
                  onChange={(e) =>
                    update((d) => {
                      d.operational_day.flights![target.index].heavy = e.target.checked;
                    })
                  }
                />
                Heavy flight
              </label>
            </div>
            <fieldset>
              <legend>Fixed employee assignments</legend>
              <p className="muted">
                Selected employees are required on this flight. The optimizer may add other
                employees. The server validates eligibility and conflicts.
              </p>
              <div className="assignment-picker">
                {day.employees?.map((e) => (
                  <label className="check" key={e.employee_id}>
                    <input
                      type="checkbox"
                      checked={
                        day.fixed_assignments?.some(
                          (a) =>
                            a.employee_id === e.employee_id &&
                            flightKey(a.flight) === flightKey(flight),
                        ) || false
                      }
                      onChange={(event) =>
                        update((d) => {
                          d.operational_day.fixed_assignments = (
                            d.operational_day.fixed_assignments || []
                          ).filter(
                            (a) =>
                              !(
                                a.employee_id === e.employee_id &&
                                flightKey(a.flight) === flightKey(flight)
                              ),
                          );
                          if (event.target.checked)
                            d.operational_day.fixed_assignments.push({
                              employee_id: e.employee_id,
                              flight: {
                                arrival_flight_number: flight.arrival_flight_number,
                                departure_flight_number: flight.departure_flight_number,
                              },
                            });
                        })
                      }
                    />
                    {e.name}
                    <small>{e.qualifications?.map(human).join(' · ') || 'No qualifications'}</small>
                  </label>
                ))}
              </div>
            </fieldset>
            {originalFlight && (
              <button
                type="button"
                disabled={busy}
                onClick={() => {
                  if (
                    confirm(
                      'Restore this flight and its fixed assignments to the original loaded version? Save the revision to apply.',
                    )
                  )
                    update((d) => {
                      d.operational_day.flights![target.index] = structuredClone(originalFlight);
                      d.operational_day.fixed_assignments = [
                        ...(d.operational_day.fixed_assignments || []).filter(
                          (a) => flightKey(a.flight) !== flightKey(flight),
                        ),
                        ...(baseline?.operational_day.fixed_assignments || []).filter(
                          (a) => flightKey(a.flight) === flightKey(flight),
                        ),
                      ];
                    });
                }}
              >
                Restore original flight values
              </button>
            )}
          </>
        )}
        {shift && target.kind === 'shift' && (
          <>
            <div className="form-grid">
              {(['start', 'end'] as const).map((field) => (
                <label key={field}>
                  Shift {field}
                  <input
                    required
                    value={shift[field]}
                    onChange={(e) =>
                      update((d) => {
                        d.operational_day.employee_shifts![target.index][field] = e.target.value;
                      })
                    }
                  />
                  <small>
                    Include date, time and UTC offset. Original:{' '}
                    {originalShift?.[field] || 'No earlier value'}
                  </small>
                </label>
              ))}
            </div>
            {originalShift && (
              <button
                type="button"
                onClick={() => {
                  if (confirm('Restore the original shift? Save the revision to apply.'))
                    update((d) => {
                      d.operational_day.employee_shifts![target.index] =
                        structuredClone(originalShift);
                    });
                }}
              >
                Restore original shift
              </button>
            )}
          </>
        )}
        {target.kind === 'settings' && (
          <>
            <p>
              Narrow-body domestic operation. At least one push-qualified and one close-qualified
              employee are required where applicable; the same employee can cover both. Leads are
              fallback resources.
            </p>
            <div className="form-grid settings-grid">
              {Object.entries(input.config || {}).map(([key, value]) => (
                <label key={key} className={typeof value === 'boolean' ? 'check' : ''}>
                  {typeof value === 'boolean' ? (
                    <>
                      <input
                        type="checkbox"
                        checked={value}
                        onChange={(e) =>
                          update((d) => {
                            d.config = { ...d.config!, [key]: e.target.checked };
                          })
                        }
                      />
                      {human(key)}
                    </>
                  ) : (
                    <>
                      {human(key)}
                      <input
                        type="number"
                        required
                        step="any"
                        value={value}
                        onChange={(e) =>
                          update((d) => {
                            d.config = {
                              ...d.config!,
                              [key]: e.target.value === '' ? undefined : Number(e.target.value),
                            };
                          })
                        }
                      />
                    </>
                  )}
                </label>
              ))}
            </div>
            <p className="muted">
              These are the saved server settings. Three-person teams are a constrained-staffing
              compromise; heavy flights prefer additional staffing. All scheduling and rule
              validation runs in the operations service.
            </p>
          </>
        )}
        <label>
          Reason for change
          <input
            required
            maxLength={1000}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Briefly explain the operational change"
          />
        </label>
        <footer className="modal-actions">
          <button type="button" disabled={busy} onClick={onClose}>
            Cancel
          </button>
          <button className="primary" disabled={busy || !reason.trim()}>
            {busy ? 'Validating…' : 'Validate & save revision'}
          </button>
        </footer>
      </form>
    </Modal>
  );
}
