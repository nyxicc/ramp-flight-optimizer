import { flightLabel, type ImportRecord } from './api';

// Older saved previews may contain messages from the previous import policy.
// Keep their audit records intact while presenting the current review guidance.
export function importIssues(record: ImportRecord) {
  return record.preview.issues
    .filter((i) => !['HEAVY_UNAVAILABLE', 'ONWARD_TIME_IGNORED'].includes(i.code))
    .filter((i) => !(i.code === 'STATUS_REQUIRES_REVIEW' && record.preview.flight_rows?.some(r => r.source_row === i.source_row && r.status === 'AOG')))
    .map((i) => {
      if (i.code !== 'STATUS_REQUIRES_REVIEW') return i;
      const row = record.preview.flight_rows?.find((r) => r.source_row === i.source_row);
      if (!row) return i;
      const reasons = {
        UNKNOWN:
          'The workbook status is not recognized. Select the intended flight status before including this movement.',
        TERMINATING:
          'This row is marked terminating but also lists a departure flight. Confirm whether it is a turn or an arrival-only movement.',
      };
      const reason = reasons[row.status as keyof typeof reasons];
      return reason
        ? {
            ...i,
            message: `Flight ${flightLabel(row.flight)}: ${reason}`,
            remediation: 'Use Correct to review the flight status.',
          }
        : i;
    });
}
