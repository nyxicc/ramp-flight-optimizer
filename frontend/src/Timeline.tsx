import { flightLabel, time, type Day, type Result } from './api';

export function Timeline({ day, result }: { day: Day; result: Result | null }) {
  const shifts = (day.employee_shifts || []).filter((s) => s.normalized_role === 'RAMP_AGENT');
  if (!result || !shifts.length)
    return (
      <section className="surface">
        <p className="empty">Optimize the loaded input to view assignment work windows.</p>
      </section>
    );
  const start = Math.min(...shifts.map((s) => Date.parse(s.start)));
  const end = Math.max(...shifts.map((s) => Date.parse(s.end)));
  const pct = (n: number) => `${((n - start) / (end - start)) * 100}%`;
  return (
    <section className="surface">
      <div className="section-heading">
        <h2>Employee timeline</h2>
        <span className="muted">Assignment work windows · shift boundaries outlined</span>
      </div>
      <div className="timeline-scroll">
        <div className="timeline">
          <div className="timeline-row">
            <strong>Employee</strong>
            <div className="timeline-axis">
              {Array.from({ length: 7 }, (_, i) => (
                <span key={i} style={{ left: `${(i / 6) * 100}%` }}>
                  {new Date(start + ((end - start) * i) / 6).toISOString().slice(11, 16)}Z
                </span>
              ))}
            </div>
          </div>
          {day.employees
            ?.filter((e) => shifts.some((s) => s.employee_id === e.employee_id))
            .map((e) => (
              <div className="timeline-row" key={e.employee_id}>
                <strong>{e.name}</strong>
                <div className="timeline-track">
                  {shifts
                    .filter((s) => s.employee_id === e.employee_id)
                    .map((s, i) => (
                      <div
                        className="shift-block"
                        key={i}
                        style={{
                          left: pct(Date.parse(s.start)),
                          width: `${((Date.parse(s.end) - Date.parse(s.start)) / (end - start)) * 100}%`,
                        }}
                        title={`Shift ${s.start} to ${s.end}`}
                      />
                    ))}
                  {result.result.flight_results
                    .filter((f) => f.assigned_employee_ids.includes(e.employee_id))
                    .map((f) => (
                      <div
                        tabIndex={0}
                        className={`assignment-block ${f.fixed_employee_ids.includes(e.employee_id) ? 'fixed' : ''}`}
                        key={flightLabel(f.flight)}
                        style={{
                          left: pct(Date.parse(f.work_start)),
                          width: `${((Date.parse(f.work_end) - Date.parse(f.work_start)) / (end - start)) * 100}%`,
                        }}
                        title={`${flightLabel(f.flight)} · ${time(f.work_start)}–${time(f.work_end)} airport local`}
                      >
                        {flightLabel(f.flight)}
                      </div>
                    ))}
                </div>
              </div>
            ))}
        </div>
      </div>
      <footer className="surface-footer">
        Axis uses UTC (Z) to preserve alignment across offsets. Hover or focus assignments for
        airport-local times. Exact break placement is not supplied; consult workforce break
        assessments.
      </footer>
    </section>
  );
}
