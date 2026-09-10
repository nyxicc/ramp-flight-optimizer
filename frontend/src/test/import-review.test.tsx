// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { BulkImportReview } from '../BulkImportReview';
import { Workforce } from '../Schedule';
import type { Day, ImportRecord } from '../api';
import { importIssues } from '../importIssues';

it('hides obsolete warnings and ignores AOG on saved imports', () => {
  const record = {
    preview: {
      issues: [
        { code: 'HEAVY_UNAVAILABLE' },
        { code: 'ONWARD_TIME_IGNORED' },
        { code: 'STATUS_REQUIRES_REVIEW', source_row: 3, message: 'Resolve status.' },
      ],
      flight_rows: [{ source_row: 3, status: 'AOG', flight: { departure_flight_number: '1134' } }],
    },
  } as unknown as ImportRecord;
  const issues = importIssues(record);
  expect(issues).toHaveLength(0);
});

beforeEach(() => {
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.open = true;
    },
  });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', {
    configurable: true,
    value: function (this: HTMLDialogElement) {
      this.open = false;
    },
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it('saves all reviewed flights as a single revision without guessing missing times', async () => {
  const record = {
    import_id: 'import-1',
    import_type: 'DAILY_FLIGHT_LOG',
    preview: {
      revision: 7,
      rows: [],
      flight_rows: [
        {
          row_id: 'row-1',
          will_be_excluded: false,
          flight: {
            arrival_flight_number: '101',
            arrival_time: '2035-04-15T10:00:00-05:00',
            heavy: false,
          },
        },
        {
          row_id: 'row-2',
          will_be_excluded: false,
          flight: { arrival_flight_number: '103', arrival_time: null, heavy: false },
        },
      ],
    },
  } as unknown as ImportRecord;
  const fetch = vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue(new Response(JSON.stringify(record), { status: 200 }));
  const saved = vi.fn();
  render(<BulkImportReview record={record} onSaved={saved} onClose={() => {}} />);
  await userEvent.selectOptions(
    screen.getByLabelText('Heavy classification for all flights'),
    'heavy',
  );
  await userEvent.click(screen.getByRole('button', { name: 'Save all corrections' }));
  expect(fetch).toHaveBeenCalledTimes(1);
  const payload = JSON.parse(fetch.mock.calls[0][1]?.body as string);
  expect(payload.revision).toBe(7);
  expect(payload.flight_corrections).toHaveLength(2);
  expect(payload.flight_corrections[0].heavy).toBe(true);
  expect(payload.flight_corrections[1].heavy).toBe(true);
  expect(payload.flight_corrections[1]).not.toHaveProperty('arrival_time');
  expect(saved).toHaveBeenCalledOnce();
});

it('shows ramp-agent names and excludes all other positions from workforce', () => {
  const day: Day = {
    operational_date: '2035-04-15',
    flights: [],
    fixed_assignments: [],
    employees: [
      { employee_id: 'agent', name: 'Jordan Example', qualifications: [], enabled: true },
      { employee_id: 'lead', name: 'Lead Example', qualifications: [], enabled: true },
    ],
    employee_shifts: [
      {
        employee_id: 'agent',
        normalized_role: 'RAMP_AGENT',
        start: '2035-04-15T05:00:00-05:00',
        end: '2035-04-15T13:00:00-05:00',
      },
      {
        employee_id: 'lead',
        normalized_role: 'RAMP_LEAD',
        start: '2035-04-15T05:00:00-05:00',
        end: '2035-04-15T13:00:00-05:00',
      },
    ],
  };
  render(
    <Workforce
      day={day}
      selected={null}
      result={null}
      editable
      onEdit={() => {}}
      onSelect={() => {}}
    />,
  );
  expect(screen.getByText('Jordan Example')).toBeTruthy();
  expect(screen.queryByText('Lead Example')).toBeNull();
  expect(screen.getByText('05:00–13:00')).toBeTruthy();
});
