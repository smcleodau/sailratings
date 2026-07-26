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
      ? "text-[#C92B12]"
      : tone === "good"
      ? "text-[#2E7D54]"
      : "text-[#162423]";
  return (
    <div className="border border-[#0C5F5C]/12 bg-white rounded-[4px] px-4 py-3 shadow-sm">
      <p className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[#7E948F]">
        {label}
      </p>
      <p className={`heading-display text-2xl mt-1 tabular-nums ${valueClass}`}>
        {value}
      </p>
      {sub && (
        <p className="admin-mono-font text-[9px] text-[#52655F] mt-1">{sub}</p>
      )}
    </div>
  );
}

function ModePill({ mode }: { mode: CallMode }) {
  const cls =
    mode === "scrape"
      ? "bg-[#E8B23A]/15 text-[#8A6613] border-[#8A6613]/30"
      : "bg-[#0C5F5C]/10 text-[#0C5F5C] border-[#0C5F5C]/20";
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
      <span className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[#2E7D54]">
        <CheckCircle2 size={11} strokeWidth={2} /> ok
      </span>
    );
  }
  if (status === "empty") {
    return (
      <span className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[#7E948F]">
        empty
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[#C92B12]">
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
      <div className="flex-1 flex items-center justify-center px-6 bg-[#F3F1EC]">
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
          <h1 className="heading-display text-2xl text-[#162423] text-center mb-6">
            Firecrawl
          </h1>
          <input
            type="password"
            value={pwInput}
            onChange={(e) => setPwInput(e.target.value)}
            placeholder="Admin password"
            className="w-full h-12 px-4 bg-white border border-[#0C5F5C]/25 text-[#162423] text-[13px] placeholder:text-[#7E948F] focus:border-[#0C5F5C] focus:ring-1 focus:ring-[#0C5F5C]/20 outline-none transition-all rounded-[4px] shadow-sm"
          />
          <button
            type="submit"
            className="w-full h-12 bg-[#0C5F5C] text-white text-[13px] font-medium hover:bg-[#3E9B95] transition-colors rounded-[4px] shadow-sm"
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
    <div className="flex-1 overflow-y-auto bg-[#F3F1EC]">
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-end justify-between mb-8 gap-6 flex-wrap">
          <div>
            <h1 className="heading-display text-2xl text-[#162423]">Firecrawl</h1>
            <p className="text-[13px] text-[#52655F] mt-1">
              Per-call telemetry for every scrape + map request. Auto-refreshes
              every minute.
            </p>
          </div>
          <div className="flex items-center gap-4 text-[#7E948F]">
            {summary?.as_of && (
              <span className="admin-mono-font text-[10px] uppercase tracking-[0.16em]">
                As of {fmtDateTime(summary.as_of)}
              </span>
            )}
            <button
              onClick={fetchAll}
              disabled={loading}
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[#7E948F] hover:text-[#162423] transition-colors disabled:opacity-40"
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
          <div className="border border-[#C92B12]/40 bg-[#C92B12]/5 px-4 py-3 mb-6 text-[13px] text-[#C92B12] rounded-[4px]">
            {error}
          </div>
        )}

        {/* Credit-balance banner — the authoritative number from Firecrawl */}
        {summary?.remaining ? (
          <div className="border border-[#0C5F5C]/12 bg-white rounded-[4px] shadow-sm p-4 mb-6">
            <div className="flex items-baseline justify-between gap-4 flex-wrap mb-2">
              <span className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[#7E948F]">
                Credit balance · Firecrawl
              </span>
              <span
                className={`admin-mono-font text-[10px] uppercase tracking-[0.16em] ${
                  remainingTone === "warn"
                    ? "text-[#C92B12]"
                    : remainingTone === "good"
                    ? "text-[#2E7D54]"
                    : "text-[#52655F]"
                }`}
              >
                {remaining?.toLocaleString()} remaining
                {plan ? ` of ${plan.toLocaleString()}` : ""}
              </span>
            </div>
            {planUsedPct != null && (
              <div className="h-1.5 bg-[#F6F4EE] rounded-[2px] overflow-hidden">
                <div
                  className={`h-full ${
                    remainingTone === "warn"
                      ? "bg-[#C92B12]"
                      : remainingTone === "good"
                      ? "bg-[#2E7D54]"
                      : "bg-[#0C5F5C]/60"
                  }`}
                  style={{ width: `${planUsedPct * 100}%` }}
                />
              </div>
            )}
            {monthlyBurn != null && plan != null && (
              <p className="admin-mono-font text-[10px] text-[#7E948F] mt-2">
                Projected monthly burn (from last 7d):{" "}
                <span className="text-[#162423] tabular-nums">
                  {monthlyBurn.toLocaleString()}
                </span>{" "}
                cr ·{" "}
                <span
                  className={
                    monthlyBurn > plan ? "text-[#C92B12]" : "text-[#2E7D54]"
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
          <div className="border border-[#8A6613]/30 bg-[#E8B23A]/10 px-4 py-3 mb-6 flex items-start gap-3 rounded-[4px]">
            <AlertTriangle size={16} className="text-[#8A6613] flex-shrink-0 mt-0.5" />
            <p className="text-[13px] text-[#8A6613]">
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
            <h2 className="heading-display text-base text-[#162423]">
              Per-domain · last 7 days
            </h2>
            <span className="admin-mono-font text-[9px] text-[#7E948F]">
              {domains.length} domain{domains.length === 1 ? "" : "s"}
            </span>
          </div>
          <div className="border border-[#0C5F5C]/12 bg-white rounded-[4px] shadow-sm overflow-hidden">
            <div className="grid grid-cols-[1.6fr_70px_70px_90px_90px_120px] gap-4 px-4 py-3 bg-[#F6F4EE] border-b border-[#0C5F5C]/12 admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[#7E948F] font-medium">
              <span>Domain</span>
              <span className="text-right">Calls</span>
              <span className="text-right">Cr</span>
              <span className="text-right">Success</span>
              <span className="text-right">Avg latency</span>
              <span className="text-right">Last call</span>
            </div>
            {domains.length === 0 && !loading && (
              <div className="px-4 py-6 text-[13px] text-[#7E948F] italic text-center">
                No Firecrawl calls in the last 7 days.
              </div>
            )}
            {domains.map((d) => {
              const successCls =
                d.success_rate >= 0.95
                  ? "text-[#2E7D54]"
                  : d.success_rate >= 0.7
                  ? "text-[#52655F]"
                  : "text-[#C92B12]";
              return (
                <div
                  key={d.domain}
                  className="grid grid-cols-[1.6fr_70px_70px_90px_90px_120px] gap-4 px-4 py-2.5 items-center border-b border-[#0C5F5C]/5 last:border-b-0 hover:bg-[#F6F4EE] transition-colors"
                >
                  <p className="text-[13px] text-[#162423] truncate">
                    {d.domain}
                  </p>
                  <p className="admin-mono-font text-[10px] text-[#52655F] tabular-nums text-right">
                    {d.calls}
                  </p>
                  <p className="admin-mono-font text-[10px] text-[#52655F] tabular-nums text-right">
                    {d.credits}
                  </p>
                  <p
                    className={`admin-mono-font text-[10px] tabular-nums text-right ${successCls}`}
                  >
                    {pct(d.success_rate)}
                    {d.errored > 0 && (
                      <span className="text-[#7E948F]"> · {d.errored} err</span>
                    )}
                  </p>
                  <p className="admin-mono-font text-[10px] text-[#7E948F] tabular-nums text-right">
                    {fmtMs(d.avg_ms)}
                  </p>
                  <p className="admin-mono-font text-[10px] text-[#7E948F] tabular-nums text-right">
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
            <h2 className="heading-display text-base text-[#162423]">
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
                      ? "border-[#0C5F5C] text-[#0C5F5C] bg-[#E6F0EE]"
                      : "border-[#0C5F5C]/12 text-[#7E948F] bg-white hover:text-[#162423]"
                  }`}
                >
                  {s}
                </button>
              ))}
              <span className="text-[#0C5F5C]/20 mx-1">·</span>
              {(["all", "scrape", "map"] as const).map((m) => (
                <button
                  key={m}
                  onClick={() => setModeFilter(m)}
                  className={`admin-mono-font text-[9px] uppercase tracking-[0.14em] px-2.5 py-1 border rounded-[4px] transition-colors ${
                    modeFilter === m
                      ? "border-[#0C5F5C] text-[#0C5F5C] bg-[#E6F0EE]"
                      : "border-[#0C5F5C]/12 text-[#7E948F] bg-white hover:text-[#162423]"
                  }`}
                >
                  {m}
                </button>
              ))}
            </div>
          </div>

          <div className="border border-[#0C5F5C]/12 bg-white rounded-[4px] shadow-sm overflow-hidden">
            <div className="grid grid-cols-[110px_70px_1fr_70px_80px_70px_90px] gap-3 px-4 py-3 bg-[#F6F4EE] border-b border-[#0C5F5C]/12 admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[#7E948F] font-medium">
              <span>When</span>
              <span>Mode</span>
              <span>URL</span>
              <span className="text-right">Status</span>
              <span className="text-right">Latency</span>
              <span className="text-right">Cr</span>
              <span className="text-right">Size / Links</span>
            </div>

            {loading && recent.length === 0 && (
              <div className="px-4 py-6 flex items-center gap-2 text-[#7E948F] text-[13px]">
                <Loader2 size={14} className="animate-spin" /> Loading…
              </div>
            )}

            {!loading && recent.length === 0 && (
              <div className="px-4 py-8 text-[13px] text-[#7E948F] italic text-center flex items-center justify-center gap-2">
                <Clock size={14} /> No calls match the current filter.
              </div>
            )}

            {recent.map((c) => (
              <div
                key={c.id}
                className="grid grid-cols-[110px_70px_1fr_70px_80px_70px_90px] gap-3 px-4 py-2 items-center border-b border-[#0C5F5C]/5 last:border-b-0 hover:bg-[#F6F4EE] transition-colors"
              >
                <p
                  className="admin-mono-font text-[10px] text-[#7E948F] tabular-nums truncate"
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
                    className="text-[11px] text-[#52655F] hover:text-[#0C5F5C] truncate inline-flex items-center gap-1 max-w-full transition-colors"
                    title={c.url}
                  >
                    <span className="truncate">
                      {c.url.replace(/^https?:\/\/(www\.)?/, "")}
                    </span>
                    <ExternalLink size={10} className="flex-shrink-0 text-[#7E948F]" />
                  </a>
                  {c.error_message && (
                    <p
                      className="admin-mono-font text-[9px] text-[#C92B12] truncate mt-0.5"
                      title={c.error_message}
                    >
                      {c.error_message}
                    </p>
                  )}
                  {c.caller && (
                    <p className="admin-mono-font text-[9px] text-[#7E948F] mt-0.5">
                      caller: {c.caller}
                    </p>
                  )}
                </div>
                <div className="text-right">
                  <StatusPill status={c.status} />
                </div>
                <p className="admin-mono-font text-[10px] text-[#7E948F] tabular-nums text-right">
                  {fmtMs(c.duration_ms)}
                </p>
                <p className="admin-mono-font text-[10px] text-[#7E948F] tabular-nums text-right">
                  {c.credits ?? "—"}
                </p>
                <p className="admin-mono-font text-[10px] text-[#7E948F] tabular-nums text-right">
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

        <p className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[#7E948F] mt-6">
          Firecrawl Hobby plan · 1 cr per scrape or map ·
          credit balance refreshes monthly · per-domain rollup is the last 7 days
        </p>
      </div>
    </div>
  );
}
