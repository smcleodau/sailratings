/**
 * AD-01-22 — fixtures for the /admin/firecrawl e2e spec.
 *
 * Shapes mirror the real GET /v1/admin/firecrawl/{summary,recent,by-domain,
 * budget,diffs} responses (routers/admin.py, crawl_telemetry.credit_balance).
 */

export const SUMMARY_FIXTURE = {
  as_of: '2026-09-08T09:00:00Z',
  remaining: { remaining_credits: 2400, plan_credits: 3000 },
  windows: {
    today: { window: 'today', calls: 12, credits: 12, ok: 10, empty: 1, errored: 1, scrapes: 9, maps: 3, avg_ms: 1800, domains: 3 },
    '7d': { window: '7d', calls: 84, credits: 84, ok: 80, empty: 2, errored: 2, scrapes: 60, maps: 24, avg_ms: 1700, domains: 4 },
    '30d': { window: '30d', calls: 300, credits: 300, ok: 290, empty: 5, errored: 5, scrapes: 220, maps: 80, avg_ms: 1650, domains: 5 },
  },
  daily: { daily_credit_cap: 200, used_today: 12, daily_capped: false },
};

const BASE_BUDGET = {
  provider: 'firecrawl',
  balance_credits: 2400,
  balance_source: 'provider',
  provider_reported: { remaining_credits: 2400, plan_credits: 3000 },
  period_credits: 3000,
  period_start: '2026-09-01T00:00:00+00:00',
  period_spend: 600,
  ledger_balance: 2400,
  burn_7d: 70, // 10 cr/day → 240 days of runway
  burn_30d: 300,
  projected_monthly_burn: 300,
  headroom_credits: 2100,
  soft_cap: 2400,
  hard_cap: 2850,
  daily_credit_cap: 200,
  used_today: 12,
  daily_capped: false,
  as_of: '2026-09-08T09:00:00Z',
  throttle_events: [],
};

/** Healthy: 240 days of headroom, cap 200, 12 spent today. */
export const BUDGET_OK_FIXTURE = { ...BASE_BUDGET };

/** Low runway: balance 140 at 20 cr/day → 7.0 d … use 100 → 5.0 days (<7). */
export const BUDGET_LOW_FIXTURE = {
  ...BASE_BUDGET,
  balance_credits: 100,
  ledger_balance: 100,
  burn_7d: 140,
  projected_monthly_burn: 600,
  headroom_credits: -500,
};

/** Daily cap reached. */
export const BUDGET_CAPPED_FIXTURE = {
  ...BASE_BUDGET,
  used_today: 200,
  daily_capped: true,
};

const CALL = {
  duration_ms: 1500,
  response_chars: 12000,
  links_found: null,
  error_message: null,
  caller: 'scraper',
  mode: 'scrape',
  status: 'ok',
  credits: 1,
};

export const RECENT_FIXTURE = {
  calls: [
    { ...CALL, id: 1, called_at: '2026-09-08T08:55:00Z', url: 'https://www.sailsys.com.au/results/1', domain: 'sailsys.com.au' },
    { ...CALL, id: 2, called_at: '2026-09-08T08:50:00Z', url: 'https://www.sailsys.com.au/results/2', domain: 'sailsys.com.au' },
    { ...CALL, id: 3, called_at: '2026-09-08T08:45:00Z', url: 'https://www.topyacht.com.au/events', domain: 'topyacht.com.au' },
    { ...CALL, id: 4, called_at: '2026-09-08T08:40:00Z', url: 'https://www.rhkyc.org.hk/races', domain: 'rhkyc.org.hk', mode: 'map', links_found: 41, response_chars: null },
  ],
};

export const BY_DOMAIN_FIXTURE = {
  days: 7,
  domains: [
    { domain: 'sailsys.com.au', calls: 40, credits: 40, ok: 40, empty: 0, errored: 0, success_rate: 1, avg_ms: 1500, last_called: '2026-09-08T08:55:00Z' },
    { domain: 'topyacht.com.au', calls: 30, credits: 30, ok: 28, empty: 1, errored: 1, success_rate: 0.93, avg_ms: 1900, last_called: '2026-09-08T08:45:00Z' },
    { domain: 'rhkyc.org.hk', calls: 14, credits: 14, ok: 12, empty: 0, errored: 2, success_rate: 0.86, avg_ms: 2100, last_called: '2026-09-08T08:40:00Z' },
  ],
};

export const DIFFS_FIXTURE = {
  diffs: [
    {
      id: 101, ran_at: '2026-09-08T06:00:00Z', source: 'cowesweek',
      source_url: 'https://www.cowesweek.co.uk/results', event_name: 'Cowes Week 2026 — IRC 1',
      event_date: '2026-08-01', legacy_rows: 40, firecrawl_rows: 39, matched: 39, match_rate: 0.975,
      confidence: 0.9, missing_names: ['Blue Moon'], extra_names: [], notes: null,
    },
    {
      id: 102, ran_at: '2026-09-07T06:00:00Z', source: 'isora',
      source_url: 'https://www.isora.org.uk/results', event_name: 'ISORA Race 5',
      event_date: '2026-07-12', legacy_rows: 20, firecrawl_rows: 15, matched: 15, match_rate: 0.75,
      confidence: 0.8, missing_names: ['A', 'B', 'C', 'D', 'E'], extra_names: [], notes: null,
    },
    {
      id: 103, ran_at: '2026-09-06T06:00:00Z', source: 'rhkyc',
      source_url: 'https://www.rhkyc.org.hk/results', event_name: 'RHKYC Autumn Series',
      event_date: '2026-06-02', legacy_rows: 30, firecrawl_rows: 27, matched: 27, match_rate: 0.9,
      confidence: 0.85, missing_names: ['X', 'Y', 'Z'], extra_names: [], notes: null,
    },
  ],
  rollup: [
    { source: 'cowesweek', runs: 4, avg_rate: 0.97, min_rate: 0.95, green: 4, amber: 0, red: 0, last_run: '2026-09-08T06:00:00Z' },
    { source: 'isora', runs: 3, avg_rate: 0.8, min_rate: 0.75, green: 0, amber: 1, red: 2, last_run: '2026-09-07T06:00:00Z' },
  ],
};
