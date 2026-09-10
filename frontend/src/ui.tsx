import { useEffect, useRef, type ReactNode } from 'react';
import { human, type Issue } from './api';

export function Badge({
  children,
  tone = 'neutral',
  title,
}: {
  children: ReactNode;
  tone?: string;
  title?: string;
}) {
  return (
    <span className={`badge ${tone}`} title={title}>
      {children}
    </span>
  );
}
export function Coverage({ value }: { value?: boolean | null }) {
  return (
    <Badge tone={value === true ? 'good' : value === false ? 'bad' : 'neutral'}>
      {value === true ? 'Yes' : value === false ? 'Missing' : '—'}
    </Badge>
  );
}
export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current!;
    dialog.showModal();
    return () => dialog.close();
  }, []);
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      aria-label={title}
    >
      <header className="modal-header">
        <h2>{title}</h2>
        <button onClick={onClose} aria-label="Close dialog">
          Close
        </button>
      </header>
      {children}
    </dialog>
  );
}
export function Issues({ issues, evaluated }: { issues: Issue[]; evaluated: boolean }) {
  const ordered = [...issues].sort(
    (a, b) =>
      Number(['CRITICAL', 'ERROR', 'FATAL'].includes(b.severity || '')) -
      Number(['CRITICAL', 'ERROR', 'FATAL'].includes(a.severity || '')),
  );
  return (
    <section className="surface issues">
      <div className="section-heading">
        <h2>Operational issues</h2>
        <span className="count">{issues.length}</span>
      </div>
      {ordered.length ? (
        <ul>
          {ordered.map((issue, i) => (
            <li
              key={`${issue.code}-${i}`}
              className={
                ['CRITICAL', 'ERROR', 'FATAL'].includes(issue.severity || '')
                  ? 'issue-hard'
                  : 'issue-soft'
              }
            >
              <Badge
                tone={
                  ['CRITICAL', 'ERROR', 'FATAL'].includes(issue.severity || '') ? 'bad' : 'warn'
                }
              >
                {issue.severity ? human(issue.severity) : 'Input warning'}
              </Badge>
              <strong>
                {issue.arrival_flight_number || issue.departure_flight_number
                  ? `Flight ${issue.arrival_flight_number || issue.departure_flight_number}`
                  : issue.employee_id || issue.path || human(issue.code)}
              </strong>
              <p>{issue.message}</p>
            </li>
          ))}
        </ul>
      ) : (
        <p className="empty compact">
          {evaluated
            ? 'No issues reported for this input and result.'
            : 'Load an operational day to review validation issues.'}
        </p>
      )}
    </section>
  );
}
