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

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

/* ────────────────────────────────────────────────────────────────── */
/* Types                                                              */
/* ────────────────────────────────────────────────────────────────── */

type CallMode = "scrape" | "map";
type CallStatus = "ok" | "empty" | "error";

interface WindowStats {
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

interface DailyCapState {
  daily_credit_cap: number | null;
  used_today: number | null;
  daily_capped: boolean;
}

interface SummaryPayload {
  as_of: string;
  remaining: { remaining_credits: number; plan_credits: number | null } | null;
  windows: Record<string, WindowStats>;
  daily?: DailyCapState;
}

interface RecentCall {
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

interface DomainStat {
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
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<CallStatus | "all">("all");
  const [modeFilter, setModeFilter] = useState<CallMode | "all">("all");

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
      const headers = { Authorization: `Bearer ${token}` };
      const recentParams = new URLSearchParams({ limit: "150" });
      if (statusFilter !== "all") recentParams.set("status", statusFilter);
      if (modeFilter !== "all") recentParams.set("mode", modeFilter);

      const [sumRes, recRes, domRes] = await Promise.all([
        fetch(`${API_BASE}/admin/firecrawl/summary`, { headers }),
        fetch(`${API_BASE}/admin/firecrawl/recent?${recentParams}`, { headers }),
        fetch(`${API_BASE}/admin/firecrawl/by-domain?days=7`, { headers }),
      ]);

      for (const r of [sumRes, recRes, domRes]) {
        if (!r.ok) {
          if (r.status === 401 || r.status === 403) {
            localStorage.removeItem("admin_token");
            setToken(null);
            throw new Error("Session expired. Sign in again.");
          }
          throw new Error(`Failed: ${r.status}`);
        }
      }

      const [sumJson, recJson, domJson] = await Promise.all([
        sumRes.json(),
        recRes.json(),
        domRes.json(),
      ]);
      setSummary(sumJson);
      setRecent(recJson.calls);
      setDomains(domJson.domains);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
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

  const remaining = summary?.remaining?.remaining_credits;
  const plan = summary?.remaining?.plan_credits;
  const planUsedPct =
    remaining != null && plan
      ? Math.max(0, Math.min(1, 1 - remaining / plan))
      : null;
  const remainingTone: "good" | "warn" | "neutral" =
    planUsedPct == null
      ? "neutral"
      : planUsedPct > 0.9
      ? "warn"
      : planUsedPct > 0.7
      ? "neutral"
      : "good";

  // Project monthly burn from the 7-day window (×30/7) — only meaningful
  // if we have meaningful traffic.
  const monthlyBurn = w7 && w7.credits > 0 ? Math.round((w7.credits * 30) / 7) : null;

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

        {/* Credit-balance banner — the authoritative number from Firecrawl */}
        {summary?.remaining ? (
          <div className="border border-[var(--sr-link)]/12 bg-[var(--sr-surface-card)] rounded-[4px] shadow-sm p-4 mb-6">
            <div className="flex items-baseline justify-between gap-4 flex-wrap mb-2">
              <span className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
                Credit balance · Firecrawl
              </span>
              <span
                className={`admin-mono-font text-[10px] uppercase tracking-[0.16em] ${
                  remainingTone === "warn"
                    ? "text-[var(--sr-action-pressed)]"
                    : remainingTone === "good"
                    ? "text-[var(--sr-status-success)]"
                    : "text-[var(--sr-text-tertiary)]"
                }`}
              >
                {remaining?.toLocaleString()} remaining
                {plan ? ` of ${plan.toLocaleString()}` : ""}
              </span>
            </div>
            {planUsedPct != null && (
              <div className="h-1.5 bg-[var(--sr-surface-interactive)] rounded-[2px] overflow-hidden">
                <div
                  className={`h-full ${
                    remainingTone === "warn"
                      ? "bg-[var(--sr-action-pressed)]"
                      : remainingTone === "good"
                      ? "bg-[var(--sr-status-success)]"
                      : "bg-[var(--sr-link)]/60"
                  }`}
                  style={{ width: `${planUsedPct * 100}%` }}
                />
              </div>
            )}
            {monthlyBurn != null && plan != null && (
              <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] mt-2">
                Projected monthly burn (from last 7d):{" "}
                <span className="text-[var(--sr-text-primary)] tabular-nums">
                  {monthlyBurn.toLocaleString()}
                </span>{" "}
                cr ·{" "}
                <span
                  className={
                    monthlyBurn > plan ? "text-[var(--sr-action-pressed)]" : "text-[var(--sr-status-success)]"
                  }
                >
                  {monthlyBurn > plan
                    ? `${Math.round((monthlyBurn / plan - 1) * 100)}% over plan`
                    : `${Math.round((1 - monthlyBurn / plan) * 100)}% headroom`}
                </span>
              </p>
            )}
          </div>
        ) : (
          <div className="border border-[var(--sr-status-warning)]/30 bg-[var(--sr-status-warning)]/10 px-4 py-3 mb-6 flex items-start gap-3 rounded-[4px]">
            <AlertTriangleIcon size={16} className="text-[var(--sr-status-warning)] flex-shrink-0 mt-0.5" />
            <p className="text-[13px] text-[var(--sr-status-warning)]">
              Credit balance unavailable. CheckIcon that FIRECRAWL_API_KEY is set on
              the API process.
            </p>
          </div>
        )}

        {/* Daily hard-stop banner (OPS-02-06 / AD-01-08) */}
        {summary?.daily && summary.daily.daily_credit_cap != null && (
          <div
            className={`border px-4 py-3 mb-6 flex items-start gap-3 rounded-[4px] ${
              summary.daily.daily_capped
                ? "border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/5"
                : "border-[var(--sr-link)]/12 bg-[var(--sr-surface-card)]"
            } shadow-sm`}
          >
            {summary.daily.daily_capped ? (
              <AlertTriangleIcon
                size={16}
                className="text-[var(--sr-action-pressed)] flex-shrink-0 mt-0.5"
              />
            ) : (
              <CheckCircleIcon
                size={16}
                className="text-[var(--sr-status-success)] flex-shrink-0 mt-0.5"
              />
            )}
            <div className="flex-1">
              <p
                className={`text-[13px] font-medium ${
                  summary.daily.daily_capped
                    ? "text-[var(--sr-action-pressed)]"
                    : "text-[var(--sr-text-primary)]"
                }`}
              >
                {summary.daily.daily_capped
                  ? "Daily credit cap reached — Firecrawl calls are stopped (hard stop)"
                  : "Daily credit cap"}
              </p>
              <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] mt-1 tabular-nums">
                {summary.daily.used_today ?? 0} / {summary.daily.daily_credit_cap} credits
                used today (UTC)
                {summary.daily.daily_capped
                  ? " · refusing non-manual calls until the day rolls"
                  : ""}
              </p>
              {summary.daily.daily_credit_cap != null &&
                summary.daily.used_today != null &&
                summary.daily.daily_credit_cap > 0 && (
                  <div className="h-1.5 bg-[var(--sr-surface-interactive)] rounded-[2px] overflow-hidden mt-2 max-w-md">
                    <div
                      className={`h-full ${
                        summary.daily.daily_capped
                          ? "bg-[var(--sr-action-pressed)]"
                          : "bg-[var(--sr-link)]/60"
                      }`}
                      style={{
                        width: `${Math.min(
                          100,
                          (summary.daily.used_today / summary.daily.daily_credit_cap) * 100
                        )}%`,
                      }}
                    />
                  </div>
                )}
            </div>
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

            {loading && recent.length === 0 && (
              <div className="px-4 py-6 flex items-center gap-2 text-[var(--sr-text-label)] text-[13px]">
                <SpinnerIcon size={14} className="animate-spin" /> Loading…
              </div>
            )}

            {!loading && recent.length === 0 && (
              <div className="px-4 py-8 text-[13px] text-[var(--sr-text-label)] italic text-center flex items-center justify-center gap-2">
                <ClockIcon size={14} /> No calls match the current filter.
              </div>
            )}

            {recent.map((c) => (
              <div
                key={c.id}
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
