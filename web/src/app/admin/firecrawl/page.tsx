"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangleIcon,
  CheckCircleIcon,
  ClockIcon,
  ExternalLinkIcon,
  SpinnerIcon,
  RefreshIcon,
} from "@/components/admin/AdminIcons";

import {
  AdminApiError,
} from "@/lib/adminApi";
import {
  HEADROOM_WARN_DAYS,
  getFirecrawlBudget,
  getFirecrawlByDomain,
  getFirecrawlDiffs,
  getFirecrawlRecent,
  getFirecrawlSummary,
  headroomDays,
  type BudgetPayload,
  type CallMode,
  type CallStatus,
  type DiffsPayload,
  type DomainStat,
  type RecentCall,
  type SummaryPayload,
} from "@/lib/adminApi.firecrawl";

/** Cap-reached colour (AD-01-22). Buoy (--sr-buoy) is used for low headroom. */
const CAP_REACHED_COLOUR = "#F0806A";

/* ────────────────────────────────────────────────────────────────── */
/* Helpers                                                            */
/* ────────────────────────────────────────────────────────────────── */

function fmtAge(iso: string | null): string {
  if (!iso) return "—";
  const diffSec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${(diffSec / 3600).toFixed(1)}h ago`;
  return `${(diffSec / 86400).toFixed(1)}d ago`;
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function fmtMs(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

function hostOf(url: string | null): string | null {
  if (!url) return null;
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch {
    return null;
  }
}

function pct(n: number): string {
  return `${(n * 100).toFixed(0)}%`;
}

/* ────────────────────────────────────────────────────────────────── */
/* Sub-components                                                     */
/* ────────────────────────────────────────────────────────────────── */

function StatCard({
  label,
  value,
  sub,
  tone = "neutral",
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: "neutral" | "warn" | "good";
}) {
  const valueClass =
    tone === "warn"
      ? "text-[var(--sr-action-pressed)]"
      : tone === "good"
      ? "text-[var(--sr-status-success)]"
      : "text-[var(--sr-text-primary)]";
  return (
    <div className="border border-[var(--sr-link)]/12 bg-[var(--sr-surface-card)] rounded-[4px] px-4 py-3 shadow-sm">
      <p className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
        {label}
      </p>
      <p className={`heading-display text-2xl mt-1 tabular-nums ${valueClass}`}>
        {value}
      </p>
      {sub && (
        <p className="admin-mono-font text-[9px] text-[var(--sr-text-tertiary)] mt-1">{sub}</p>
      )}
    </div>
  );
}

function ModePill({ mode }: { mode: CallMode }) {
  const cls =
    mode === "scrape"
      ? "bg-[var(--sr-status-warning)]/15 text-[var(--sr-status-warning)] border-[var(--sr-status-warning)]/30"
      : "bg-[var(--sr-link)]/10 text-[var(--sr-link)] border-[var(--sr-link)]/20";
  return (
    <span
      className={`admin-mono-font text-[9px] uppercase tracking-[0.14em] px-2 py-0.5 border rounded-[2px] ${cls}`}
    >
      {mode}
    </span>
  );
}

function StatusPill({ status }: { status: CallStatus }) {
  if (status === "ok") {
    return (
      <span className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-status-success)]">
        <CheckCircleIcon size={11} strokeWidth={2} /> ok
      </span>
    );
  }
  if (status === "empty") {
    return (
      <span className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-label)]">
        empty
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-action-pressed)]">
      <AlertTriangleIcon size={11} strokeWidth={2} /> error
    </span>
  );
}

/* ────────────────────────────────────────────────────────────────── */
/* Main page                                                          */
/* ────────────────────────────────────────────────────────────────── */

export default function FirecrawlPage() {
  const [token, setToken] = useState<string | null>(null);
  const [pwInput, setPwInput] = useState("");

  const [summary, setSummary] = useState<SummaryPayload | null>(null);
  const [recent, setRecent] = useState<RecentCall[]>([]);
  const [domains, setDomains] = useState<DomainStat[]>([]);
  const [budget, setBudget] = useState<BudgetPayload | null>(null);
  const [budgetError, setBudgetError] = useState<string | null>(null);
  const [diffs, setDiffs] = useState<DiffsPayload | null>(null);
  const [diffsError, setDiffsError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<CallStatus | "all">("all");
  const [modeFilter, setModeFilter] = useState<CallMode | "all">("all");
  const [domainFilter, setDomainFilter] = useState<string>("all");

  useEffect(() => {
    const t = localStorage.getItem("admin_token") || process.env.NEXT_PUBLIC_ADMIN_PASSWORD || "sailfast2026";
    if (t) {
      localStorage.setItem("admin_token", t);
      setToken(t);
    }
  }, []);

  const fetchAll = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      // summary / recent / by-domain are the page's backbone: any failure
      // fails the load. budget and diffs are independent panels — a failure
      // there degrades just that panel (reported inline) and never blanks
      // the rest of the page.
      const [sumJson, recJson, domJson, budgetRes, diffsRes] = await Promise.all([
        getFirecrawlSummary(),
        getFirecrawlRecent({ limit: 150, status: statusFilter, mode: modeFilter }),
        getFirecrawlByDomain(7),
        getFirecrawlBudget().then(
          (v) => ({ ok: true as const, v }),
          (e: unknown) => ({ ok: false as const, e }),
        ),
        getFirecrawlDiffs({ limit: 25 }).then(
          (v) => ({ ok: true as const, v }),
          (e: unknown) => ({ ok: false as const, e }),
        ),
      ]);
      setSummary(sumJson);
      setRecent(recJson.calls);
      setDomains(domJson.domains);
      if (budgetRes.ok) {
        setBudget(budgetRes.v);
        setBudgetError(null);
      } else {
        setBudgetError(budgetRes.e instanceof Error ? budgetRes.e.message : "Failed to load budget");
      }
      if (diffsRes.ok) {
        setDiffs(diffsRes.v);
        setDiffsError(null);
      } else {
        setDiffsError(diffsRes.e instanceof Error ? diffsRes.e.message : "Failed to load diffs");
      }
    } catch (e) {
      if (e instanceof AdminApiError && (e.status === 401 || e.status === 403)) {
        // adminFetch already cleared the stale token — fall back to the gate.
        setToken(null);
        setError("Session expired. Sign in again.");
      } else {
        setError(e instanceof Error ? e.message : "Failed to load");
      }
    } finally {
      setLoading(false);
    }
  }, [token, statusFilter, modeFilter]);

  useEffect(() => {
    fetchAll();
    if (!token) return;
    const id = setInterval(fetchAll, 60_000); // refresh every minute
    return () => clearInterval(id);
  }, [fetchAll, token]);

  /* ── Login gate ─────────────────────────────────────────────────── */

  if (!token) {
    return (
      <div className="flex-1 flex items-center justify-center px-6 bg-[var(--sr-surface-page)]">
        <form
          className="w-full max-w-sm space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (pwInput.trim()) {
              localStorage.setItem("admin_token", pwInput.trim());
              setToken(pwInput.trim());
            }
          }}
        >
          <h1 className="heading-display text-2xl text-[var(--sr-text-primary)] text-center mb-6">
            Firecrawl
          </h1>
          <input
            type="password"
            value={pwInput}
            onChange={(e) => setPwInput(e.target.value)}
            placeholder="Admin password"
            className="w-full h-12 px-4 bg-[var(--sr-surface-card)] border border-[var(--sr-link)]/25 text-[var(--sr-text-primary)] text-[13px] placeholder:text-[var(--sr-text-label)] focus:border-[var(--sr-link)] focus:ring-1 focus:ring-[var(--sr-link)]/20 outline-none transition-all rounded-[4px] shadow-sm"
          />
          <button
            type="submit"
            className="w-full h-12 bg-[var(--sr-link)] text-[var(--sr-text-primary)] text-[13px] font-medium hover:bg-[var(--sr-focus)] transition-colors rounded-[4px] shadow-sm"
          >
            Sign in
          </button>
        </form>
      </div>
    );
  }

  /* ── Computed displays ──────────────────────────────────────────── */

  const today = summary?.windows.today;
  const w7 = summary?.windows["7d"];
  const w30 = summary?.windows["30d"];

  const daysLeft = budget ? headroomDays(budget) : null;
  const headroomLow = daysLeft != null && daysLeft < HEADROOM_WARN_DAYS;

  // Domain filter for the recent-calls log. /recent has no domain param
  // (admin.py is not ours), so it narrows the loaded page client-side.
  const recentDomains = Array.from(new Set(recent.map((c) => c.domain))).sort();
  const visibleRecent =
    domainFilter === "all" ? recent : recent.filter((c) => c.domain === domainFilter);

  /* ── Render ─────────────────────────────────────────────────────── */

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--sr-surface-page)]">
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-end justify-between mb-8 gap-6 flex-wrap">
          <div>
            <h1 className="heading-display text-2xl text-[var(--sr-text-primary)]">Firecrawl</h1>
            <p className="text-[13px] text-[var(--sr-text-tertiary)] mt-1">
              Per-call telemetry for every scrape + map request. Auto-refreshes
              every minute.
            </p>
          </div>
          <div className="flex items-center gap-4 text-[var(--sr-text-label)]">
            {summary?.as_of && (
              <span className="admin-mono-font text-[10px] uppercase tracking-[0.16em]">
                As of {fmtDateTime(summary.as_of)}
              </span>
            )}
            <button
              onClick={fetchAll}
              disabled={loading}
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)] transition-colors disabled:opacity-40"
            >
              <RefreshIcon
                size={12}
                strokeWidth={2}
                className={loading ? "animate-spin" : ""}
              />
              Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="border border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/5 px-4 py-3 mb-6 text-[13px] text-[var(--sr-action-pressed)] rounded-[4px]">
            {error}
          </div>
        )}

        {/* Credit panel — balance, daily cap, spend and runway (AD-01-22).
            From /firecrawl/budget; supersedes the AD-01-08 balance + daily
            banners, which this panel subsumes. */}
        {budget ? (
          <div
            data-testid="credit-panel"
            data-headroom-tone={headroomLow ? "low" : "ok"}
            data-capped={budget.daily_capped ? "true" : "false"}
            className={`border bg-[var(--sr-surface-card)] rounded-[4px] shadow-sm p-4 mb-6 ${
              budget.daily_capped
                ? "border-[#F0806A]/50"
                : "border-[var(--sr-link)]/12"
            }`}
          >
            <div className="flex items-baseline justify-between gap-4 flex-wrap mb-3">
              <span className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
                Credit budget · {budget.provider}
              </span>
              <span
                data-testid="credit-balance-source"
                className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-tertiary)]"
              >
                {budget.balance_source === "provider"
                  ? "balance reported by Firecrawl"
                  : "balance derived from ledger"}
              </span>
            </div>

            <div className="grid grid-cols-2 lg:grid-cols-5 gap-4">
              <div>
                <p className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
                  Balance
                </p>
                <p
                  data-testid="credit-balance"
                  className="heading-display text-2xl mt-1 tabular-nums text-[var(--sr-text-primary)]"
                >
                  {budget.balance_credits.toLocaleString()}
                </p>
                <p className="admin-mono-font text-[9px] text-[var(--sr-text-tertiary)] mt-1">
                  of {budget.period_credits.toLocaleString()} cr plan
                </p>
              </div>

              <div>
                <p className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
                  Daily cap
                </p>
                <p
                  data-testid="credit-daily-cap"
                  className="heading-display text-2xl mt-1 tabular-nums text-[var(--sr-text-primary)]"
                >
                  {budget.daily_credit_cap != null && budget.daily_credit_cap > 0
                    ? budget.daily_credit_cap.toLocaleString()
                    : "none"}
                </p>
                <p className="admin-mono-font text-[9px] text-[var(--sr-text-tertiary)] mt-1">
                  hard stop · UTC day
                </p>
              </div>

              <div>
                <p className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
                  Spent today
                </p>
                <p
                  data-testid="credit-spent-today"
                  className="heading-display text-2xl mt-1 tabular-nums"
                  style={budget.daily_capped ? { color: CAP_REACHED_COLOUR } : undefined}
                >
                  {budget.used_today.toLocaleString()}
                </p>
                <p
                  className="admin-mono-font text-[9px] mt-1"
                  style={budget.daily_capped ? { color: CAP_REACHED_COLOUR } : undefined}
                >
                  {budget.daily_capped ? "cap reached" : "since 00:00 UTC"}
                </p>
              </div>

              <div>
                <p className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
                  Projected burn
                </p>
                <p
                  data-testid="credit-projected-burn"
                  className="heading-display text-2xl mt-1 tabular-nums text-[var(--sr-text-primary)]"
                >
                  {budget.projected_monthly_burn.toLocaleString()}
                </p>
                <p className="admin-mono-font text-[9px] text-[var(--sr-text-tertiary)] mt-1">
                  cr / 30d · from last 7d ({budget.burn_7d.toLocaleString()})
                </p>
              </div>

              <div>
                <p className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
                  Headroom
                </p>
                <p
                  data-testid="credit-headroom"
                  data-headroom-days={daysLeft == null ? "" : daysLeft.toFixed(1)}
                  className={`heading-display text-2xl mt-1 tabular-nums ${
                    headroomLow
                      ? "headroom-low text-[var(--sr-buoy)]"
                      : "headroom-ok text-[var(--sr-status-success)]"
                  }`}
                >
                  {daysLeft == null ? "∞" : `${daysLeft.toFixed(1)} d`}
                </p>
                <p className="admin-mono-font text-[9px] text-[var(--sr-text-tertiary)] mt-1">
                  {headroomLow
                    ? `under ${HEADROOM_WARN_DAYS} days at current burn`
                    : "at current burn"}
                  {" · "}
                  {budget.headroom_credits >= 0 ? "+" : "−"}
                  {Math.abs(budget.headroom_credits).toLocaleString()} cr vs 30d
                </p>
              </div>
            </div>

            {budget.daily_credit_cap != null && budget.daily_credit_cap > 0 && (
              <div className="mt-4">
                <div className="h-1.5 bg-[var(--sr-surface-interactive)] rounded-[2px] overflow-hidden max-w-md">
                  <div
                    data-testid="credit-cap-bar"
                    className={budget.daily_capped ? "h-full" : "h-full bg-[var(--sr-link)]/60"}
                    style={{
                      width: `${Math.min(
                        100,
                        (budget.used_today / budget.daily_credit_cap) * 100,
                      )}%`,
                      ...(budget.daily_capped ? { background: CAP_REACHED_COLOUR } : {}),
                    }}
                  />
                </div>
                {budget.daily_capped && (
                  <p
                    data-testid="credit-cap-reached"
                    className="admin-mono-font text-[10px] mt-2 flex items-center gap-1.5"
                    style={{ color: CAP_REACHED_COLOUR }}
                  >
                    <AlertTriangleIcon size={12} />
                    Daily credit cap reached — Firecrawl calls are stopped
                    (hard stop) until the UTC day rolls.
                  </p>
                )}
              </div>
            )}
          </div>
        ) : (
          <div
            data-testid="credit-panel-unavailable"
            className="border border-[var(--sr-status-warning)]/30 bg-[var(--sr-status-warning)]/10 px-4 py-3 mb-6 flex items-start gap-3 rounded-[4px]"
          >
            <AlertTriangleIcon size={16} className="text-[var(--sr-status-warning)] flex-shrink-0 mt-0.5" />
            <p className="text-[13px] text-[var(--sr-status-warning)]">
              {budgetError
                ? `Credit budget unavailable: ${budgetError}`
                : loading
                ? "Loading credit budget…"
                : "Credit budget unavailable."}
            </p>
          </div>
        )}

        {/* Window stat cards */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3 mb-8">
          <StatCard
            label="Last 24h"
            value={(today?.calls ?? 0).toLocaleString()}
            sub={
              today
                ? `${today.credits} cr · ${today.ok} ok · ${today.errored} err · ${fmtMs(today.avg_ms)} avg`
                : "—"
            }
            tone={today && today.errored > 0 ? "warn" : "neutral"}
          />
          <StatCard
            label="Last 7d"
            value={(w7?.credits ?? 0).toLocaleString()}
            sub={
              w7
                ? `${w7.calls} calls · ${w7.scrapes} scrape · ${w7.maps} map · ${w7.domains} domains`
                : "—"
            }
          />
          <StatCard
            label="Last 30d"
            value={(w30?.credits ?? 0).toLocaleString()}
            sub={
              w30
                ? `${w30.calls} calls · ${w30.ok} ok · ${w30.errored} err`
                : "—"
            }
          />
        </div>

        {/* Per-domain table */}
        <div className="mb-8">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="heading-display text-base text-[var(--sr-text-primary)]">
              Per-domain · last 7 days
            </h2>
            <span className="admin-mono-font text-[9px] text-[var(--sr-text-label)]">
              {domains.length} domain{domains.length === 1 ? "" : "s"}
            </span>
          </div>
          <div className="border border-[var(--sr-link)]/12 bg-[var(--sr-surface-card)] rounded-[4px] shadow-sm overflow-hidden">
            <div className="grid grid-cols-[1.6fr_70px_70px_90px_90px_120px] gap-4 px-4 py-3 bg-[var(--sr-surface-interactive)] border-b border-[var(--sr-link)]/12 admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] font-medium">
              <span>Domain</span>
              <span className="text-right">Calls</span>
              <span className="text-right">Cr</span>
              <span className="text-right">Success</span>
              <span className="text-right">Avg latency</span>
              <span className="text-right">Last call</span>
            </div>
            {domains.length === 0 && !loading && (
              <div className="px-4 py-6 text-[13px] text-[var(--sr-text-label)] italic text-center">
                No Firecrawl calls in the last 7 days.
              </div>
            )}
            {domains.map((d) => {
              const successCls =
                d.success_rate >= 0.95
                  ? "text-[var(--sr-status-success)]"
                  : d.success_rate >= 0.7
                  ? "text-[var(--sr-text-tertiary)]"
                  : "text-[var(--sr-action-pressed)]";
              return (
                <div
                  key={d.domain}
                  className="grid grid-cols-[1.6fr_70px_70px_90px_90px_120px] gap-4 px-4 py-2.5 items-center border-b border-[var(--sr-link)]/5 last:border-b-0 hover:bg-[var(--sr-surface-interactive)] transition-colors"
                >
                  <p className="text-[13px] text-[var(--sr-text-primary)] truncate">
                    {d.domain}
                  </p>
                  <p className="admin-mono-font text-[10px] text-[var(--sr-text-tertiary)] tabular-nums text-right">
                    {d.calls}
                  </p>
                  <p className="admin-mono-font text-[10px] text-[var(--sr-text-tertiary)] tabular-nums text-right">
                    {d.credits}
                  </p>
                  <p
                    className={`admin-mono-font text-[10px] tabular-nums text-right ${successCls}`}
                  >
                    {pct(d.success_rate)}
                    {d.errored > 0 && (
                      <span className="text-[var(--sr-text-label)]"> · {d.errored} err</span>
                    )}
                  </p>
                  <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] tabular-nums text-right">
                    {fmtMs(d.avg_ms)}
                  </p>
                  <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] tabular-nums text-right">
                    {fmtAge(d.last_called)}
                  </p>
                </div>
              );
            })}
          </div>
        </div>

        {/* Diffs panel — Firecrawl-vs-legacy comparisons (AD-01-22) */}
        <div className="mb-8" data-testid="diffs-panel">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="heading-display text-base text-[var(--sr-text-primary)]">
              Content diffs · Firecrawl vs legacy
            </h2>
            <span className="admin-mono-font text-[9px] text-[var(--sr-text-label)]">
              {diffs ? `${diffs.diffs.length} recent` : ""}
            </span>
          </div>

          {diffs && diffs.rollup.length > 0 && (
            <div className="flex flex-wrap gap-2 mb-3" data-testid="diffs-rollup">
              {diffs.rollup.map((r) => (
                <span
                  key={r.source}
                  data-testid={`diffs-rollup-${r.source}`}
                  className="admin-mono-font text-[9px] uppercase tracking-[0.12em] px-2.5 py-1 border border-[var(--sr-link)]/12 rounded-[4px] bg-[var(--sr-surface-card)] text-[var(--sr-text-tertiary)]"
                >
                  {r.source} · {pct(r.avg_rate)} avg ·{" "}
                  <span className="text-[var(--sr-status-success)]">{r.green}</span>/
                  <span className="text-[var(--sr-buoy)]">{r.amber}</span>/
                  <span className="text-[var(--sr-action-pressed)]">{r.red}</span>
                </span>
              ))}
            </div>
          )}

          <div className="border border-[var(--sr-link)]/12 bg-[var(--sr-surface-card)] rounded-[4px] shadow-sm overflow-hidden">
            <div className="grid grid-cols-[110px_1.2fr_1.6fr_90px_90px_80px] gap-3 px-4 py-3 bg-[var(--sr-surface-interactive)] border-b border-[var(--sr-link)]/12 admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] font-medium">
              <span>Ran</span>
              <span>Domain</span>
              <span>Event</span>
              <span className="text-right">Legacy / FC</span>
              <span className="text-right">Match</span>
              <span className="text-right">Missing</span>
            </div>
            {diffsError && (
              <div className="px-4 py-4 text-[13px] text-[var(--sr-action-pressed)]">
                Diffs unavailable: {diffsError}
              </div>
            )}
            {!diffsError && diffs && diffs.diffs.length === 0 && (
              <div
                data-testid="diffs-empty"
                className="px-4 py-6 text-[13px] text-[var(--sr-text-label)] italic text-center"
              >
                No content diffs recorded yet.
              </div>
            )}
            {diffs?.diffs.map((d) => {
              const rateCls =
                d.match_rate >= 0.95
                  ? "text-[var(--sr-status-success)]"
                  : d.match_rate >= 0.85
                  ? "text-[var(--sr-buoy)]"
                  : "text-[var(--sr-action-pressed)]";
              return (
                <div
                  key={d.id}
                  data-testid={`diff-row-${d.id}`}
                  className="grid grid-cols-[110px_1.2fr_1.6fr_90px_90px_80px] gap-3 px-4 py-2.5 items-center border-b border-[var(--sr-link)]/5 last:border-b-0 hover:bg-[var(--sr-surface-interactive)] transition-colors"
                >
                  <p
                    className="admin-mono-font text-[10px] text-[var(--sr-text-label)] tabular-nums truncate"
                    title={fmtDateTime(d.ran_at)}
                  >
                    {fmtAge(d.ran_at)}
                  </p>
                  <div className="min-w-0">
                    <p className="text-[13px] text-[var(--sr-text-primary)] truncate">
                      {hostOf(d.source_url) ?? d.source}
                    </p>
                    <p className="admin-mono-font text-[9px] text-[var(--sr-text-label)] truncate">
                      {d.source}
                    </p>
                  </div>
                  <p
                    className="text-[11px] text-[var(--sr-text-tertiary)] truncate"
                    title={d.event_name ?? undefined}
                  >
                    {d.event_name ?? "—"}
                  </p>
                  <p className="admin-mono-font text-[10px] text-[var(--sr-text-tertiary)] tabular-nums text-right">
                    {d.legacy_rows ?? "—"} / {d.firecrawl_rows ?? "—"}
                  </p>
                  <p
                    data-testid={`diff-rate-${d.id}`}
                    className={`admin-mono-font text-[10px] tabular-nums text-right ${rateCls}`}
                  >
                    {pct(d.match_rate)}
                  </p>
                  <p
                    className="admin-mono-font text-[10px] text-[var(--sr-text-label)] tabular-nums text-right"
                    title={d.missing_names.join(", ")}
                  >
                    {d.missing_names.length}
                  </p>
                </div>
              );
            })}
          </div>
        </div>

        {/* Recent calls — log */}
        <div>
          <div className="flex items-baseline justify-between mb-3 gap-3 flex-wrap">
            <h2 className="heading-display text-base text-[var(--sr-text-primary)]">
              Recent calls
            </h2>
            <div className="flex items-center gap-2 flex-wrap">
              {/* status filter */}
              {(["all", "ok", "empty", "error"] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setStatusFilter(s)}
                  className={`admin-mono-font text-[9px] uppercase tracking-[0.14em] px-2.5 py-1 border rounded-[4px] transition-colors ${
                    statusFilter === s
                      ? "border-[var(--sr-link)] text-[var(--sr-link)] bg-[var(--sr-surface-card)]"
                      : "border-[var(--sr-link)]/12 text-[var(--sr-text-label)] bg-[var(--sr-surface-card)] hover:text-[var(--sr-text-primary)]"
                  }`}
                >
                  {s}
                </button>
              ))}
              <span className="text-[var(--sr-link)]/20 mx-1">·</span>
              {(["all", "scrape", "map"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setModeFilter(m)}
                  className={`admin-mono-font text-[9px] uppercase tracking-[0.14em] px-2.5 py-1 border rounded-[4px] transition-colors ${
                    modeFilter === m
                      ? "border-[var(--sr-link)] text-[var(--sr-link)] bg-[var(--sr-surface-card)]"
                      : "border-[var(--sr-link)]/12 text-[var(--sr-text-label)] bg-[var(--sr-surface-card)] hover:text-[var(--sr-text-primary)]"
                  }`}
                >
                  {m}
                </button>
              ))}
              <span className="text-[var(--sr-link)]/20 mx-1">·</span>
              <select
                data-testid="domain-filter"
                aria-label="Filter by domain"
                value={domainFilter}
                onChange={(e) => setDomainFilter(e.target.value)}
                className="admin-mono-font text-[9px] uppercase tracking-[0.14em] px-2.5 py-1 border border-[var(--sr-link)]/12 rounded-[4px] bg-[var(--sr-surface-card)] text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)] outline-none"
              >
                <option value="all">all domains</option>
                {recentDomains.map((d) => (
                  <option key={d} value={d}>
                    {d}
                  </option>
                ))}
              </select>
            </div>
          </div>

          <div className="border border-[var(--sr-link)]/12 bg-[var(--sr-surface-card)] rounded-[4px] shadow-sm overflow-hidden">
            <div className="grid grid-cols-[110px_70px_1fr_70px_80px_70px_90px] gap-3 px-4 py-3 bg-[var(--sr-surface-interactive)] border-b border-[var(--sr-link)]/12 admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] font-medium">
              <span>When</span>
              <span>Mode</span>
              <span>URL</span>
              <span className="text-right">Status</span>
              <span className="text-right">Latency</span>
              <span className="text-right">Cr</span>
              <span className="text-right">Size / Links</span>
            </div>

            {loading && visibleRecent.length === 0 && (
              <div className="px-4 py-6 flex items-center gap-2 text-[var(--sr-text-label)] text-[13px]">
                <SpinnerIcon size={14} className="animate-spin" /> Loading…
              </div>
            )}

            {!loading && visibleRecent.length === 0 && (
              <div
                data-testid="recent-empty"
                className="px-4 py-8 text-[13px] text-[var(--sr-text-label)] italic text-center flex items-center justify-center gap-2">
                <ClockIcon size={14} /> No calls match the current filter.
              </div>
            )}

            {visibleRecent.map((c) => (
              <div
                key={c.id}
                data-testid="recent-row"
                data-domain={c.domain}
                className="grid grid-cols-[110px_70px_1fr_70px_80px_70px_90px] gap-3 px-4 py-2 items-center border-b border-[var(--sr-link)]/5 last:border-b-0 hover:bg-[var(--sr-surface-interactive)] transition-colors"
              >
                <p
                  className="admin-mono-font text-[10px] text-[var(--sr-text-label)] tabular-nums truncate"
                  title={fmtDateTime(c.called_at)}
                >
                  {fmtAge(c.called_at)}
                </p>
                <ModePill mode={c.mode} />
                <div className="min-w-0">
                  <a
                    href={c.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[11px] text-[var(--sr-text-tertiary)] hover:text-[var(--sr-link)] truncate inline-flex items-center gap-1 max-w-full transition-colors"
                    title={c.url}
                  >
                    <span className="truncate">
                      {c.url.replace(/^https?:\/\/(www\.)?/, "")}
                    </span>
                    <ExternalLinkIcon size={10} className="flex-shrink-0 text-[var(--sr-text-label)]" />
                  </a>
                  {c.error_message && (
                    <p
                      className="admin-mono-font text-[9px] text-[var(--sr-action-pressed)] truncate mt-0.5"
                      title={c.error_message}
                    >
                      {c.error_message}
                    </p>
                  )}
                  {c.caller && (
                    <p className="admin-mono-font text-[9px] text-[var(--sr-text-label)] mt-0.5">
                      caller: {c.caller}
                    </p>
                  )}
                </div>
                <div className="text-right">
                  <StatusPill status={c.status} />
                </div>
                <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] tabular-nums text-right">
                  {fmtMs(c.duration_ms)}
                </p>
                <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] tabular-nums text-right">
                  {c.credits ?? "—"}
                </p>
                <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] tabular-nums text-right">
                  {c.mode === "map"
                    ? `${c.links_found ?? 0} links`
                    : c.response_chars != null
                    ? `${c.response_chars.toLocaleString()} ch`
                    : "—"}
                </p>
              </div>
            ))}
          </div>
        </div>

        <p className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-label)] mt-6">
          Firecrawl Hobby plan · 1 cr per scrape or map ·
          credit balance refreshes monthly · per-domain rollup is the last 7 days
        </p>
      </div>
    </div>
  );
}
