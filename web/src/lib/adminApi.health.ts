/**
 * AD-01-15 — `/admin/data-health` API client.
 *
 * Every `fetch(` call behind `app/admin/data-health/page.tsx` lives here —
 * the dashboard GET, the incident-workflow POSTs (unchanged DP-05-04
 * behaviour, just lifted out), and the two new AD-01-15 / SPEC-22 §3.4
 * endpoints:
 *
 *   GET /admin/data-health/tables         — nightly table census
 *   GET /admin/data-health/completeness   — nightly completeness snapshot
 *
 * Both of the new endpoints read ONLY the `admin_metrics` snapshot written
 * nightly by `irc-data compute-admin-metrics` (api/src/irc_data/cli.py) —
 * no on-request heavy query.
 *
 * Deliberately its own module, not an addition to `@/lib/adminApi.ts`
 * (AD-01-24's file — append-only, not touched by this card). Mirrors the
 * page's existing calling convention of an explicit `token` argument (read
 * from the page's own React state, not localStorage), so the "401 → drop
 * token → re-prompt" behaviour the page already implements is unchanged.
 */

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";
const PREFIX = `${API_BASE}/admin/data-health`;

export class DataHealthApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "DataHealthApiError";
    this.status = status;
  }
}

async function request<T>(
  path: string,
  token: string,
  init: RequestInit = {},
): Promise<T> {
  const res = await fetch(`${PREFIX}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${token}`,
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(init.headers ?? {}),
    },
  });
  if (!res.ok) {
    const payload = await res.json().catch(() => ({}) as { detail?: string });
    throw new DataHealthApiError(
      res.status,
      (payload as { detail?: string }).detail || `HTTP ${res.status}`,
    );
  }
  return (await res.json()) as T;
}

/* ── GET /dashboard (DP-05-04, unchanged shape) ─────────────────────────── */

export function fetchDataHealthDashboard<T = unknown>(
  token: string,
  windowDays?: number,
): Promise<T> {
  const qs = windowDays ? `?window_days=${windowDays}` : "";
  return request<T>(`/dashboard${qs}`, token);
}

/* ── Incident workflow (unchanged shape) ────────────────────────────────── */

export type IncidentAction = "acknowledge" | "mitigate" | "resolve" | "notes";

export function runIncidentAction<T = unknown>(
  token: string,
  incidentId: string,
  action: IncidentAction,
  body: Record<string, unknown>,
): Promise<T> {
  return request<T>(`/incidents/${incidentId}/${action}`, token, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/* ── AD-01-15 · GET /tables — nightly table census ──────────────────────── */

export interface TableCensusRow {
  name: string;
  rows: number | null;
  bytes: number | null;
  empty: boolean;
  computed_at: string | null;
}

export interface TableCensusResponse {
  count: number;
  tables: TableCensusRow[];
}

export function fetchDataHealthTables(
  token: string,
): Promise<TableCensusResponse> {
  return request<TableCensusResponse>("/tables", token);
}

/* ── AD-01-15 · GET /completeness — nightly completeness snapshot ──────── */

export interface CompletenessMetric {
  metric: string;
  value: number | null;
  computed_at: string | null;
}

export interface CompletenessResponse {
  count: number;
  metrics: CompletenessMetric[];
}

export function fetchDataHealthCompleteness(
  token: string,
): Promise<CompletenessResponse> {
  return request<CompletenessResponse>("/completeness", token);
}

/** The 10 boats identity columns the nightly job computes completeness
 * for (SPEC-22 §3.4) — in display order. */
export const BOATS_COMPLETENESS_COLUMNS = [
  "design",
  "design_canonical",
  "country",
  "year_built",
  "builder",
  "designer",
  "loa",
  "lwl",
  "beam_max",
  "displacement_kg",
] as const;

export const EVENTS_VENUE_NULL_METRIC = "completeness.events.venue_null_pct";

/** A completeness meter is flagged (SailRatings "Buoy" warning colour)
 * below this threshold. */
export const COMPLETENESS_WARNING_THRESHOLD = 40;
