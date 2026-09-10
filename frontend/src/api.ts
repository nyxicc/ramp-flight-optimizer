import type { components } from './schema';
export type Schema = components['schemas'];
export type Version = Schema['InputVersionResponse'];
export type Input = Schema['OptimizationRequest'];
export type Day = Schema['OperationalDayRequest'];
export type Flight = Schema['FlightRequest'];
export type Job = Schema['JobResponse'];
export type Result = Schema['JobResultResponse'];
export type ImportRecord = Schema['ImportResponse'];
export type Issue = {
  code: string;
  message: string;
  path?: string;
  severity?: string;
  employee_id?: string | null;
  arrival_flight_number?: string | null;
  departure_flight_number?: string | null;
};

export class ApiError extends Error {
  constructor(
    message: string,
    public details: Issue[] = [],
    public status = 0,
  ) {
    super(message);
  }
}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/v1${path}`, {
      ...options,
      headers:
        options.body instanceof FormData
          ? options.headers
          : { 'Content-Type': 'application/json', ...options.headers },
    });
  } catch (error) {
    if (error instanceof Error && error.name === 'AbortError') throw error;
    throw new ApiError('Cannot reach the operations service. Check the connection and retry.');
  }
  const body = await response.json().catch(() => null);
  if (!response.ok)
    throw new ApiError(
      body?.error?.message || `Operations service returned ${response.status}.`,
      body?.error?.details || [],
      response.status,
    );
  return body as T;
}
export const post = <T>(path: string, body: unknown) =>
  api<T>(path, { method: 'POST', body: JSON.stringify(body) });
export async function versionsForDate(date: string, signal?: AbortSignal): Promise<Version[]> {
  const rows: Version[] = [];
  for (let offset = 0; ; offset += 100) {
    const page = await api<Version[]>(
      `/operational-days/${date}/versions?limit=100&offset=${offset}`,
      { signal },
    );
    rows.push(...page);
    if (page.length < 100) return rows;
  }
}
type FlightIdentity = Pick<Flight, 'arrival_flight_number' | 'departure_flight_number'>;
export const flightKey = (f: FlightIdentity) =>
  `${f.arrival_flight_number || ''}|${f.departure_flight_number || ''}`;
export const flightLabel = (f: FlightIdentity) =>
  [f.arrival_flight_number, f.departure_flight_number].filter(Boolean).join(' / ');
export const human = (s: string) =>
  s
    .toLowerCase()
    .replaceAll('_', ' ')
    .replace(/^./, (c) => c.toUpperCase());
// Preserve the airport-local clock and offset supplied by the backend. Never convert to the browser timezone.
export const time = (s?: string | null) => (s ? s.slice(11, 16) : '—');
export const activeJob = (s?: string) => ['QUEUED', 'RUNNING', 'CANCELLING'].includes(s || '');
export const newKey = () => crypto.randomUUID();
