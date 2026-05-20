"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  ExternalLink,
  Loader2,
  RefreshCw,
} from "lucide-react";

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

interface SummaryPayload {
  as_of: string;
  remaining: { remaining_credits: number; plan_credits: number | null } | null;
  windows: Record<string, WindowStats>;
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

interface DiffRow {
  id: number;
  ran_at: string;
  source: string;
  source_url: string;
  event_name: string | null;
  event_date: string | null;
  legacy_rows: number;
  firecrawl_rows: number;
  matched: number;
  match_rate: number;
  confidence: number | null;
  missing_names: string[];
  extra_names: string[];
  notes: string | null;
}

interface DiffRollup {
  source: string;
  runs: number;
  avg_rate: number;
  min_rate: number;
  green: number;
  amber: number;
  red: number;
  last_run: string | null;
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
      ? "text-brass"
      : tone === "good"
      ? "text-emerald-400"
      : "text-white/90";
  return (
    <div className="border border-white/10 bg-white/[0.02] rounded-sm px-4 py-3">
      <p className="data-mono text-[10px] uppercase tracking-[0.16em] text-white/40">
        {label}
      </p>
      <p className={`heading-display text-2xl mt-1 tabular-nums ${valueClass}`}>
        {value}
      </p>
      {sub && (
        <p className="data-mono text-[11px] text-white/35 mt-1">{sub}</p>
      )}
    </div>
  );
}

function ModePill({ mode }: { mode: CallMode }) {
  const cls =
    mode === "scrape"
      ? "bg-blue-500/15 text-blue-300 border-blue-400/30"
      : "bg-purple-500/15 text-purple-300 border-purple-400/30";
  return (
    <span
      className={`data-mono text-[10px] uppercase tracking-[0.14em] px-2 py-0.5 border ${cls}`}
    >
      {mode}
    </span>
  );
}

function StatusPill({ status }: { status: CallStatus }) {
  if (status === "ok") {
    return (
      <span className="inline-flex items-center gap-1 data-mono text-[10px] uppercase tracking-[0.14em] text-emerald-400/90">
        <CheckCircle2 size={11} strokeWidth={2} /> ok
      </span>
    );
  }
  if (status === "empty") {
    return (
      <span className="data-mono text-[10px] uppercase tracking-[0.14em] text-white/40">
        empty
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 data-mono text-[10px] uppercase tracking-[0.14em] text-brass">
      <AlertTriangle size={11} strokeWidth={2} /> error
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
  const [diffs, setDiffs] = useState<DiffRow[]>([]);
  const [diffRollup, setDiffRollup] = useState<DiffRollup[]>([]);
  const [expandedDiff, setExpandedDiff] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState<CallStatus | "all">("all");
  const [modeFilter, setModeFilter] = useState<CallMode | "all">("all");

  useEffect(() => {
    const t = localStorage.getItem("admin_token");
    if (t) setToken(t);
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

      const [sumRes, recRes, domRes, diffRes] = await Promise.all([
        fetch(`${API_BASE}/admin/firecrawl/summary`, { headers }),
        fetch(`${API_BASE}/admin/firecrawl/recent?${recentParams}`, { headers }),
        fetch(`${API_BASE}/admin/firecrawl/by-domain?days=7`, { headers }),
        fetch(`${API_BASE}/admin/firecrawl/diffs?limit=50`, { headers }),
      ]);

      for (const r of [sumRes, recRes, domRes, diffRes]) {
        if (!r.ok) {
          if (r.status === 401 || r.status === 403) {
            localStorage.removeItem("admin_token");
            setToken(null);
            throw new Error("Session expired. Sign in again.");
          }
          throw new Error(`Failed: ${r.status}`);
        }
      }

      const [sumJson, recJson, domJson, diffJson] = await Promise.all([
        sumRes.json(),
        recRes.json(),
        domRes.json(),
        diffRes.json(),
      ]);
      setSummary(sumJson);
      setRecent(recJson.calls);
      setDomains(domJson.domains);
      setDiffs(diffJson.diffs);
      setDiffRollup(diffJson.rollup);
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
      <div className="flex-1 flex items-center justify-center px-6">
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
          <h1 className="heading-display text-2xl text-white/90 text-center">
            Firecrawl
          </h1>
          <input
            type="password"
            value={pwInput}
            onChange={(e) => setPwInput(e.target.value)}
            placeholder="Admin password"
            className="w-full h-12 px-4 bg-navy-light border border-white/10 text-white body-text"
          />
          <button
            type="submit"
            className="w-full h-12 bg-brass text-white body-text font-medium"
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
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-end justify-between mb-8 gap-6 flex-wrap">
          <div>
            <h1 className="heading-display text-2xl text-white/90">Firecrawl</h1>
            <p className="body-text text-sm text-white/40 mt-1">
              Per-call telemetry for every scrape + map request. Auto-refreshes
              every minute.
            </p>
          </div>
          <div className="flex items-center gap-4 text-white/35">
            {summary?.as_of && (
              <span className="data-mono text-[11px] uppercase tracking-[0.16em]">
                As of {fmtDateTime(summary.as_of)}
              </span>
            )}
            <button
              onClick={fetchAll}
              disabled={loading}
              className="inline-flex items-center gap-1.5 data-mono text-[11px] uppercase tracking-[0.16em] text-white/40 hover:text-white/70 transition-colors disabled:opacity-40"
            >
              <RefreshCw
                size={12}
                strokeWidth={2}
                className={loading ? "animate-spin" : ""}
              />
              Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="border border-brass/40 bg-brass/5 px-4 py-3 mb-6 body-text text-sm text-brass">
            {error}
          </div>
        )}

        {/* Credit-balance banner — the authoritative number from Firecrawl */}
        {summary?.remaining ? (
          <div className="border border-white/10 bg-white/[0.02] rounded-sm p-4 mb-6">
            <div className="flex items-baseline justify-between gap-4 flex-wrap mb-2">
              <span className="data-mono text-[11px] uppercase tracking-[0.16em] text-white/55">
                Credit balance · Firecrawl
              </span>
              <span
                className={`data-mono text-[11px] uppercase tracking-[0.16em] ${
                  remainingTone === "warn"
                    ? "text-brass"
                    : remainingTone === "good"
                    ? "text-emerald-400/70"
                    : "text-white/45"
                }`}
              >
                {remaining?.toLocaleString()} remaining
                {plan ? ` of ${plan.toLocaleString()}` : ""}
              </span>
            </div>
            {planUsedPct != null && (
              <div className="h-1.5 bg-white/5 rounded-sm overflow-hidden">
                <div
                  className={`h-full ${
                    remainingTone === "warn"
                      ? "bg-brass"
                      : remainingTone === "good"
                      ? "bg-emerald-500/60"
                      : "bg-white/40"
                  }`}
                  style={{ width: `${planUsedPct * 100}%` }}
                />
              </div>
            )}
            {monthlyBurn != null && plan != null && (
              <p className="data-mono text-[11px] text-white/35 mt-2">
                Projected monthly burn (from last 7d):{" "}
                <span className="text-white/65 tabular-nums">
                  {monthlyBurn.toLocaleString()}
                </span>{" "}
                cr ·{" "}
                <span
                  className={
                    monthlyBurn > plan ? "text-brass" : "text-emerald-400/70"
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
          <div className="border border-amber-500/30 bg-amber-500/5 px-4 py-3 mb-6 flex items-start gap-3">
            <AlertTriangle size={16} className="text-amber-400 flex-shrink-0 mt-0.5" />
            <p className="body-text text-sm text-white/80">
              Credit balance unavailable. Check that FIRECRAWL_API_KEY is set on
              the API process.
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
            <h2 className="heading-display text-base text-white/85">
              Per-domain · last 7 days
            </h2>
            <span className="data-mono text-[10px] text-white/30">
              {domains.length} domain{domains.length === 1 ? "" : "s"}
            </span>
          </div>
          <div className="border border-white/10 rounded-sm overflow-hidden">
            <div className="grid grid-cols-[1.6fr_70px_70px_90px_90px_120px] gap-4 px-4 py-3 bg-white/[0.03] border-b border-white/10 data-mono text-[10px] uppercase tracking-[0.16em] text-white/35">
              <span>Domain</span>
              <span className="text-right">Calls</span>
              <span className="text-right">Cr</span>
              <span className="text-right">Success</span>
              <span className="text-right">Avg latency</span>
              <span className="text-right">Last call</span>
            </div>
            {domains.length === 0 && !loading && (
              <div className="px-4 py-6 body-text text-sm text-white/40 italic text-center">
                No Firecrawl calls in the last 7 days.
              </div>
            )}
            {domains.map((d) => {
              const successCls =
                d.success_rate >= 0.95
                  ? "text-emerald-400/85"
                  : d.success_rate >= 0.7
                  ? "text-white/70"
                  : "text-brass";
              return (
                <div
                  key={d.domain}
                  className="grid grid-cols-[1.6fr_70px_70px_90px_90px_120px] gap-4 px-4 py-2.5 items-center border-b border-white/5 last:border-b-0 hover:bg-white/[0.02]"
                >
                  <p className="body-text text-sm text-white/85 truncate">
                    {d.domain}
                  </p>
                  <p className="data-mono text-xs text-white/70 tabular-nums text-right">
                    {d.calls}
                  </p>
                  <p className="data-mono text-xs text-white/70 tabular-nums text-right">
                    {d.credits}
                  </p>
                  <p
                    className={`data-mono text-xs tabular-nums text-right ${successCls}`}
                  >
                    {pct(d.success_rate)}
                    {d.errored > 0 && (
                      <span className="text-white/30"> · {d.errored} err</span>
                    )}
                  </p>
                  <p className="data-mono text-xs text-white/55 tabular-nums text-right">
                    {fmtMs(d.avg_ms)}
                  </p>
                  <p className="data-mono text-xs text-white/45 tabular-nums text-right">
                    {fmtAge(d.last_called)}
                  </p>
                </div>
              );
            })}
          </div>
        </div>

        {/* Parallel-run diffs: legacy vs Firecrawl */}
        <div className="mb-8">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="heading-display text-base text-white/85">
              Parallel run · legacy ↔ Firecrawl
            </h2>
            <span className="data-mono text-[10px] text-white/30">
              {diffs.length} comparison{diffs.length === 1 ? "" : "s"}
            </span>
          </div>

          {/* Per-source rollup row — at-a-glance cut-over readiness */}
          {diffRollup.length > 0 && (
            <div className="border border-white/10 rounded-sm overflow-hidden mb-3">
              <div className="grid grid-cols-[1fr_60px_90px_90px_120px_110px] gap-3 px-4 py-3 bg-white/[0.03] border-b border-white/10 data-mono text-[10px] uppercase tracking-[0.16em] text-white/35">
                <span>Source</span>
                <span className="text-right">Runs</span>
                <span className="text-right">Avg</span>
                <span className="text-right">Min</span>
                <span className="text-right">G / A / R</span>
                <span className="text-right">Last run</span>
              </div>
              {diffRollup.map((r) => {
                const avgCls =
                  r.avg_rate >= 0.95
                    ? "text-emerald-400/85"
                    : r.avg_rate >= 0.85
                    ? "text-white/70"
                    : "text-brass";
                const ready = r.avg_rate >= 0.95 && r.red === 0;
                return (
                  <div
                    key={r.source}
                    className="grid grid-cols-[1fr_60px_90px_90px_120px_110px] gap-3 px-4 py-2.5 items-center border-b border-white/5 last:border-b-0 hover:bg-white/[0.02]"
                  >
                    <div className="flex items-center gap-2">
                      <span className="body-text text-sm text-white/85">{r.source}</span>
                      {ready && (
                        <span className="data-mono text-[10px] uppercase tracking-[0.14em] text-emerald-400/80 border border-emerald-400/30 px-1.5 py-0.5">
                          ready to cut
                        </span>
                      )}
                    </div>
                    <p className="data-mono text-xs text-white/70 tabular-nums text-right">
                      {r.runs}
                    </p>
                    <p className={`data-mono text-xs tabular-nums text-right ${avgCls}`}>
                      {pct(r.avg_rate)}
                    </p>
                    <p className="data-mono text-xs text-white/55 tabular-nums text-right">
                      {pct(r.min_rate)}
                    </p>
                    <p className="data-mono text-xs tabular-nums text-right">
                      <span className="text-emerald-400/85">{r.green}</span>
                      <span className="text-white/25"> / </span>
                      <span className="text-white/55">{r.amber}</span>
                      <span className="text-white/25"> / </span>
                      <span className={r.red > 0 ? "text-brass" : "text-white/25"}>
                        {r.red}
                      </span>
                    </p>
                    <p className="data-mono text-xs text-white/45 tabular-nums text-right">
                      {fmtAge(r.last_run)}
                    </p>
                  </div>
                );
              })}
            </div>
          )}

          {/* Per-event diff log */}
          <div className="border border-white/10 rounded-sm overflow-hidden">
            <div className="grid grid-cols-[100px_110px_1fr_90px_90px_80px] gap-3 px-4 py-3 bg-white/[0.03] border-b border-white/10 data-mono text-[10px] uppercase tracking-[0.16em] text-white/35">
              <span>When</span>
              <span>Source</span>
              <span>Event / URL</span>
              <span className="text-right">Rows L / F</span>
              <span className="text-right">Match</span>
              <span className="text-right">Conf</span>
            </div>
            {diffs.length === 0 && !loading && (
              <div className="px-4 py-8 body-text text-sm text-white/40 italic text-center">
                No parallel-run comparisons yet. Run{" "}
                <code className="data-mono text-white/55 bg-white/5 px-1">
                  irc-data firecrawl-diff --source &lt;X&gt;
                </code>{" "}
                to seed.
              </div>
            )}
            {diffs.map((d) => {
              const open = expandedDiff === d.id;
              const rateCls =
                d.match_rate >= 0.95
                  ? "text-emerald-400/85"
                  : d.match_rate >= 0.85
                  ? "text-white/75"
                  : "text-brass";
              return (
                <div key={d.id} className="border-b border-white/5 last:border-b-0">
                  <button
                    onClick={() => setExpandedDiff(open ? null : d.id)}
                    className="w-full grid grid-cols-[100px_110px_1fr_90px_90px_80px] gap-3 px-4 py-2.5 items-center text-left hover:bg-white/[0.02]"
                  >
                    <p className="data-mono text-[11px] text-white/55 tabular-nums truncate">
                      {fmtAge(d.ran_at)}
                    </p>
                    <p className="data-mono text-[11px] text-white/70 truncate">
                      {d.source}
                    </p>
                    <div className="min-w-0">
                      <p className="body-text text-xs text-white/80 truncate">
                        {d.event_name || d.source_url}
                      </p>
                      <p className="data-mono text-[10px] text-white/30 truncate">
                        {d.source_url.replace(/^https?:\/\/(www\.)?/, "")}
                      </p>
                    </div>
                    <p className="data-mono text-[11px] text-white/65 tabular-nums text-right">
                      {d.legacy_rows} / {d.firecrawl_rows}
                    </p>
                    <p className={`data-mono text-xs tabular-nums text-right ${rateCls}`}>
                      {pct(d.match_rate)}
                      <span className="text-white/30 ml-1 text-[10px]">
                        ({d.matched})
                      </span>
                    </p>
                    <p className="data-mono text-[11px] text-white/45 tabular-nums text-right">
                      {d.confidence != null ? pct(d.confidence) : "—"}
                    </p>
                  </button>
                  {open && (
                    <div className="px-4 pb-4 pt-1 bg-white/[0.015] grid grid-cols-2 gap-6">
                      <div>
                        <p className="data-mono text-[10px] uppercase tracking-[0.14em] text-brass/80 mb-1.5">
                          In legacy, missing from Firecrawl
                        </p>
                        {d.missing_names.length === 0 ? (
                          <p className="body-text text-xs text-white/35 italic">none</p>
                        ) : (
                          <ul className="data-mono text-[11px] text-white/65 space-y-0.5">
                            {d.missing_names.map((n) => (
                              <li key={n}>{n}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                      <div>
                        <p className="data-mono text-[10px] uppercase tracking-[0.14em] text-emerald-400/70 mb-1.5">
                          In Firecrawl, not in legacy
                        </p>
                        {d.extra_names.length === 0 ? (
                          <p className="body-text text-xs text-white/35 italic">none</p>
                        ) : (
                          <ul className="data-mono text-[11px] text-white/65 space-y-0.5">
                            {d.extra_names.map((n) => (
                              <li key={n}>{n}</li>
                            ))}
                          </ul>
                        )}
                      </div>
                      {d.notes && (
                        <div className="col-span-2">
                          <p className="data-mono text-[10px] uppercase tracking-[0.14em] text-white/35 mb-1.5">
                            Notes
                          </p>
                          <p className="body-text text-xs text-brass/80">{d.notes}</p>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>

        {/* Recent calls — log */}
        <div>
          <div className="flex items-baseline justify-between mb-3 gap-3 flex-wrap">
            <h2 className="heading-display text-base text-white/85">
              Recent calls
            </h2>
            <div className="flex items-center gap-2 flex-wrap">
              {/* status filter */}
              {(["all", "ok", "empty", "error"] as const).map((s) => (
                <button
                  key={s}
                  onClick={() => setStatusFilter(s)}
                  className={`data-mono text-[10px] uppercase tracking-[0.14em] px-2.5 py-1 border ${
                    statusFilter === s
                      ? "border-brass text-brass bg-brass/10"
                      : "border-white/10 text-white/40 hover:text-white/70"
                  }`}
                >
                  {s}
                </button>
              ))}
              <span className="text-white/15 mx-1">·</span>
              {(["all", "scrape", "map"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setModeFilter(m)}
                  className={`data-mono text-[10px] uppercase tracking-[0.14em] px-2.5 py-1 border ${
                    modeFilter === m
                      ? "border-brass text-brass bg-brass/10"
                      : "border-white/10 text-white/40 hover:text-white/70"
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>

          <div className="border border-white/10 rounded-sm overflow-hidden">
            <div className="grid grid-cols-[110px_70px_1fr_70px_80px_70px_90px] gap-3 px-4 py-3 bg-white/[0.03] border-b border-white/10 data-mono text-[10px] uppercase tracking-[0.16em] text-white/35">
              <span>When</span>
              <span>Mode</span>
              <span>URL</span>
              <span className="text-right">Status</span>
              <span className="text-right">Latency</span>
              <span className="text-right">Cr</span>
              <span className="text-right">Size / Links</span>
            </div>

            {loading && recent.length === 0 && (
              <div className="px-4 py-6 flex items-center gap-2 text-white/40 body-text text-sm">
                <Loader2 size={14} className="animate-spin" /> Loading…
              </div>
            )}

            {!loading && recent.length === 0 && (
              <div className="px-4 py-8 body-text text-sm text-white/40 italic text-center flex items-center justify-center gap-2">
                <Clock size={14} /> No calls match the current filter.
              </div>
            )}

            {recent.map((c) => (
              <div
                key={c.id}
                className="grid grid-cols-[110px_70px_1fr_70px_80px_70px_90px] gap-3 px-4 py-2 items-center border-b border-white/5 last:border-b-0 hover:bg-white/[0.02]"
              >
                <p
                  className="data-mono text-[11px] text-white/55 tabular-nums truncate"
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
                    className="body-text text-xs text-white/75 hover:text-white truncate inline-flex items-center gap-1 max-w-full"
                    title={c.url}
                  >
                    <span className="truncate">
                      {c.url.replace(/^https?:\/\/(www\.)?/, "")}
                    </span>
                    <ExternalLink size={10} className="flex-shrink-0 text-white/30" />
                  </a>
                  {c.error_message && (
                    <p
                      className="data-mono text-[10px] text-brass/80 truncate"
                      title={c.error_message}
                    >
                      {c.error_message}
                    </p>
                  )}
                  {c.caller && (
                    <p className="data-mono text-[10px] text-white/25">
                      caller: {c.caller}
                    </p>
                  )}
                </div>
                <div className="text-right">
                  <StatusPill status={c.status} />
                </div>
                <p className="data-mono text-[11px] text-white/55 tabular-nums text-right">
                  {fmtMs(c.duration_ms)}
                </p>
                <p className="data-mono text-[11px] text-white/55 tabular-nums text-right">
                  {c.credits ?? "—"}
                </p>
                <p className="data-mono text-[11px] text-white/45 tabular-nums text-right">
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

        <p className="data-mono text-[10px] uppercase tracking-[0.14em] text-white/25 mt-6">
          Firecrawl Hobby plan · 1 cr per scrape or map ·
          credit balance refreshes monthly · per-domain rollup is the last 7 days
        </p>
      </div>
    </div>
  );
}
