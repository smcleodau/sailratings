/**
 * Scrapers control-plane client (AD-01-20).
 *
 * Delta on AD-01-06: the /admin/scrapers page (AD-01-06) only read the
 * run/freshness summary. The control plane — pause / resume / run-now and
 * the Temporal schedule state behind it — lives on a *separate* router
 * (api/src/irc_data/api/routers/scrapers.py, OPS-02-04) that this module
 * wraps:
 *
 *   GET  /v1/admin/scrapers/schedule-state   — every source's schedule_id,
 *                                              cadence and paused flag
 *   POST /v1/admin/scrapers/{slug}/pause     — pause the Temporal schedule
 *   POST /v1/admin/scrapers/{slug}/resume    — resume it
 *   POST /v1/admin/scrapers/{slug}/run       — fire it once, right now
 *
 * Kept out of adminApi.ts (AD-01-24 owns that file) — everything here rides
 * on the same `adminFetch` helper (shared bearer token, base URL, and
 * AdminApiError semantics) so callers get identical auth/error handling.
 */

import { adminFetch, type AdminRequestOptions } from "./adminApi";

/** One row of GET /v1/admin/scrapers/schedule-state. */
export interface ScheduleStateRow {
  slug: string;
  display_name?: string | null;
  base_url?: string | null;
  category?: string | null;
  legal_status?: string | null;
  enabled?: boolean | null;
  cadence: string | null;
  adapter_status?: string | null;
  adapter_class?: string | null;
  schedule_id: string | null;
  schedule_paused: boolean | null;
  schedule_synced_at?: string | null;
  last_run_status?: string | null;
  last_run_at?: string | null;
}

export interface ScheduleStateResponse {
  count: number;
  scrapers: ScheduleStateRow[];
}

/** GET /v1/admin/scrapers/schedule-state — every source's schedule state. */
export async function fetchScheduleState(
  signal?: AbortSignal,
): Promise<ScheduleStateResponse> {
  return adminFetch<ScheduleStateResponse>("/admin/scrapers/schedule-state", {
    signal,
  });
}

/** Result shape shared by pause/resume/run — tolerant of whatever the
 *  underlying Temporal activity returns (set_schedule_paused /
 *  trigger_source_run); callers only rely on the fields they need. */
export interface ScraperControlResult {
  paused?: boolean;
  status?: string;
  run_id?: string | number;
  schedule_id?: string | null;
  detail?: string;
  [key: string]: unknown;
}

function controlRequest(
  slug: string,
  action: "pause" | "resume" | "run",
  options: AdminRequestOptions = {},
): Promise<ScraperControlResult> {
  return adminFetch<ScraperControlResult>(
    `/admin/scrapers/${encodeURIComponent(slug)}/${action}`,
    { method: "POST", ...options },
  );
}

/** POST /v1/admin/scrapers/{slug}/pause */
export function pauseScraperSchedule(
  slug: string,
): Promise<ScraperControlResult> {
  return controlRequest(slug, "pause");
}

/** POST /v1/admin/scrapers/{slug}/resume */
export function resumeScraperSchedule(
  slug: string,
): Promise<ScraperControlResult> {
  return controlRequest(slug, "resume");
}

/** POST /v1/admin/scrapers/{slug}/run — manual "run now" trigger. */
export function runScraperNow(slug: string): Promise<ScraperControlResult> {
  return controlRequest(slug, "run");
}

export const adminScrapersApi = {
  fetchScheduleState,
  pauseScraperSchedule,
  resumeScraperSchedule,
  runScraperNow,
};
