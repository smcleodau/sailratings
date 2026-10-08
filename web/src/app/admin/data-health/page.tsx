"use client";

import React, { useCallback, useEffect, useState } from "react";
import {
  AlertTriangleIcon,
  CheckCircleIcon,
  ChevronDownIcon,
  ChevronRightIcon,
  ClockIcon,
  DatabaseIcon,
  GitBranchIcon,
  ListChecksIcon,
  RefreshIcon,
  ShieldAlertIcon,
  UsersIcon,
} from "@/components/admin/AdminIcons";
import {
  BOATS_COMPLETENESS_COLUMNS,
  COMPLETENESS_WARNING_THRESHOLD,
  DataHealthApiError,
  EVENTS_VENUE_NULL_METRIC,
  fetchDataHealthCompleteness,
  fetchDataHealthDashboard,
  fetchDataHealthTables,
  runIncidentAction,
  type CompletenessMetric,
  type TableCensusRow,
} from "@/lib/adminApi.health";

/* ── Types ─────────────────────────────────────────────────────────────── */

interface OwnerInfo {
  handle: string;
  role: string;
  escalation: string;
}

interface Incident {
  incident_id: string;
  kind: string;
  severity: "info" | "warning" | "critical";
  status: "open" | "acknowledged" | "mitigating" | "resolved";
  source_slug: string | null;
  dataset: string | null;
  title: string;
  summary: string;
  detected_at: string;
  acknowledged_at: string | null;
  resolved_at: string | null;
  acknowledged_by: string | null;
  owner: OwnerInfo;
  affected_batches: string[];
  affected_consumers: string[];
  evidence: Record<string, unknown>;
  recommended_action: {
    kind?: string;
    policy?: string;
    summary?: string;
    replay_plan?: Record<string, unknown>;
  };
  notes: { at: string; by: string; note: string }[];
}

interface SourceRow {
  source: string;
  dataset: string | null;
  freshness: {
    last_started_at: string | null;
    last_completed_at: string | null;
    last_new_data_at: string | null;
    seconds_since_last_success: number | null;
    budget_seconds: number;
    stale: boolean;
    [k: string]: unknown;
  } | null;
  latest_yield: {
    run_id: number;
    yield_ratio: number | null;
    variance: number;
    decision: string;
    abrupt_yield_change: boolean;
    baseline_p10: number | null;
    baseline_p50: number | null;
  } | null;
  active_quarantine: boolean;
  quarantine?: { reason: string | null; since: string | null };
  open_source_incidents: number;
  open_data_incidents: number;
  gate_quarantined_batches: number;
  lineage_gap_runs: number;
}

interface Dashboard {
  as_of: string;
  window_days: number;
  overview: {
    sources_tracked: number;
    sources_stale: number;
    sources_quarantined: number;
    open_source_incidents: number;
    open_data_incidents: number;
    unacknowledged_data_incidents: number;
    blocking_reconciliations_in_window: number;
    lineage_gap_runs_in_window: number;
    gate_quarantine_open: number;
    identity_awaiting_review: number;
    identity_quarantined: number;
    slo_breaches: number;
  };
  sources: SourceRow[];
  identity_uncertainty: {
    available: boolean;
    awaiting_review_batches: number;
    quarantined_batches: number;
    consumer_impact: string;
  };
  lineage_gaps: {
    available: boolean;
    runs: {
      run_id: number;
      source: string;
      started_at: string | null;
      records_found: number | null;
      records_new: number | null;
    }[];
  };
  slo_breaches: {
    source_incident_id?: number;
    report_id?: string | null;
    source: string | null;
    kind: string;
    detected_at: string | null;
    block_reason?: string | null;
  }[];
  active_quarantines: {
    source: string | null;
    reason: string | null;
    since: string | null;
  }[];
  incidents: Incident[];
  availability: Record<string, boolean>;
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

function fmtAge(seconds: number | null | undefined): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${Math.floor(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

function fmtPct(x: number | null | undefined): string {
  if (x == null) return "—";
  return `${(x * 100).toFixed(1)}%`;
}

/** "computed <ago>" — the age of a nightly admin_metrics snapshot. */
function fmtComputedAgo(iso: string | null | undefined): string {
  if (!iso) return "computed —";
  const seconds = (Date.now() - new Date(iso).getTime()) / 1000;
  if (!Number.isFinite(seconds) || seconds < 0) return "computed —";
  return `computed ${fmtAge(seconds)} ago`;
}

function fmtBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let n = bytes;
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i += 1;
  }
  return `${n.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function fmtRows(rows: number | null | undefined): string {
  if (rows == null) return "—";
  return rows.toLocaleString("en-GB");
}

function errMessage(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/* ── Small presentational bits ─────────────────────────────────────────── */

function SeverityPill({ severity }: { severity: Incident["severity"] }) {
  const map = {
    critical: "text-[var(--sr-action-pressed)] border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/5",
    warning: "text-[var(--sr-status-warning)] border-[var(--sr-status-warning)]/40 bg-[var(--sr-status-warning)]/5",
    info: "text-[var(--sr-text-secondary)] border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)]",
  } as const;
  return (
    <span className={`admin-mono-font text-[9px] uppercase tracking-[0.12em] px-1.5 py-0.5 border rounded-[3px] ${map[severity]}`}>
      {severity}
    </span>
  );
}

function StatusPill({ status }: { status: Incident["status"] }) {
  const map: Record<Incident["status"], string> = {
    open: "text-[var(--sr-action-pressed)] border-[var(--sr-action-pressed)]/40",
    acknowledged: "text-[var(--sr-status-warning)] border-[var(--sr-status-warning)]/40",
    mitigating: "text-[var(--sr-link)] border-[var(--sr-link)]/40",
    resolved: "text-[var(--sr-status-success)] border-[var(--sr-status-success)]/40",
  };
  return (
    <span className={`admin-mono-font text-[9px] uppercase tracking-[0.12em] px-1.5 py-0.5 border rounded-[3px] ${map[status]}`}>
      {status}
    </span>
  );
}

function OverviewCard({
  label,
  value,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  tone?: "bad" | "warn" | "ok" | "neutral";
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
    <div className="border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] px-4 py-3">
      <div className={`heading-display text-2xl ${colour}`}>{value}</div>
      <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mt-1">
        {label}
      </div>
    </div>
  );
}

/* ── Incident row (with workflow buttons) ──────────────────────────────── */

function IncidentRow({
  incident,
  token,
  onChanged,
}: {
  incident: Incident;
  token: string;
  onChanged: () => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState("");
  const [err, setErr] = useState<string | null>(null);

  const act = async (action: string, body: Record<string, unknown>) => {
    setBusy(true);
    setErr(null);
    try {
      await runIncidentAction(
        token,
        incident.incident_id,
        action as "acknowledge" | "mitigate" | "resolve" | "notes",
        body,
      );
      onChanged();
    } catch (e) {
      setErr(errMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const actor = incident.owner?.handle || "admin";

  return (
    <div className="border border-[var(--sr-border-subtle)] rounded-[4px] bg-[var(--sr-surface-card)]">
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[var(--sr-surface-interactive)]/40 transition-colors"
      >
        {open ? <ChevronDownIcon size={14} /> : <ChevronRightIcon size={14} />}
        <SeverityPill severity={incident.severity} />
        <StatusPill status={incident.status} />
        <span className="text-[13px] text-[var(--sr-text-primary)] flex-1 truncate">
          {incident.title}
        </span>
        <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
          {incident.owner?.handle ?? "unassigned"}
        </span>
        <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
          {fmtDateTime(incident.detected_at)}
        </span>
      </button>

      {open && (
        <div className="border-t border-[var(--sr-border-subtle)] px-4 py-3 space-y-3">
          <p className="text-[12px] text-[var(--sr-text-secondary)]">
            {incident.summary || incident.title}
          </p>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 text-[12px]">
            <div>
              <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-1">
                Owner (must ack)
              </div>
              <div className="text-[var(--sr-text-primary)]">
                {incident.owner?.handle}{" "}
                <span className="text-[var(--sr-text-tertiary)]">
                  — {incident.owner?.role}; escalates to {incident.owner?.escalation}
                </span>
              </div>
            </div>
            <div>
              <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-1">
                Affected consumers
              </div>
              <div className="text-[var(--sr-text-primary)]">
                {incident.affected_consumers.length
                  ? incident.affected_consumers.join(", ")
                  : "—"}
              </div>
            </div>
            {incident.affected_batches.length > 0 && (
              <div className="sm:col-span-2">
                <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-1">
                  Affected batches
                </div>
                <div className="admin-mono-font text-[11px] text-[var(--sr-text-primary)]">
                  {incident.affected_batches.join(", ")}
                </div>
              </div>
            )}
          </div>

          {incident.recommended_action?.summary && (
            <div className="border-l-2 border-[var(--sr-link)]/50 pl-3 py-1">
              <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-link)] mb-0.5">
                Recommended {incident.recommended_action.kind ?? "action"}
                {incident.recommended_action.policy
                  ? ` · ${incident.recommended_action.policy}`
                  : ""}
              </div>
              <p className="text-[12px] text-[var(--sr-text-secondary)]">
                {incident.recommended_action.summary}
              </p>
            </div>
          )}

          {incident.notes.length > 0 && (
            <div>
              <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-1">
                Workflow log
              </div>
              <ul className="space-y-1">
                {incident.notes.map((n, i) => (
                  <li key={i} className="text-[12px] text-[var(--sr-text-secondary)]">
                    <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
                      {fmtDateTime(n.at)} · {n.by}
                    </span>{" "}
                    — {n.note}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {incident.status !== "resolved" && (
            <div className="flex items-center gap-2 pt-1 flex-wrap">
              <input
                value={note}
                onChange={(e) => setNote(e.target.value)}
                placeholder="Workflow note…"
                className="flex-1 min-w-[200px] h-9 px-3 bg-[var(--sr-surface-card)] border border-[var(--sr-border-subtle)] text-[12px] text-[var(--sr-text-primary)] rounded-[3px] outline-none focus:border-[var(--sr-link)]"
              />
              {incident.status === "open" && (
                <button
                  disabled={busy}
                  onClick={() =>
                    act("acknowledge", { actor, note: note || undefined })
                  }
                  className="h-9 px-3 admin-mono-font text-[10px] uppercase tracking-[0.12em] border border-[var(--sr-status-warning)]/50 text-[var(--sr-status-warning)] rounded-[3px] hover:bg-[var(--sr-status-warning)]/10 disabled:opacity-40"
                >
                  Acknowledge
                </button>
              )}
              {(incident.status === "open" || incident.status === "acknowledged") && (
                <button
                  disabled={busy}
                  onClick={() =>
                    act("mitigate", { actor, note: note || undefined })
                  }
                  className="h-9 px-3 admin-mono-font text-[10px] uppercase tracking-[0.12em] border border-[var(--sr-link)]/50 text-[var(--sr-link)] rounded-[3px] hover:bg-[var(--sr-link)]/10 disabled:opacity-40"
                >
                  Start mitigation
                </button>
              )}
              <button
                disabled={busy || !note.trim()}
                onClick={() =>
                  act("resolve", { actor, resolution: note.trim() })
                }
                className="h-9 px-3 admin-mono-font text-[10px] uppercase tracking-[0.12em] border border-[var(--sr-status-success)]/50 text-[var(--sr-status-success)] rounded-[3px] hover:bg-[var(--sr-status-success)]/10 disabled:opacity-40"
              >
                Resolve
              </button>
            </div>
          )}
          {err && (
            <p className="text-[12px] text-[var(--sr-action-pressed)]">{err}</p>
          )}
        </div>
      )}
    </div>
  );
}

/* ── AD-01-15 · Completeness meter ─────────────────────────────────────── */

function CompletenessMeter({
  label,
  value,
}: {
  label: string;
  value: number | null;
}) {
  const pct = value == null ? 0 : Math.min(100, Math.max(0, value));
  const low = value != null && value < COMPLETENESS_WARNING_THRESHOLD;
  const barColour = low ? "bg-[var(--sr-buoy)]" : "bg-[var(--sr-status-success)]";
  const textColour = low ? "text-[var(--sr-buoy)]" : "text-[var(--sr-text-primary)]";
  return (
    <div data-testid="completeness-meter" className="space-y-1">
      <div className="flex items-center justify-between text-[11px]">
        <span className="text-[var(--sr-text-secondary)] admin-mono-font">
          {label}
        </span>
        <span className={`admin-mono-font ${textColour}`}>
          {value == null ? "—" : `${value.toFixed(1)}%`}
        </span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-[var(--sr-surface-interactive)]/40 overflow-hidden">
        <div
          className={`h-full rounded-full ${barColour}`}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

/* ── AD-01-15 · Completeness section (GET /completeness) ───────────────── */

function CompletenessSection({
  metrics,
  error,
}: {
  metrics: CompletenessMetric[] | null;
  error: string | null;
}) {
  const byMetric = new Map((metrics ?? []).map((m) => [m.metric, m]));
  const boatMeters = BOATS_COMPLETENESS_COLUMNS.map((col) => ({
    col,
    metric: byMetric.get(`completeness.boats.${col}`) ?? null,
  }));
  const venue = byMetric.get(EVENTS_VENUE_NULL_METRIC) ?? null;
  const latestComputedAt =
    metrics && metrics.length ? metrics[0].computed_at : null;

  return (
    <section data-testid="completeness-section">
      <div className="flex items-center justify-between mb-3">
        <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] flex items-center gap-2">
          <ListChecksIcon size={12} /> Completeness
        </h2>
        <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
          {fmtComputedAgo(latestComputedAt)}
        </span>
      </div>
      <div className="border border-[var(--sr-border-subtle)] rounded-[4px] bg-[var(--sr-surface-card)] px-4 py-4">
        {error ? (
          <p className="text-[13px] text-[var(--sr-action-pressed)]">{error}</p>
        ) : metrics == null ? (
          <p className="text-[13px] text-[var(--sr-text-tertiary)]">Loading…</p>
        ) : (
          <div className="space-y-4">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-3">
              {boatMeters.map(({ col, metric }) => (
                <CompletenessMeter
                  key={col}
                  label={col}
                  value={metric?.value ?? null}
                />
              ))}
            </div>
            <div className="border-t border-[var(--sr-border-subtle)] pt-3">
              <div
                data-testid="completeness-venue-null"
                className="flex items-center justify-between text-[12px]"
              >
                <span className="text-[var(--sr-text-secondary)]">
                  events.venue — null rate
                </span>
                <span
                  className={`admin-mono-font ${
                    venue && venue.value != null && venue.value > 60
                      ? "text-[var(--sr-buoy)]"
                      : "text-[var(--sr-text-primary)]"
                  }`}
                >
                  {venue?.value == null ? "—" : `${venue.value.toFixed(1)}%`}
                </span>
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}

/* ── AD-01-15 · Tables census section (GET /tables) ─────────────────────── */

function TablesCensusSection({
  tables,
  error,
}: {
  tables: TableCensusRow[] | null;
  error: string | null;
}) {
  const latestComputedAt =
    tables && tables.length ? tables[0].computed_at : null;
  // Empty tables first (the API already sorts this way; re-sort
  // defensively so the UI invariant doesn't depend on it).
  const sorted = tables
    ? [...tables].sort((a, b) =>
        a.empty === b.empty ? a.name.localeCompare(b.name) : a.empty ? -1 : 1,
      )
    : null;
  const emptyCount = sorted ? sorted.filter((t) => t.empty).length : 0;

  return (
    <section data-testid="tables-census-section">
      <div className="flex items-center justify-between mb-3">
        <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] flex items-center gap-2">
          <DatabaseIcon size={12} /> Tables census
          {sorted && (
            <span className="text-[var(--sr-text-tertiary)] normal-case tracking-normal">
              ({emptyCount} empty / {sorted.length})
            </span>
          )}
        </h2>
        <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
          {fmtComputedAgo(latestComputedAt)}
        </span>
      </div>
      <div className="border border-[var(--sr-border-subtle)] rounded-[4px] overflow-x-auto bg-[var(--sr-surface-card)]">
        {error ? (
          <p className="px-4 py-6 text-[13px] text-[var(--sr-action-pressed)]">
            {error}
          </p>
        ) : sorted == null ? (
          <p className="px-4 py-6 text-[13px] text-[var(--sr-text-tertiary)]">
            Loading…
          </p>
        ) : (
          <table className="w-full text-[12px]">
            <thead>
              <tr className="text-left admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-label)] border-b border-[var(--sr-border-subtle)]">
                <th className="px-3 py-2">Table</th>
                <th className="px-3 py-2">Rows</th>
                <th className="px-3 py-2">Size</th>
                <th className="px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {sorted.length ? (
                sorted.map((t) => (
                  <tr
                    key={t.name}
                    data-testid="table-census-row"
                    data-table-empty={t.empty ? "true" : "false"}
                    className="border-b border-[var(--sr-border-subtle)]/60 last:border-0"
                  >
                    <td className="px-3 py-2 text-[var(--sr-text-primary)] font-medium">
                      {t.name}
                    </td>
                    <td className="px-3 py-2 admin-mono-font text-[11px] text-[var(--sr-text-primary)]">
                      {fmtRows(t.rows)}
                    </td>
                    <td className="px-3 py-2 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                      {fmtBytes(t.bytes)}
                    </td>
                    <td className="px-3 py-2">
                      {t.empty ? (
                        <span className="inline-flex items-center gap-1 text-[var(--sr-buoy)]">
                          <AlertTriangleIcon size={11} /> empty
                        </span>
                      ) : (
                        <span className="text-[var(--sr-text-tertiary)]">—</span>
                      )}
                    </td>
                  </tr>
                ))
              ) : (
                <tr>
                  <td
                    colSpan={4}
                    className="px-3 py-6 text-center text-[var(--sr-text-tertiary)]"
                  >
                    No table census yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>
    </section>
  );
}

/* ── Page ──────────────────────────────────────────────────────────────── */

export default function DataHealthPage() {
  const [token, setToken] = useState<string | null>(null);
  const [pwInput, setPwInput] = useState("");
  const [data, setData] = useState<Dashboard | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // AD-01-15 / SPEC-22 §3.4 — completeness + table-census snapshot, fed
  // by the nightly admin_metrics job (irc-data compute-admin-metrics).
  const [completeness, setCompleteness] = useState<
    CompletenessMetric[] | null
  >(null);
  const [completenessError, setCompletenessError] = useState<string | null>(
    null,
  );
  const [tables, setTables] = useState<TableCensusRow[] | null>(null);
  const [tablesError, setTablesError] = useState<string | null>(null);

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

  const dropSessionIfExpired = useCallback((e: unknown) => {
    if (e instanceof DataHealthApiError && e.status === 401) {
      localStorage.removeItem("admin_token");
      setToken(null);
      return true;
    }
    return false;
  }, []);

  const fetchDashboard = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      setData(await fetchDataHealthDashboard<Dashboard>(token));
    } catch (e) {
      if (!dropSessionIfExpired(e)) setError(errMessage(e));
    } finally {
      setLoading(false);
    }
  }, [token, dropSessionIfExpired]);

  const fetchCompleteness = useCallback(async () => {
    if (!token) return;
    setCompletenessError(null);
    try {
      const body = await fetchDataHealthCompleteness(token);
      setCompleteness(body.metrics);
    } catch (e) {
      if (!dropSessionIfExpired(e)) setCompletenessError(errMessage(e));
    }
  }, [token, dropSessionIfExpired]);

  const fetchTables = useCallback(async () => {
    if (!token) return;
    setTablesError(null);
    try {
      const body = await fetchDataHealthTables(token);
      setTables(body.tables);
    } catch (e) {
      if (!dropSessionIfExpired(e)) setTablesError(errMessage(e));
    }
  }, [token, dropSessionIfExpired]);

  const refreshAll = useCallback(() => {
    fetchDashboard();
    fetchCompleteness();
    fetchTables();
  }, [fetchDashboard, fetchCompleteness, fetchTables]);

  useEffect(() => {
    refreshAll();
    if (!token) return;
    const id = setInterval(refreshAll, 60000);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

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
            Data health
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

  const ov = data?.overview;

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--sr-surface-page)]">
      <div className="max-w-6xl mx-auto px-6 py-8 space-y-8">
        {/* Header */}
        <div className="flex items-end justify-between gap-6 flex-wrap">
          <div>
            <h1 className="heading-display text-2xl text-[var(--sr-text-primary)]">
              Data health
            </h1>
            <p className="text-[13px] text-[var(--sr-text-tertiary)] mt-1">
              Source freshness, pipeline yields, quarantine, lineage gaps,
              identity uncertainty and SLO breaches — with owned incidents.
              Auto-refreshes every minute.
            </p>
          </div>
          <div className="flex items-center gap-4 text-[var(--sr-text-label)]">
            {data?.as_of && (
              <span className="admin-mono-font text-[10px] uppercase tracking-[0.16em]">
                As of {fmtDateTime(data.as_of)}
              </span>
            )}
            <button
              onClick={refreshAll}
              disabled={loading}
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)] transition-colors disabled:opacity-40"
            >
              <RefreshIcon size={12} strokeWidth={2} className={loading ? "animate-spin" : ""} />
              Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="border border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/5 px-4 py-3 text-[13px] text-[var(--sr-action-pressed)] rounded-[4px]">
            {error}
          </div>
        )}

        {/* Overview cards */}
        {ov && (
          <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
            <OverviewCard
              label="Open incidents"
              value={ov.open_data_incidents}
              tone={ov.open_data_incidents ? "bad" : "ok"}
            />
            <OverviewCard
              label="Unacknowledged"
              value={ov.unacknowledged_data_incidents}
              tone={ov.unacknowledged_data_incidents ? "bad" : "ok"}
            />
            <OverviewCard
              label="SLO breaches"
              value={ov.slo_breaches}
              tone={ov.slo_breaches ? "warn" : "ok"}
            />
            <OverviewCard
              label="Quarantined sources"
              value={ov.sources_quarantined}
              tone={ov.sources_quarantined ? "warn" : "ok"}
            />
            <OverviewCard
              label="Stale sources"
              value={`${ov.sources_stale}/${ov.sources_tracked}`}
              tone={ov.sources_stale ? "warn" : "ok"}
            />
            <OverviewCard
              label="Lineage gaps (7d)"
              value={ov.lineage_gap_runs_in_window}
              tone={ov.lineage_gap_runs_in_window ? "warn" : "ok"}
            />
          </div>
        )}

        {/* Active incidents */}
        <section>
          <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-3 flex items-center gap-2">
            <ShieldAlertIcon size={12} /> Active incidents — owned recovery work
          </h2>
          <div className="space-y-2">
            {data?.incidents?.length ? (
              data.incidents.map((inc) => (
                <IncidentRow
                  key={inc.incident_id}
                  incident={inc}
                  token={token}
                  onChanged={fetchDashboard}
                />
              ))
            ) : (
              <p className="text-[13px] text-[var(--sr-text-tertiary)] border border-dashed border-[var(--sr-border-subtle)] rounded-[4px] px-4 py-6 text-center">
                No active incidents — every quality signal is inside policy.
              </p>
            )}
          </div>
        </section>

        {/* Per-source table */}
        <section>
          <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-3 flex items-center gap-2">
            <DatabaseIcon size={12} /> Sources
          </h2>
          <div className="border border-[var(--sr-border-subtle)] rounded-[4px] overflow-x-auto bg-[var(--sr-surface-card)]">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-left admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-label)] border-b border-[var(--sr-border-subtle)]">
                  <th className="px-3 py-2">Source</th>
                  <th className="px-3 py-2">Dataset</th>
                  <th className="px-3 py-2">Freshness</th>
                  <th className="px-3 py-2">Latest yield</th>
                  <th className="px-3 py-2">Variance</th>
                  <th className="px-3 py-2">Quarantine</th>
                  <th className="px-3 py-2">Incidents</th>
                  <th className="px-3 py-2">Lineage gaps</th>
                </tr>
              </thead>
              <tbody>
                {data?.sources?.length ? (
                  data.sources.map((s) => (
                    <tr
                      key={s.source}
                      className="border-b border-[var(--sr-border-subtle)]/60 last:border-0"
                    >
                      <td className="px-3 py-2 text-[var(--sr-text-primary)] font-medium">
                        {s.source}
                      </td>
                      <td className="px-3 py-2 text-[var(--sr-text-secondary)]">
                        {s.dataset ?? "—"}
                      </td>
                      <td className="px-3 py-2">
                        {s.freshness ? (
                          s.freshness.stale ? (
                            <span className="inline-flex items-center gap-1 text-[var(--sr-status-warning)]">
                              <ClockIcon size={11} /> stale (
                              {fmtAge(s.freshness.seconds_since_last_success)})
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 text-[var(--sr-status-success)]">
                              <CheckCircleIcon size={11} /> fresh (
                              {fmtAge(s.freshness.seconds_since_last_success)})
                            </span>
                          )
                        ) : (
                          <span className="text-[var(--sr-text-tertiary)]">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2 admin-mono-font text-[11px]">
                        {s.latest_yield ? (
                          <span
                            className={
                              s.latest_yield.decision === "block"
                                ? "text-[var(--sr-action-pressed)]"
                                : "text-[var(--sr-text-primary)]"
                            }
                          >
                            {fmtPct(s.latest_yield.yield_ratio)}
                            {s.latest_yield.decision === "block" ? " · block" : ""}
                          </span>
                        ) : (
                          <span className="text-[var(--sr-text-tertiary)]">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2 admin-mono-font text-[11px]">
                        {s.latest_yield ? (
                          <span
                            className={
                              s.latest_yield.variance > 0
                                ? "text-[var(--sr-action-pressed)]"
                                : "text-[var(--sr-text-primary)]"
                            }
                          >
                            {s.latest_yield.variance}
                          </span>
                        ) : (
                          "—"
                        )}
                      </td>
                      <td className="px-3 py-2">
                        {s.active_quarantine ? (
                          <span className="inline-flex items-center gap-1 text-[var(--sr-status-warning)]">
                            <AlertTriangleIcon size={11} /> quarantined
                          </span>
                        ) : (
                          <span className="text-[var(--sr-text-tertiary)]">—</span>
                        )}
                      </td>
                      <td className="px-3 py-2 admin-mono-font text-[11px] text-[var(--sr-text-primary)]">
                        {s.open_data_incidents + s.open_source_incidents || "—"}
                      </td>
                      <td className="px-3 py-2 admin-mono-font text-[11px] text-[var(--sr-text-primary)]">
                        {s.lineage_gap_runs || "—"}
                      </td>
                    </tr>
                  ))
                ) : (
                  <tr>
                    <td
                      colSpan={8}
                      className="px-3 py-6 text-center text-[var(--sr-text-tertiary)]"
                    >
                      No source signals yet.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* AD-01-15 / SPEC-22 §3.4 — completeness + table census, fed by
            the nightly admin_metrics snapshot (no on-request heavy query) */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <CompletenessSection metrics={completeness} error={completenessError} />
          <TablesCensusSection tables={tables} error={tablesError} />
        </div>

        {/* Identity uncertainty + lineage gaps side by side */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <section>
            <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-3 flex items-center gap-2">
              <UsersIcon size={12} /> Identity uncertainty
            </h2>
            <div className="border border-[var(--sr-border-subtle)] rounded-[4px] bg-[var(--sr-surface-card)] px-4 py-3 space-y-1 text-[12px]">
              <p className="text-[var(--sr-text-primary)]">
                <span className="heading-display text-lg">
                  {data?.identity_uncertainty?.awaiting_review_batches ?? 0}
                </span>{" "}
                batches awaiting identity review
              </p>
              <p className="text-[var(--sr-text-primary)]">
                <span className="heading-display text-lg">
                  {data?.identity_uncertainty?.quarantined_batches ?? 0}
                </span>{" "}
                identity batches quarantined
              </p>
              <p className="text-[var(--sr-text-tertiary)]">
                {data?.identity_uncertainty?.consumer_impact}
              </p>
            </div>
          </section>

          <section>
            <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-3 flex items-center gap-2">
              <GitBranchIcon size={12} /> Lineage gaps (unreconciled runs)
            </h2>
            <div className="border border-[var(--sr-border-subtle)] rounded-[4px] bg-[var(--sr-surface-card)] px-4 py-3">
              {data?.lineage_gaps?.runs?.length ? (
                <ul className="space-y-1 text-[12px]">
                  {data.lineage_gaps.runs.map((r) => (
                    <li key={r.run_id} className="text-[var(--sr-text-secondary)]">
                      <span className="admin-mono-font text-[11px] text-[var(--sr-text-primary)]">
                        run #{r.run_id} · {r.source}
                      </span>{" "}
                      — {fmtDateTime(r.started_at)} · {r.records_found ?? 0} found
                      / {r.records_new ?? 0} new, never reconciled
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-[12px] text-[var(--sr-text-tertiary)]">
                  Every completed run in the window has a reconciliation
                  report — no lineage gaps.
                </p>
              )}
            </div>
          </section>
        </div>
      </div>
    </div>
  );
}
