import { useState } from 'react';
import { flightKey, flightLabel, human, time, type Day, type Flight, type Result } from './api';
import { Badge, Coverage } from './ui';

type Props = {
  day: Day;
  result?: Result | null;
  baseline?: Day;
  onEdit: (index: number) => void;
  onEmployee: (id: string) => void;
  editable: boolean;
};
export function Schedule({ day, result, baseline, onEdit, onEmployee, editable }: Props) {
  const [search, setSearch] = useState('');
  const [filter, setFilter] = useState('all');
  const [sort, setSort] = useState<'time' | 'flight'>('time');
  const [descending, setDescending] = useState(false);
  const flights = day.flights || [];
  const rows = flights
    .map((flight, index) => ({
      flight,
      index,
      assignment: result?.result.flight_results.find(
        (r) => flightKey(r.flight) === flightKey(flight),
      ),
    }))
    .filter(
      ({ flight, assignment: a }) =>
        `${flightLabel(flight)} ${flight.gate || ''}`
          .toLowerCase()
          .includes(search.toLowerCase()) &&
        (filter === 'all' ||
          (filter === 'heavy' && flight.heavy) ||
          (filter === 'under' && a && !a.preferred_met) ||
          (filter === 'qual' && a && (a.push_covered === false || a.close_covered === false))),
    )
    .sort(
      (a, b) =>
        (sort === 'time'
          ? Date.parse(a.flight.arrival_time || a.flight.departure_time || '') -
            Date.parse(b.flight.arrival_time || b.flight.departure_time || '')
          : flightLabel(a.flight).localeCompare(flightLabel(b.flight), undefined, {
              numeric: true,
            })) * (descending ? -1 : 1),
    );
  function order(column: 'time' | 'flight') {
    setDescending(sort === column ? !descending : false);
    setSort(column);
  }
  return (
    <section className="surface schedule">
      <div className="section-heading">
        <h2>Flight schedule</h2>
        <span className="muted">
          {rows.length} of {flights.length} movements
        </span>
        <span className="spacer" />
        <span className="muted">Times as imported · airport local</span>
      </div>
      <div className="toolbar">
        <input
          aria-label="Search flights"
          placeholder="Search flight or gate"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <select
          aria-label="Flight filter"
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
        >
          <option value="all">All flights</option>
          <option value="heavy">Heavy flights</option>
          <option value="under">Below preferred staffing</option>
          <option value="qual">Qualification conflicts</option>
        </select>
        <span className="spacer" />
        <Badge tone="info">M · Manual override</Badge>
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th
                aria-sort={sort === 'flight' ? (descending ? 'descending' : 'ascending') : 'none'}
              >
                <button onClick={() => order('flight')}>
                  Flight {sort === 'flight' ? (descending ? '↓' : '↑') : ''}
                </button>
              </th>
              <th aria-sort={sort === 'time' ? (descending ? 'descending' : 'ascending') : 'none'}>
                <button onClick={() => order('time')}>
                  Arrival {sort === 'time' ? (descending ? '↓' : '↑') : ''}
                </button>
              </th>
              <th>Departure</th>
              <th>Gate</th>
              <th title="Aircraft type is not supplied by the current input contract">Aircraft</th>
              <th>Heavy</th>
              <th>Min / pref</th>
              <th>Assigned</th>
              <th>Push</th>
              <th>Close</th>
              <th className="team-column">Team members</th>
              <th>Staffing status</th>
              <th>Warnings</th>
              <th>
                <span className="sr-only">Actions</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ flight: f, assignment: a, index }) => {
              const original = baseline?.flights?.find((x) => flightKey(x) === flightKey(f));
              const manual =
                (original && JSON.stringify(original) !== JSON.stringify(f)) ||
                (a?.fixed_employee_ids.length || 0) > 0 ||
                day.fixed_assignments?.some((x) => flightKey(x.flight) === flightKey(f));
              const bad =
                a && (!a.minimum_met || a.push_covered === false || a.close_covered === false);
              const inProgress =
                a && Date.now() >= Date.parse(a.work_start) && Date.now() < Date.parse(a.work_end);
              return (
                <tr
                  key={flightKey(f)}
                  className={
                    bad
                      ? 'row-bad'
                      : a && !a.preferred_met
                        ? 'row-warn'
                        : manual
                          ? 'row-manual'
                          : f.heavy
                            ? 'row-heavy'
                            : ''
                  }
                >
                  <td className="mono flight-id">
                    {flightLabel(f)}{' '}
                    {manual && (
                      <Badge tone="info" title="Manually revised input or fixed assignment">
                        M
                      </Badge>
                    )}
                    {inProgress && <small>In progress</small>}
                  </td>
                  <td className="mono" title={f.arrival_time || undefined}>
                    {time(f.arrival_time)}
                  </td>
                  <td className="mono" title={f.departure_time || undefined}>
                    {time(f.departure_time)}
                  </td>
                  <td>{f.gate || '—'}</td>
                  <td className="muted" title="Not supplied">
                    —
                  </td>
                  <td>
                    {f.heavy ? (
                      <Badge title="Heavy-flight preference is defined by the server configuration">
                        H
                      </Badge>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td className="mono">{a ? `${a.minimum_staff} / ${a.preferred_staff}` : '—'}</td>
                  <td className="mono strong">{a?.staffing_count ?? '—'}</td>
                  <td>
                    <Coverage value={a?.push_covered} />
                  </td>
                  <td>
                    <Coverage value={a?.close_covered} />
                  </td>
                  <td className="team-column">
                    {a ? (
                      a.assigned_employee_ids.length ? (
                        a.assigned_employee_ids.map((id) => (
                          <button
                            className="text-button employee-link"
                            key={id}
                            onClick={() => onEmployee(id)}
                          >
                            {day.employees?.find((e) => e.employee_id === id)?.name || id}
                            {a.fixed_employee_ids.includes(id) ? ' · M' : ''}
                          </button>
                        ))
                      ) : (
                        <span className="muted">Unassigned</span>
                      )
                    ) : (
                      <span className="muted">Awaiting optimization</span>
                    )}
                  </td>
                  <td>
                    <Badge tone={!a ? 'neutral' : bad ? 'bad' : a.preferred_met ? 'good' : 'warn'}>
                      {a ? human(a.staffing_status) : 'Not evaluated'}
                    </Badge>
                  </td>
                  <td>
                    {a?.warnings.length ? (
                      <span title={a.warnings.map((w) => w.message).join('\n')}>
                        {a.warnings.length} issue{a.warnings.length !== 1 ? 's' : ''}
                      </span>
                    ) : (
                      '—'
                    )}
                  </td>
                  <td>
                    <button disabled={!editable} onClick={() => onEdit(index)}>
                      Edit
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!rows.length && (
          <p className="empty">
            {flights.length
              ? 'No flights match these filters.'
              : 'No flight data loaded. Use Import / load data to select a saved snapshot or review an import.'}
          </p>
        )}
      </div>
    </section>
  );
}

export function Workforce({
  day,
  baseline,
  result,
  onEdit,
  selected,
  onSelect,
  editable,
}: {
  day: Day;
  baseline?: Day;
  result?: Result | null;
  onEdit: (index: number) => void;
  selected: string | null;
  onSelect: (id: string | null) => void;
  editable: boolean;
}) {
  const [search, setSearch] = useState('');
  const rampIds = new Set(
    day.employee_shifts.filter((s) => s.normalized_role === 'RAMP_AGENT').map((s) => s.employee_id),
  );
  const employees = (day.employees || []).filter(
    (e) =>
      rampIds.has(e.employee_id) &&
      `${e.name} ${e.employee_id}`.toLowerCase().includes(search.toLowerCase()) &&
      (!selected || e.employee_id === selected),
  );
  return (
    <section className="surface workforce">
      <div className="section-heading">
        <h2>Workforce</h2>
        <span className="count">{rampIds.size}</span>
        <span className="spacer" />
        <input
          aria-label="Search employees"
          placeholder="Search employees"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        {selected && <button onClick={() => onSelect(null)}>Show all employees</button>}
      </div>
      <div className="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Employee</th>
              <th>Role</th>
              <th>Shift</th>
              <th>Push</th>
              <th>Close</th>
              <th>Flights</th>
              <th>Workload</th>
              <th>Consecutive</th>
              <th>Break assessment</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {employees.map((e) => {
              const r = result?.result.employee_results.find(
                (x) => x.employee_id === e.employee_id,
              );
              const shifts = (day.employee_shifts || [])
                .map((s, index) => ({ ...s, index }))
                .filter(
                  (s) => s.employee_id === e.employee_id && s.normalized_role === 'RAMP_AGENT',
                );
              return (
                <tr key={e.employee_id}>
                  <td>
                    <strong>{e.name}</strong>
                    <small className="muted mono">{e.employee_id}</small>
                  </td>
                  <td>
                    {[...new Set(shifts.map((s) => s.normalized_role))].map((role) => (
                      <div key={role}>
                        {role === 'RAMP_LEAD' ? (
                          <Badge
                            tone="warn"
                            title="Fallback resource; the server controls emergency minimum-staffing recovery"
                          >
                            Lead · fallback
                          </Badge>
                        ) : (
                          human(role)
                        )}
                      </div>
                    ))}
                  </td>
                  <td className="mono">
                    {shifts.length
                      ? shifts.map((s) => (
                          <div key={s.index}>
                            <button
                              className="text-button"
                              disabled={!editable}
                              onClick={() => onEdit(s.index)}
                              title={`${s.start} — ${s.end}; edit shift`}
                            >
                              {time(s.start)}–{time(s.end)}
                            </button>
                            {baseline &&
                              !baseline.employee_shifts?.some(
                                (original) =>
                                  original.employee_id === s.employee_id &&
                                  original.start === s.start &&
                                  original.end === s.end,
                              ) && (
                                <Badge tone="info" title="Manually revised shift">
                                  M
                                </Badge>
                              )}
                          </div>
                        ))
                      : 'No shift'}
                  </td>
                  <td>
                    <Coverage value={e.qualifications?.includes('PUSH')} />
                  </td>
                  <td>
                    <Coverage value={e.qualifications?.includes('CLOSE_OUT')} />
                  </td>
                  <td className="mono">{r?.flight_count ?? '—'}</td>
                  <td className="mono">{r?.adjusted_workload?.toFixed(2) ?? '—'}</td>
                  <td className="mono">{r?.longest_consecutive_streak ?? '—'}</td>
                  <td>
                    <Badge
                      tone={
                        r?.break_status === 'SATISFIED'
                          ? 'good'
                          : r?.break_status === 'UNSATISFIED'
                            ? 'bad'
                            : 'neutral'
                      }
                    >
                      {r ? human(r.break_status) : 'Not evaluated'}
                    </Badge>
                  </td>
                  <td>{!e.enabled ? 'Disabled' : shifts.length ? 'Rostered' : 'No shift'}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
        {!employees.length && <p className="empty">No employees match this view.</p>}
      </div>
      <footer className="surface-footer">
        Workload and break assessments come from the optimizer. Exact break times and live employee
        availability are not supplied.
      </footer>
    </section>
  );
}
