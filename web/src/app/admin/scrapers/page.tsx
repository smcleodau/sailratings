"use client";

import React, { useEffect, useState, useCallback } from "react";
import { CheckCircle2, AlertTriangle, Clock, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

type SignalState = "fresh" | "stale" | "never" | "n/a" | "optional";
type SourceState = SignalState | "uncatalogued";

interface ScraperRow {
  source: string;
  label: string;
  cadence: string;
  run_within_hours: number | null;
  data_within_hours: number | null;
  last_started: string | null;
  last_success: string | null;
  last_new_data: string | null;
  latest_event_date: string | null;
  run_age_seconds: number | null;
  data_age_seconds: number | null;
  run_state: SignalState;
  data_state: SignalState;
  state: SourceState;
  runs_7d: number;
  failed_7d: number;
  new_records_7d: number;
  optional: boolean;
}

interface ScraperRun {
  id: number;
  started_at: string | null;
  completed_at: string | null;
  duration_seconds: number | null;
  status: string | null;
  records_found: number | null;
  records_new: number | null;
  records_updated: number | null;
  error_message: string | null;
  metadata: Record<string, unknown> | null;
}

interface WatchdogAlert {
  id: number;
  alert_key: string;
  source: string;
  signal: string;
  label: string | null;
  cadence: string | null;
  reason: string | null;
  age_hours: number | null;
  budget_hours: number | null;
  status: string; // 'active' | 'recovered'
  first_seen_at: string | null;
  alerted_at: string | null;
  cooldown_until: string | null;
  recovered_at: string | null;
}

interface ScrapersResponse {
  as_of: string;
  sources: ScraperRow[];
  alerts_active: WatchdogAlert[];
  alerts_history: WatchdogAlert[];
}

function fmtAge(seconds: number | null): string {
  if (seconds == null) return "—";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${(seconds / 86400).toFixed(1)}d`;
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

function SignalPill({ label, state }: { label: string; state: SignalState }) {
  if (state === "n/a") {
    return (
      <span className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-secondary)]">
        {label}: —
      </span>
    );
  }
  if (state === "fresh") {
    return (
      <span className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-status-success)]">
        <CheckCircle2 size={11} strokeWidth={2} /> {label}: fresh
      </span>
    );
  }
  if (state === "stale" || state === "never") {
    return (
      <span className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-status-warning)]">
        <AlertTriangle size={11} strokeWidth={2} /> {label}: {state}
      </span>
    );
  }
  return (
    <span className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-tertiary)]">
      {label}: {state}
    </span>
  );
}

export default function ScrapersPage() {
  const [token, setToken] = useState<string | null>(null);
  const [pwInput, setPwInput] = useState("");
  const [data, setData] = useState<ScrapersResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, ScraperRun[]>>({});

  useEffect(() => {
    const t = localStorage.getItem("admin_token") || process.env.NEXT_PUBLIC_ADMIN_PASSWORD || "sailfast2026";
    if (t) {
      localStorage.setItem("admin_token", t);
      setToken(t);
    }
  }, []);

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/admin/login`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: pwInput }),
      });
      if (!res.ok) throw new Error("Invalid password");
      const json = await res.json();
      localStorage.setItem("admin_token", json.token);
      setToken(json.token);
      setPwInput("");
    } catch (err: any) {
      setError(err.message);
    }
  };

  const fetchSummary = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/admin/scrapers`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (res.status === 401) {
        localStorage.removeItem("admin_token");
        setToken(null);
        throw new Error("Session expired");
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const json = await res.json();
      setData(json);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchSummary();
    // Auto-refresh every 60s
    if (!token) return;
    const id = setInterval(fetchSummary, 60000);
    return () => clearInterval(id);
  }, [fetchSummary, token]);

  const handleRowClick = async (source: string) => {
    if (openRow === source) {
      setOpenRow(null);
      return;
    }
    setOpenRow(source);
    if (!runs[source] && token) {
      try {
        const res = await fetch(`${API_BASE}/admin/scrapers/${source}/runs?limit=25`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.ok) {
          const d = await res.json();
          setRuns((prev) => ({ ...prev, [source]: d.runs }));
        }
      } catch {
        // ignore — drawer just stays empty
      }
    }
  };

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
          <h1 className="heading-display text-2xl text-[var(--sr-text-primary)] text-center mb-6">Scrapers</h1>
          <input
            type="password"
            value={pwInput}
            onChange={(e) => setPwInput(e.target.value)}
            placeholder="Admin password"
            className="w-full h-12 px-4 bg-white border border-[var(--sr-link)]/25 text-[var(--sr-text-primary)] text-[13px] placeholder:text-[var(--sr-text-label)] focus:border-[var(--sr-link)] focus:ring-1 focus:ring-[var(--sr-link)]/20 outline-none transition-all rounded-[4px] shadow-sm"
          />
          <button type="submit" className="w-full h-12 bg-[var(--sr-link)] text-white text-[13px] font-medium hover:bg-[var(--sr-focus)] transition-colors rounded-[4px] shadow-sm">
            Sign in
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--sr-surface-page)]">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-end justify-between mb-8 gap-6 flex-wrap">
          <div>
            <h1 className="heading-display text-2xl text-[var(--sr-text-primary)]">Scrapers</h1>
            <p className="text-[13px] text-[var(--sr-text-tertiary)] mt-1">
              Health of every ingestion source. Auto-refreshes every minute.
            </p>
          </div>
          <div className="flex items-center gap-4 text-[var(--sr-text-label)]">
            {data?.as_of && (
              <span className="admin-mono-font text-[10px] uppercase tracking-[0.16em]">
                As of {fmtDateTime(data.as_of)}
              </span>
            )}
            <button
              onClick={fetchSummary}
              disabled={loading}
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)] transition-colors disabled:opacity-40"
            >
              <RefreshCw size={12} strokeWidth={2} className={loading ? "animate-spin" : ""} />
              Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="border border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/5 px-4 py-3 mb-6 text-[13px] text-[var(--sr-action-pressed)] rounded-[4px]">
            {error}
          </div>
        )}

        {data && (() => {
        const cronBreached = data.sources.filter(
          (s) => !s.optional && (s.run_state === "stale" || s.run_state === "never"),
        );
        const dataBreached = data.sources.filter(
          (s) => !s.optional && (s.data_state === "stale" || s.data_state === "never"),
        );
        if (cronBreached.length === 0 && dataBreached.length === 0) return null;
        return (
          <div className="mb-[18px]">
            {cronBreached.length > 0 && (
              <div className="border border-[rgba(166,124,31,0.4)] bg-[rgba(232,178,58,0.12)] rounded-[10px] p-[12px_16px] mb-[18px]">
                <div className="text-[13px] text-[var(--sr-text-primary)]">
                  <span className="text-[var(--sr-status-warning)] font-semibold">
                    Cron health: {cronBreached.length} source{cronBreached.length === 1 ? " is" : "s are"} not running.
                  </span>{" "}
                  {cronBreached.map((s) => s.label).join(", ")}.
                </div>
                <div className="text-[11px] text-[var(--sr-text-tertiary)] mt-[3px]">
                  Watchdog runs every 15 min and emails on breach. Cooldown 4 h per source.
                </div>
              </div>
            )}
            {dataBreached.length > 0 && (
              <div className="border border-[rgba(201,43,18,0.4)] bg-[rgba(201,43,18,0.12)] rounded-[10px] p-[12px_16px] mb-[18px]">
                <div className="text-[13px] text-[var(--sr-text-primary)]">
                  <span className="text-[var(--sr-action-pressed)] font-semibold">
                    Data tap: no new rows beyond seasonal lull for {dataBreached.length} source{dataBreached.length === 1 ? "" : "s"}.
                  </span>{" "}
                  {dataBreached.map((s) => s.label).join(", ")}.
                </div>
              </div>
            )}
          </div>
        );
      })()}

      <div className="admin-table-container">
        <div className="admin-table-header grid grid-cols-[1.6fr_1fr_1fr_150px_40px] gap-[14px] admin-mono-font text-[9px] tracking-[0.16em] uppercase">
          <span>Source</span>
          <span>Last run</span>
          <span>Last new data</span>
          <span className="text-right">7-day runs / fail</span>
          <span></span>
        </div>

        {data?.sources.map((src) => {
          const open = openRow === src.source;
          return (
            <div key={src.source} className="border-b border-[var(--sr-link)]/12 last:border-b-0">
              <div
                onClick={() => handleRowClick(src.source)}
                className="grid grid-cols-[1.6fr_1fr_1fr_150px_40px] gap-[14px] p-[12px_16px] items-start cursor-pointer hover:bg-[var(--sr-surface-interactive)] transition-colors"
              >
                <div>
                  <div className="text-[13px] text-[var(--sr-text-primary)] font-medium">{src.label}</div>
                  <div className="admin-mono-font text-[10px] text-[var(--sr-text-label)] mt-[2px]">{src.source}</div>
                </div>

                <div>
                  <div className="admin-mono-font text-[11px] text-[var(--sr-text-primary)]">
                    {fmtAge(src.run_age_seconds)} <span className="text-[var(--sr-text-secondary)]">ago</span>
                  </div>
                  <div className="admin-mono-font text-[10px] text-[var(--sr-text-secondary)] mt-[2px]">
                    {fmtDateTime(src.last_started)}
                  </div>
                  <div className="mt-[5px]">
                    <SignalPill label="run" state={src.run_state} />
                  </div>
                </div>

                <div>
                  <div className="admin-mono-font text-[11px] text-[var(--sr-text-primary)]">
                    {src.last_new_data ? `${fmtAge(src.data_age_seconds)} ago` : "—"}
                  </div>
                  <div className="admin-mono-font text-[10px] text-[var(--sr-text-secondary)] mt-[2px]">
                    {src.latest_event_date
                        ? `latest race ${src.latest_event_date}`
                        : "no rows on file"}
                  </div>
                  <div className="mt-[5px]">
                    <SignalPill label="data" state={src.data_state} />
                  </div>
                </div>

                <div className="text-right">
                  <div className="admin-mono-font text-[11px] text-[var(--sr-text-primary)]">
                    {src.runs_7d} <span className="text-[var(--sr-text-secondary)]">/</span>{" "}
                    <span className={src.failed_7d > 0 ? "text-[var(--sr-action-pressed)]" : ""}>{src.failed_7d}</span>
                  </div>
                  <div className="admin-mono-font text-[10px] text-[var(--sr-status-success)] mt-[2px]">
                    {src.new_records_7d > 0 ? `+${src.new_records_7d} rec` : "0 rec"}
                  </div>
                </div>

                <div className="text-right admin-mono-font text-[11px] text-[var(--sr-text-secondary)] flex justify-end">
                  {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </div>
              </div>

              {open && (
                <div className="p-[4px_16px_16px_16px] bg-[var(--sr-surface-interactive)]">
                  <div className="admin-mono-font text-[9px] tracking-[0.16em] uppercase text-[var(--sr-text-label)] mb-[8px]">
                    Recent runs
                  </div>
                  {(runs[src.source] ?? []).length === 0 ? (
                    <div className="text-[13px] text-[var(--sr-text-tertiary)] italic py-2">
                      {runs[src.source] === undefined ? "Loading…" : "No runs on record."}
                    </div>
                  ) : (
                    <div className="grid grid-cols-[1.1fr_0.6fr_0.7fr_0.5fr_0.5fr_2fr] gap-x-[14px] gap-y-[6px] admin-mono-font text-[10px]">
                      <span className="text-[var(--sr-text-secondary)]">STARTED</span>
                      <span className="text-[var(--sr-text-secondary)] text-right">DURATION</span>
                      <span className="text-[var(--sr-text-secondary)] text-right">STATUS</span>
                      <span className="text-[var(--sr-text-secondary)] text-right">FOUND</span>
                      <span className="text-[var(--sr-text-secondary)] text-right">NEW</span>
                      <span className="text-[var(--sr-text-secondary)]">ERROR</span>
                      
                      {runs[src.source]!.map((r) => (
                        <React.Fragment key={r.id}>
                          <span className="text-[var(--sr-text-primary)] border-t border-[var(--sr-link)]/12 pt-[5px] tabular-nums">{fmtDateTime(r.started_at)}</span>
                          <span className="text-[var(--sr-text-tertiary)] text-right border-t border-[var(--sr-link)]/12 pt-[5px] tabular-nums">
                            {r.duration_seconds != null ? `${r.duration_seconds.toFixed(1)}s` : "—"}
                          </span>
                          <span className={`text-right border-t border-[var(--sr-link)]/12 pt-[5px] ${
                            r.status === "completed" ? "text-[var(--sr-status-success)]"
                            : r.status === "failed" ? "text-[var(--sr-action-pressed)]"
                            : "text-[var(--sr-status-warning)]"
                          }`}>
                            {r.status ?? "—"}
                          </span>
                          <span className="text-[var(--sr-text-tertiary)] text-right border-t border-[var(--sr-link)]/12 pt-[5px] tabular-nums">{r.records_found ?? "—"}</span>
                          <span className="text-[var(--sr-text-tertiary)] text-right border-t border-[var(--sr-link)]/12 pt-[5px] tabular-nums">{r.records_new ?? "—"}</span>
                          <span className="text-[var(--sr-action-pressed)] border-t border-[var(--sr-link)]/12 pt-[5px] truncate max-w-xs" title={r.error_message ?? undefined}>
                            {r.error_message ? r.error_message.slice(0, 80) : ""}
                          </span>
                        </React.Fragment>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
        {(!data || data.sources.length === 0) && (
          <div className="p-6 text-[13px] text-[var(--sr-text-tertiary)] italic flex items-center gap-2">
            {loading ? (
              <>
                <Clock size={14} className="animate-spin" />
                Loading sources…
              </>
            ) : (
              "No sources to show."
            )}
          </div>
        )}
      </div>

      {/* Watchdog alert log (OPS-01-04) — retained history of every
          alert raised by the 15-minute staleness watchdog. */}
      {data && (data.alerts_active?.length ?? 0) + (data.alerts_history?.length ?? 0) > 0 && (
        <div className="mt-10">
          <h2 className="heading-display text-lg text-[var(--sr-text-primary)] mb-1">
            Watchdog alert log
          </h2>
          <p className="text-[12px] text-[var(--sr-text-tertiary)] mb-4">
            Every alert raised by the staleness watchdog. Active alerts clear
            automatically when the source recovers.
          </p>
          <div className="admin-table-container">
            <div className="admin-table-header grid grid-cols-[1.6fr_1fr_1fr_1fr] gap-[14px] admin-mono-font text-[9px] tracking-[0.16em] uppercase">
              <span>Source</span>
              <span>Alerted</span>
              <span>Status</span>
              <span>Recovered</span>
            </div>
            {(data.alerts_history ?? []).map((a) => (
              <div
                key={a.id}
                className="grid grid-cols-[1.6fr_1fr_1fr_1fr] gap-[14px] p-[10px_16px] items-center border-b border-[var(--sr-link)]/12 last:border-b-0"
              >
                <div>
                  <div className="text-[13px] text-[var(--sr-text-primary)] font-medium">
                    {a.label ?? a.source}
                  </div>
                  <div className="admin-mono-font text-[10px] text-[var(--sr-text-label)] mt-[2px]">
                    {a.alert_key}
                  </div>
                </div>
                <div className="admin-mono-font text-[11px] text-[var(--sr-text-primary)]">
                  {fmtDateTime(a.alerted_at)}
                </div>
                <div>
                  {a.status === "active" ? (
                    <span className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-status-warning)]">
                      <AlertTriangle size={11} strokeWidth={2} /> active
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-status-success)]">
                      <CheckCircle2 size={11} strokeWidth={2} /> recovered
                    </span>
                  )}
                </div>
                <div className="admin-mono-font text-[11px] text-[var(--sr-text-primary)]">
                  {a.recovered_at ? fmtDateTime(a.recovered_at) : "—"}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      </div>
    </div>
  );
}
