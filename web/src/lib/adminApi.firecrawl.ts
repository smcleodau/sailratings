/**
 * Firecrawl admin client (AD-01-22).
 *
 * Delta on AD-01-08: the /admin/firecrawl page used three raw fetch calls
 * (summary / recent / by-domain) and never consumed the OPS-01-05 budget
 * endpoint or the parallel-run diffs endpoint. All five now live here, on the
 * shared `adminFetch` helper (bearer token, base URL, AdminApiError and the
 * stale-token clearing on 401/403), following the adminApi.<feature>.ts
 * pattern set by adminApi.scrapers.ts (AD-01-20). Kept out of adminApi.ts
 * (AD-01-24 owns that file).
 *
 *   GET /v1/admin/firecrawl/summary    — today / 7d / 30d windows + daily cap
 *   GET /v1/admin/firecrawl/recent     — last N calls (status / mode filters)
 *   GET /v1/admin/firecrawl/by-domain  — per-domain rollup
 *   GET /v1/admin/firecrawl/budget     — balance, burn, headroom, caps
 *   GET /v1/admin/firecrawl/diffs      — Firecrawl-vs-legacy comparisons
 */

import { adminFetch } from "./adminApi";

/* ── Types ─────────────────────────────────────────────────────────── */

export type CallMode = "scrape" | "map";
export type CallStatus = "ok" | "empty" | "error";

export interface WindowStats {
  window: string;
  calls: number;
  credits: number;
  ok: number;
  empty: number;
  errored: number;
  scrapes: number;
  maps: number;
  avg_ms: number;
  domains: number;
}

export interface DailyCapState {
  daily_credit_cap: number | null;
  used_today: number | null;
  daily_capped: boolean;
}

export interface SummaryPayload {
  as_of: string;
  remaining: { remaining_credits: number; plan_credits: number | null } | null;
  windows: Record<string, WindowStats>;
  daily?: DailyCapState;
}

export interface RecentCall {
  id: number;
  called_at: string;
  mode: CallMode;
  url: string;
  domain: string;
  status: CallStatus;
  credits: number | null;
  duration_ms: number | null;
  response_chars: number | null;
  links_found: number | null;
  error_message: string | null;
  caller: string | null;
}

export interface DomainStat {
  domain: string;
  calls: number;
  credits: number;
  ok: number;
  empty: number;
  errored: number;
  success_rate: number;
  avg_ms: number;
  last_called: string | null;
}

export interface ThrottleEvent {
  id: number;
  created_at: string | null;
  provider: string;
  action: string;
  caller: string | null;
  mode: string | null;
  url: string | null;
  used_credits: number | null;
  soft_cap: number | null;
  hard_cap: number | null;
  utilization: number | null;
  message: string | null;
}

/** GET /firecrawl/budget — crawl_telemetry.credit_balance() + throttle log. */
export interface BudgetPayload {
  provider: string;
  balance_credits: number;
  balance_source: "provider" | "ledger";
  period_credits: number;
  period_start: string | null;
  period_spend: number;
  ledger_balance: number;
  burn_7d: number;
  burn_30d: number;
  projected_monthly_burn: number;
  headroom_credits: number;
  soft_cap: number;
  hard_cap: number;
  daily_credit_cap: number | null;
  used_today: number;
  daily_capped: boolean;
  as_of: string;
  throttle_events?: ThrottleEvent[];
}

export interface FirecrawlDiff {
  id: number;
  ran_at: string | null;
  source: string;
  source_url: string | null;
  event_name: string | null;
  event_date: string | null;
  legacy_rows: number | null;
  firecrawl_rows: number | null;
  matched: number | null;
  match_rate: number;
  confidence: number | null;
  missing_names: string[];
  extra_names: string[];
  notes: string | null;
}

export interface DiffRollup {
  source: string;
  runs: number;
  avg_rate: number;
  min_rate: number;
  green: number;
  amber: number;
  red: number;
  last_run: string | null;
}

export interface DiffsPayload {
  diffs: FirecrawlDiff[];
  rollup: DiffRollup[];
}

export interface RecentCallsFilter {
  limit?: number;
  status?: CallStatus | "all";
  mode?: CallMode | "all";
}

/* ── Calls ─────────────────────────────────────────────────────────── */

export function getFirecrawlSummary(signal?: AbortSignal) {
  return adminFetch<SummaryPayload>("/admin/firecrawl/summary", { signal });
}

export async function getFirecrawlRecent(
  filter: RecentCallsFilter = {},
  signal?: AbortSignal,
): Promise<{ calls: RecentCall[] }> {
  const params = new URLSearchParams({ limit: String(filter.limit ?? 150) });
  if (filter.status && filter.status !== "all") params.set("status", filter.status);
  if (filter.mode && filter.mode !== "all") params.set("mode", filter.mode);
  return adminFetch<{ calls: RecentCall[] }>(
    `/admin/firecrawl/recent?${params.toString()}`,
    { signal },
  );
}

export function getFirecrawlByDomain(days = 7, signal?: AbortSignal) {
  return adminFetch<{ days: number; domains: DomainStat[] }>(
    `/admin/firecrawl/by-domain?days=${days}`,
    { signal },
  );
}

export function getFirecrawlBudget(signal?: AbortSignal) {
  return adminFetch<BudgetPayload>("/admin/firecrawl/budget", { signal });
}

export function getFirecrawlDiffs(
  opts: { source?: string; limit?: number } = {},
  signal?: AbortSignal,
) {
  const params = new URLSearchParams({ limit: String(opts.limit ?? 50) });
  if (opts.source) params.set("source", opts.source);
  return adminFetch<DiffsPayload>(`/admin/firecrawl/diffs?${params.toString()}`, {
    signal,
  });
}

/* ── Derived budget numbers ────────────────────────────────────────── */

/** Headroom below this many days flips the Credit panel to Buoy. */
export const HEADROOM_WARN_DAYS = 7;

/**
 * Days the current balance lasts at the trailing 7-day burn rate.
 * Returns null when there is no burn to extrapolate from (infinite runway).
 */
export function headroomDays(b: Pick<BudgetPayload, "balance_credits" | "burn_7d">): number | null {
  const perDay = b.burn_7d / 7;
  if (!(perDay > 0)) return null;
  return Math.max(0, b.balance_credits) / perDay;
}
