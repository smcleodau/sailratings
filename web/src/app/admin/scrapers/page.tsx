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
      <span className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[#98A8A3]">
        {label}: —
      </span>
    );
  }
  if (state === "fresh") {
    return (
      <span className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[#2E7D54]">
        <CheckCircle2 size={11} strokeWidth={2} /> {label}: fresh
      </span>
    );
  }
  if (state === "stale" || state === "never") {
    return (
      <span className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[#8A6613]">
        <AlertTriangle size={11} strokeWidth={2} /> {label}: {state}
      </span>
    );
  }
  return (
    <span className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[#52655F]">
      {label}: {state}
    </span>
  );
}

export default function ScrapersPage() {
  const [token, setToken] = useState<string | null>(null);
  const [pwInput, setPwInput] = useState("");
  const [data, setData] = useState<{ as_of: string; sources: ScraperRow[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [openRow, setOpenRow] = useState<string | null>(null);
  const [runs, setRuns] = useState<Record<string, ScraperRun[]>>({});

  useEffect(() => {
    const t = localStorage.getItem("admin_token");
    if (t) setToken(t);
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
          <h1 className="heading-display text-2xl text-[#162423] text-center mb-6">Scrapers</h1>
          <input
            type="password"
            value={pwInput}
            onChange={(e) => setPwInput(e.target.value)}
            placeholder="Admin password"
            className="w-full h-12 px-4 bg-white border border-[#0C5F5C]/25 text-[#162423] text-[13px] placeholder:text-[#7E948F] focus:border-[#0C5F5C] focus:ring-1 focus:ring-[#0C5F5C]/20 outline-none transition-all rounded-[4px] shadow-sm"
          />
          <button type="submit" className="w-full h-12 bg-[#0C5F5C] text-white text-[13px] font-medium hover:bg-[#3E9B95] transition-colors rounded-[4px] shadow-sm">
            Sign in
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto bg-[#F3F1EC]">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-end justify-between mb-8 gap-6 flex-wrap">
          <div>
            <h1 className="heading-display text-2xl text-[#162423]">Scrapers</h1>
            <p className="text-[13px] text-[#52655F] mt-1">
              Health of every ingestion source. Auto-refreshes every minute.
            </p>
          </div>
          <div className="flex items-center gap-4 text-[#7E948F]">
            {data?.as_of && (
              <span className="admin-mono-font text-[10px] uppercase tracking-[0.16em]">
                As of {fmtDateTime(data.as_of)}
              </span>
            )}
            <button
              onClick={fetchSummary}
              disabled={loading}
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[#7E948F] hover:text-[#162423] transition-colors disabled:opacity-40"
            >
              <RefreshCw size={12} strokeWidth={2} className={loading ? "animate-spin" : ""} />
              Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="border border-[#C92B12]/40 bg-[#C92B12]/5 px-4 py-3 mb-6 text-[13px] text-[#C92B12] rounded-[4px]">
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
                <div className="text-[13px] text-[#162423]">
                  <span className="text-[#8A6613] font-semibold">
                    Cron health: {cronBreached.length} source{cronBreached.length === 1 ? " is" : "s are"} not running.
                  </span>{" "}
                  {cronBreached.map((s) => s.label).join(", ")}.
                </div>
                <div className="text-[11px] text-[#52655F] mt-[3px]">
                  Watchdog runs every 15 min and emails on breach. Cooldown 4 h per source.
                </div>
              </div>
            )}
            {dataBreached.length > 0 && (
              <div className="border border-[rgba(201,43,18,0.4)] bg-[rgba(201,43,18,0.12)] rounded-[10px] p-[12px_16px] mb-[18px]">
                <div className="text-[13px] text-[#162423]">
                  <span className="text-[#C92B12] font-semibold">
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
            <div key={src.source} className="border-b border-[#0C5F5C]/12 last:border-b-0">
              <div
                onClick={() => handleRowClick(src.source)}
                className="grid grid-cols-[1.6fr_1fr_1fr_150px_40px] gap-[14px] p-[12px_16px] items-start cursor-pointer hover:bg-[#F6F4EE] transition-colors"
              >
                <div>
                  <div className="text-[13px] text-[#162423] font-medium">{src.label}</div>
                  <div className="admin-mono-font text-[10px] text-[#7E948F] mt-[2px]">{src.source}</div>
                </div>

                <div>
                  <div className="admin-mono-font text-[11px] text-[#162423]">
                    {fmtAge(src.run_age_seconds)} <span className="text-[#98A8A3]">ago</span>
                  </div>
                  <div className="admin-mono-font text-[10px] text-[#98A8A3] mt-[2px]">
                    {fmtDateTime(src.last_started)}
                  </div>
                  <div className="mt-[5px]">
                    <SignalPill label="run" state={src.run_state} />
                  </div>
                </div>

                <div>
                  <div className="admin-mono-font text-[11px] text-[#162423]">
                    {src.last_new_data ? `${fmtAge(src.data_age_seconds)} ago` : "—"}
                  </div>
                  <div className="admin-mono-font text-[10px] text-[#98A8A3] mt-[2px]">
                    {src.latest_event_date
                        ? `latest race ${src.latest_event_date}`
                        : "no rows on file"}
                  </div>
                  <div className="mt-[5px]">
                    <SignalPill label="data" state={src.data_state} />
                  </div>
                </div>

                <div className="text-right">
                  <div className="admin-mono-font text-[11px] text-[#162423]">
                    {src.runs_7d} <span className="text-[#98A8A3]">/</span>{" "}
                    <span className={src.failed_7d > 0 ? "text-[#C92B12]" : ""}>{src.failed_7d}</span>
                  </div>
                  <div className="admin-mono-font text-[10px] text-[#2E7D54] mt-[2px]">
                    {src.new_records_7d > 0 ? `+${src.new_records_7d} rec` : "0 rec"}
                  </div>
                </div>

                <div className="text-right admin-mono-font text-[11px] text-[#98A8A3] flex justify-end">
                  {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                </div>
              </div>

              {open && (
                <div className="p-[4px_16px_16px_16px] bg-[#F6F4EE]">
                  <div className="admin-mono-font text-[9px] tracking-[0.16em] uppercase text-[#7E948F] mb-[8px]">
                    Recent runs
                  </div>
                  {(runs[src.source] ?? []).length === 0 ? (
                    <div className="text-[13px] text-[#52655F] italic py-2">
                      {runs[src.source] === undefined ? "Loading…" : "No runs on record."}
                    </div>
                  ) : (
                    <div className="grid grid-cols-[1.1fr_0.6fr_0.7fr_0.5fr_0.5fr_2fr] gap-x-[14px] gap-y-[6px] admin-mono-font text-[10px]">
                      <span className="text-[#98A8A3]">STARTED</span>
                      <span className="text-[#98A8A3] text-right">DURATION</span>
                      <span className="text-[#98A8A3] text-right">STATUS</span>
                      <span className="text-[#98A8A3] text-right">FOUND</span>
                      <span className="text-[#98A8A3] text-right">NEW</span>
                      <span className="text-[#98A8A3]">ERROR</span>
                      
                      {runs[src.source]!.map((r) => (
                        <React.Fragment key={r.id}>
                          <span className="text-[#162423] border-t border-[#0C5F5C]/12 pt-[5px] tabular-nums">{fmtDateTime(r.started_at)}</span>
                          <span className="text-[#52655F] text-right border-t border-[#0C5F5C]/12 pt-[5px] tabular-nums">
                            {r.duration_seconds != null ? `${r.duration_seconds.toFixed(1)}s` : "—"}
                          </span>
                          <span className={`text-right border-t border-[#0C5F5C]/12 pt-[5px] ${
                            r.status === "completed" ? "text-[#2E7D54]"
                            : r.status === "failed" ? "text-[#C92B12]"
                            : "text-[#8A6613]"
                          }`}>
                            {r.status ?? "—"}
                          </span>
                          <span className="text-[#52655F] text-right border-t border-[#0C5F5C]/12 pt-[5px] tabular-nums">{r.records_found ?? "—"}</span>
                          <span className="text-[#52655F] text-right border-t border-[#0C5F5C]/12 pt-[5px] tabular-nums">{r.records_new ?? "—"}</span>
                          <span className="text-[#C92B12] border-t border-[#0C5F5C]/12 pt-[5px] truncate max-w-xs" title={r.error_message ?? undefined}>
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
          <div className="p-6 text-[13px] text-[#52655F] italic flex items-center gap-2">
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
      </div>
    </div>
  );
}
