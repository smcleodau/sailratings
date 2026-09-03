"use client";

/**
 * /admin — the Today screen (AD-01-13).
 *
 * "What needs a human today, in one call and one screen."  One fetch to
 * GET /v1/admin/overview renders:
 *
 *   - four stat tiles          (attention, new today, dupe clusters, corrections)
 *   - the Attention list       (server-side attention rules, SPEC-22 §3.1)
 *   - the Sources table        (register ⋈ schedule state ⋈ ledger, stale pills,
 *                               per-source 14-day sparkline)
 *   - runs-per-day sparkline   (60d, zero-run bands in Buoy)
 *   - boats count + completeness meters
 */

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Anchor,
  CheckCircle2,
  Clock,
  GitMerge,
  ListChecks,
  RefreshCw,
  Waves,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

/* ── Types (mirror the admin-overview-v1 contract) ─────────────────────── */

interface Last14Day {
  day: string;
  runs: number;
  failed: number;
  new: number;
}

interface SourceRow {
  slug: string;
  display_name: string;
  cadence: string;
  enabled: boolean;
  paused: boolean;
  schedule_id: string | null;
  legal_status: string | null;
  adapter_status: string | null;
  last_run_at: string | null;
  last_completed_at: string | null;
  last_status: string | null;
  runs_total: number;
  runs_14d: number;
  failed_14d: number;
  stale_days: number | null;
  budget_hours: number;
  stale: boolean;
  last14: Last14Day[];
}

interface AttentionItem {
  kind: string;
  severity: "critical" | "warning" | "info";
  source: string | null;
  title: string;
  detail: string;
  stale_days: number | null;
  href: string;
}

interface Overview {
  schema_version: string;
  as_of: string;
  today: {
    date: string;
    runs: number;
    completed: number;
    failed: number;
    found: number;
    new: number;
    updated: number;
  };
  overview: {
    sources_tracked: number;
    sources_stale: number;
    sources_failed: number;
    sources_paused: number;
    attention_count: number;
    dupes_pending_clusters: number;
    corrections_pending: number;
    boats: number;
  };
  sources: SourceRow[];
  runs_per_day: {
    days: number;
    series: { day: string; runs: number; failed: number }[];
  };
  dupes: {
    available: boolean;
    pending: number;
    pending_clusters: number;
    by_tier: Record<string, number>;
  };
  corrections: { available: boolean; pending: number };
  fleet: {
    available: boolean;
    boats: number;
    completeness: Record<string, { count: number; pct: number }>;
  };
  attention: AttentionItem[];
}

/* ── Formatting helpers ────────────────────────────────────────────────── */

function fmtDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}



/* ── Stat tiles ────────────────────────────────────────────────────────── */

function StatTile({
  label,
  value,
  sub,
  tone,
  icon,
  testId,
}: {
  label: string;
  value: React.ReactNode;
  sub?: string;
  tone?: "bad" | "warn" | "ok" | "neutral";
  icon?: React.ReactNode;
  testId?: string;
}) {
  const colour =
    tone === "bad"
      ? "text-[var(--sr-action-pressed)]"
      : tone === "warn"
        ? "text-[var(--sr-status-warning)]"
        : tone === "ok"
          ? "text-[var(--sr-status-success)]"
          : "text-[var(--sr-text-primary)]";
  return (
    <div
      data-testid={testId}
      className="border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] px-4 py-3"
    >
      <div className="flex items-start justify-between gap-2">
        <div className={`heading-display text-3xl leading-none ${colour}`}>
          {value}
        </div>
        {icon && (
          <span className="text-[var(--sr-text-label)] mt-0.5">{icon}</span>
        )}
      </div>
      <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mt-2">
        {label}
      </div>
      {sub && (
        <div className="admin-mono-font text-[10px] text-[var(--sr-text-tertiary)] mt-1">
          {sub}
        </div>
      )}
    </div>
  );
}

/* ── Attention list ────────────────────────────────────────────────────── */

function SeverityDot({ severity }: { severity: AttentionItem["severity"] }) {
  const colour =
    severity === "critical"
      ? "bg-[var(--sr-action)]"
      : severity === "warning"
        ? "bg-[var(--sr-status-warning)]"
        : "bg-[var(--sr-status-info)]";
  return (
    <span
      className={`inline-block w-2 h-2 rounded-full mt-1.5 shrink-0 ${colour}`}
      aria-hidden
    />
  );
}

function AttentionList({ items }: { items: AttentionItem[] }) {
  if (items.length === 0) {
    return (
      <div
        data-testid="attention-empty"
        className="flex items-center gap-2 text-[var(--sr-status-success)] border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] px-4 py-6 justify-center"
      >
        <CheckCircle2 size={16} strokeWidth={2} />
        <span className="text-[13px]">Nothing needs a human today.</span>
      </div>
    );
  }
  return (
    <ul data-testid="attention-list" className="space-y-2">
      {items.map((item, idx) => (
        <li key={`${item.kind}-${item.source ?? "fleet"}-${idx}`}>
          <Link
            href={item.href}
            className="flex items-start gap-3 border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] px-4 py-3 hover:border-[var(--sr-border-strong)] transition-colors"
          >
            <SeverityDot severity={item.severity} />
            <div className="min-w-0 flex-1">
              <div className="flex items-baseline justify-between gap-3 flex-wrap">
                <span className="text-[13px] text-[var(--sr-text-primary)] font-medium">
                  {item.title}
                </span>
                <span className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-label)]">
                  {item.kind.replace(/_/g, " ")}
                </span>
              </div>
              <p className="text-[12px] text-[var(--sr-text-tertiary)] mt-0.5">
                {item.detail}
              </p>
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}

/* ── Sparklines ────────────────────────────────────────────────────────── */

function RunsSparkline({
  series,
  height = 56,
  testId,
}: {
  series: { day: string; runs: number; failed: number }[];
  height?: number;
  testId?: string;
}) {
  const max = Math.max(1, ...series.map((d) => d.runs));
  const n = series.length;
  return (
    <div
      data-testid={testId}
      className="flex items-end gap-px w-full"
      style={{ height }}
      role="img"
      aria-label="Runs per day, trailing 60 days"
    >
      {series.map((d) => {
        const h = d.runs === 0 ? 0 : Math.max(8, (d.runs / max) * 100);
        // Zero-run bands render in Buoy — a day nothing ran is the signal.
        const colour =
          d.runs === 0
            ? "bg-[var(--sr-buoy)]/25"
            : d.failed > 0
              ? "bg-[var(--sr-action-pressed)]"
              : "bg-[var(--sr-marine-400)]";
        return (
          <div
            key={d.day}
            title={`${d.day}: ${d.runs} run${d.runs === 1 ? "" : "s"}${
              d.failed ? `, ${d.failed} failed` : ""
            }`}
            className={`flex-1 rounded-[1px] ${colour}`}
            style={{ height: d.runs === 0 ? "100%" : `${h}%` }}
          />
        );
      })}
      {n === 0 && (
        <div className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
          no runs recorded
        </div>
      )}
    </div>
  );
}

function Last14Sparkline({ days }: { days: Last14Day[] }) {
  return (
    <div
      className="flex items-end gap-[2px] h-5 w-[86px]"
      role="img"
      aria-label="Last 14 days"
    >
      {days.map((d) => {
        const colour =
          d.runs === 0
            ? "bg-[var(--sr-buoy)]/30"
            : d.failed > 0
              ? "bg-[var(--sr-action-pressed)]"
              : d.new > 0
                ? "bg-[var(--sr-status-success)]"
                : "bg-[var(--sr-marine-400)]";
        return (
          <div
            key={d.day}
            title={`${d.day}: ${d.runs} run${d.runs === 1 ? "" : "s"}, ${
              d.new
            } new`}
            className={`flex-1 rounded-[1px] ${colour}`}
            style={{ height: d.runs === 0 ? "100%" : "70%" }}
          />
        );
      })}
    </div>
  );
}

/* ── Sources table ─────────────────────────────────────────────────────── */

function StalePill({ row }: { row: SourceRow }) {
  if (row.paused) {
    return (
      <span className="admin-mono-font text-[9px] uppercase tracking-[0.12em] px-1.5 py-0.5 border rounded-[3px] text-[var(--sr-text-label)] border-[var(--sr-border-subtle)]">
        paused
      </span>
    );
  }
  if (row.stale_days == null) {
    return (
      <span className="admin-mono-font text-[9px] uppercase tracking-[0.12em] px-1.5 py-0.5 border rounded-[3px] text-[var(--sr-action-pressed)] border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/5">
        never run
      </span>
    );
  }
  if (row.stale) {
    return (
      <span
        data-testid={`stale-pill-${row.slug}`}
        className="admin-mono-font text-[9px] uppercase tracking-[0.12em] px-1.5 py-0.5 border rounded-[3px] text-[var(--sr-status-warning)] border-[var(--sr-status-warning)]/40 bg-[var(--sr-status-warning)]/5"
      >
        stale {row.stale_days}d
      </span>
    );
  }
  return (
    <span className="admin-mono-font text-[9px] uppercase tracking-[0.12em] px-1.5 py-0.5 border rounded-[3px] text-[var(--sr-status-success)] border-[var(--sr-status-success)]/40 bg-[var(--sr-status-success)]/5">
      fresh
    </span>
  );
}

function StatusCell({ status }: { status: string | null }) {
  if (!status) return <span className="admin-mono-font text-[11px] text-[var(--sr-text-label)]">—</span>;
  const colour =
    status === "completed"
      ? "text-[var(--sr-status-success)]"
      : status === "failed"
        ? "text-[var(--sr-action-pressed)]"
        : "text-[var(--sr-status-info)]";
  return (
    <span className={`admin-mono-font text-[11px] ${colour}`}>{status}</span>
  );
}

function SourcesTable({ sources }: { sources: SourceRow[] }) {
  return (
    <div
      data-testid="sources-table"
      className="border border-[var(--sr-border-subtle)] rounded-[4px] overflow-hidden"
    >
      <table className="w-full text-left">
        <thead>
          <tr className="bg-[var(--sr-surface-card)] border-b border-[var(--sr-border-subtle)]">
            {["Source", "Cadence", "Last run", "Status", "Last 14d", "Freshness"].map(
              (h) => (
                <th
                  key={h}
                  className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] font-medium px-4 py-2.5"
                >
                  {h}
                </th>
              )
            )}
          </tr>
        </thead>
        <tbody>
          {sources.map((s) => (
            <tr
              key={s.slug}
              data-testid={`source-row-${s.slug}`}
              className="border-b border-[var(--sr-border-subtle)] last:border-0 bg-[var(--sr-surface-card)] hover:bg-[var(--sr-surface-interactive)]/40 transition-colors"
            >
              <td className="px-4 py-2.5">
                <div className="text-[13px] text-[var(--sr-text-primary)]">
                  {s.display_name}
                </div>
                <div className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
                  {s.slug}
                </div>
              </td>
              <td className="px-4 py-2.5 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                {s.cadence}
              </td>
              <td className="px-4 py-2.5 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                {fmtDateTime(s.last_run_at)}
              </td>
              <td className="px-4 py-2.5">
                <StatusCell status={s.last_status} />
              </td>
              <td className="px-4 py-2.5">
                <Last14Sparkline days={s.last14} />
              </td>
              <td className="px-4 py-2.5">
                <StalePill row={s} />
              </td>
            </tr>
          ))}
          {sources.length === 0 && (
            <tr>
              <td
                colSpan={6}
                className="px-4 py-8 text-center admin-mono-font text-[11px] text-[var(--sr-text-label)]"
              >
                No sources registered yet.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

/* ── Completeness meters ───────────────────────────────────────────────── */

const METER_LABELS: Record<string, string> = {
  sail_number: "Sail number",
  design: "Design",
  design_canonical: "Design (canonical)",
  country: "Country",
  year_built: "Year built",
};

function CompletenessMeters({
  completeness,
}: {
  completeness: Record<string, { count: number; pct: number }>;
}) {
  const keys = Object.keys(METER_LABELS).filter((k) => k in completeness);
  return (
    <div data-testid="completeness-meters" className="space-y-2.5">
      {keys.map((k) => {
        const m = completeness[k];
        return (
          <div key={k}>
            <div className="flex items-baseline justify-between gap-3">
              <span className="text-[12px] text-[var(--sr-text-secondary)]">
                {METER_LABELS[k]}
              </span>
              <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
                {m.count.toLocaleString()} · {m.pct.toFixed(1)}%
              </span>
            </div>
            <div className="h-1.5 rounded-full bg-[var(--sr-surface-card)] border border-[var(--sr-border-subtle)] overflow-hidden mt-1">
              <div
                className={`h-full rounded-full ${
                  m.pct >= 90
                    ? "bg-[var(--sr-status-success)]"
                    : m.pct >= 60
                      ? "bg-[var(--sr-buoy)]"
                      : "bg-[var(--sr-action-pressed)]"
                }`}
                style={{ width: `${Math.min(100, m.pct)}%` }}
              />
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ── Login gate (same convention as the other admin pages) ─────────────── */

function LoginGate({ onLogin }: { onLogin: (token: string) => void }) {
  const [pwInput, setPwInput] = useState("");
  return (
    <div className="flex-1 flex items-center justify-center px-6 bg-[var(--sr-surface-page)]">
      <form
        className="w-full max-w-sm space-y-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (pwInput.trim()) onLogin(pwInput.trim());
        }}
      >
        <h1 className="heading-display text-2xl text-[var(--sr-text-primary)] text-center mb-6">
          Today
        </h1>
        <input
          type="password"
          value={pwInput}
          onChange={(e) => setPwInput(e.target.value)}
          placeholder="Admin password"
          className="w-full h-12 px-4 bg-white border border-[var(--sr-link)]/25 text-[var(--sr-ink)] text-[13px] placeholder:text-[var(--sr-text-label)] focus:border-[var(--sr-link)] focus:ring-1 focus:ring-[var(--sr-link)]/20 outline-none transition-all rounded-[4px] shadow-sm"
        />
        <button
          type="submit"
          className="w-full h-12 bg-[var(--sr-link)] text-white text-[13px] font-medium hover:bg-[var(--sr-focus)] transition-colors rounded-[4px] shadow-sm"
        >
          Sign in
        </button>
      </form>
    </div>
  );
}

/* ── Page ──────────────────────────────────────────────────────────────── */

export default function AdminTodayPage() {
  const [token, setToken] = useState<string | null>(null);
  const [data, setData] = useState<Overview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t =
      localStorage.getItem("admin_token") ||
      process.env.NEXT_PUBLIC_ADMIN_PASSWORD ||
      "sailfast2026";
    if (t) {
      localStorage.setItem("admin_token", t);
      setToken(t);
    }
  }, []);

  const fetchOverview = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/admin/overview`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        localStorage.removeItem("admin_token");
        setToken(null);
        throw new Error("Session expired");
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchOverview();
    if (!token) return;
    const id = setInterval(fetchOverview, 60000);
    return () => clearInterval(id);
  }, [fetchOverview, token]);

  if (!token) {
    return (
      <LoginGate
        onLogin={(t) => {
          localStorage.setItem("admin_token", t);
          setToken(t);
        }}
      />
    );
  }

  const ov = data?.overview;
  const today = data?.today;
  const dupes = data?.dupes;
  const corrections = data?.corrections;

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--sr-surface-page)]">
      <div className="max-w-6xl mx-auto px-6 py-8 space-y-8">
        {/* Header */}
        <div className="flex items-end justify-between gap-6 flex-wrap">
          <div>
            <h1 className="heading-display text-2xl text-[var(--sr-text-primary)]">
              Today
            </h1>
            <p className="text-[13px] text-[var(--sr-text-tertiary)] mt-1">
              What needs a human today — sources, dupes, corrections and
              fleet completeness in one call. Auto-refreshes every minute.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {data && (
              <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
                as of {fmtDateTime(data.as_of)}
              </span>
            )}
            <button
              onClick={fetchOverview}
              disabled={loading}
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.14em] text-[var(--sr-text-secondary)] hover:text-[var(--sr-text-primary)] border border-[var(--sr-border-subtle)] rounded-[4px] px-3 py-2 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={12} strokeWidth={2} className={loading ? "animate-spin" : ""} />
              Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="flex items-center gap-2 border border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/5 text-[var(--sr-action-pressed)] rounded-[4px] px-4 py-3 text-[13px]">
            <AlertTriangle size={14} strokeWidth={2} />
            {error}
          </div>
        )}

        {/* Stat tiles */}
        <div
          data-testid="stat-tiles"
          className="grid grid-cols-2 lg:grid-cols-4 gap-3"
        >
          <StatTile
            testId="tile-attention"
            label="Needs attention"
            value={ov?.attention_count ?? "—"}
            sub={
              ov
                ? `${ov.sources_stale} stale · ${ov.sources_failed} failed`
                : undefined
            }
            tone={
              ov == null
                ? "neutral"
                : ov.attention_count === 0
                  ? "ok"
                  : "warn"
            }
            icon={<AlertTriangle size={16} strokeWidth={1.8} />}
          />
          <StatTile
            testId="tile-new-today"
            label="New rows today"
            value={today?.new ?? "—"}
            sub={
              today
                ? `${today.runs} run${today.runs === 1 ? "" : "s"} · ${today.failed} failed`
                : undefined
            }
            tone={today && today.failed > 0 ? "bad" : "neutral"}
            icon={<Waves size={16} strokeWidth={1.8} />}
          />
          <StatTile
            testId="tile-dupes"
            label="Dupe clusters pending"
            value={dupes?.pending_clusters ?? "—"}
            sub={
              dupes?.available
                ? `${dupes.pending} boats · ${Object.entries(dupes.by_tier)
                    .map(([t, n]) => `${t}:${n}`)
                    .join(" ")}`
                : "queue unavailable"
            }
            tone={dupes && dupes.pending_clusters > 0 ? "warn" : "ok"}
            icon={<GitMerge size={16} strokeWidth={1.8} />}
          />
          <StatTile
            testId="tile-corrections"
            label="Corrections pending"
            value={corrections?.pending ?? "—"}
            sub={corrections?.available ? undefined : "queue unavailable"}
            tone={corrections && corrections.pending > 0 ? "warn" : "ok"}
            icon={<ListChecks size={16} strokeWidth={1.8} />}
          />
        </div>

        {/* Attention */}
        <section>
          <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.18em] text-[var(--sr-text-label)] mb-3">
            Attention
          </h2>
          <AttentionList items={data?.attention ?? []} />
        </section>

        {/* Runs per day */}
        <section>
          <div className="flex items-baseline justify-between gap-3 mb-3">
            <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.18em] text-[var(--sr-text-label)]">
              Runs per day — trailing {data?.runs_per_day.days ?? 60} days
            </h2>
            <span className="admin-mono-font text-[9px] text-[var(--sr-text-label)] inline-flex items-center gap-1.5">
              <span className="inline-block w-2 h-2 rounded-[1px] bg-[var(--sr-buoy)]/40" />
              zero-run band
            </span>
          </div>
          <div className="border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] px-4 py-4">
            <RunsSparkline
              testId="runs-per-day-sparkline"
              series={data?.runs_per_day.series ?? []}
            />
          </div>
        </section>

        {/* Sources */}
        <section>
          <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.18em] text-[var(--sr-text-label)] mb-3">
            Sources
          </h2>
          <SourcesTable sources={data?.sources ?? []} />
        </section>

        {/* Fleet */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div
            data-testid="boats-tile"
            className="border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] px-4 py-4"
          >
            <div className="flex items-start justify-between gap-2">
              <div className="heading-display text-3xl leading-none text-[var(--sr-text-primary)]">
                {data?.fleet.boats.toLocaleString() ?? "—"}
              </div>
              <span className="text-[var(--sr-text-label)] mt-0.5">
                <Anchor size={16} strokeWidth={1.8} />
              </span>
            </div>
            <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mt-2">
              Boats in fleet
            </div>
            {today && (
              <div className="admin-mono-font text-[10px] text-[var(--sr-text-tertiary)] mt-1 inline-flex items-center gap-1">
                <Clock size={10} strokeWidth={2} />
                {today.found.toLocaleString()} rows seen today
              </div>
            )}
          </div>
          <div className="md:col-span-2 border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] px-4 py-4">
            <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-3">
              Fleet completeness
            </div>
            {data?.fleet.available ? (
              <CompletenessMeters completeness={data.fleet.completeness} />
            ) : (
              <div className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
                fleet table unavailable
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
