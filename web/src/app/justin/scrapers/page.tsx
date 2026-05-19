"use client";

import { useEffect, useState, useCallback } from "react";
import { CheckCircle2, AlertTriangle, Clock, ChevronDown, ChevronRight, RefreshCw } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

type SourceState = "fresh" | "stale" | "never" | "optional" | "uncatalogued";

interface ScraperRow {
  source: string;
  label: string;
  cadence: string;
  expected_within_hours: number | null;
  last_started: string | null;
  last_success: string | null;
  age_seconds: number | null;
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

function StatePill({ state, optional }: { state: SourceState; optional: boolean }) {
  if (optional || state === "optional") {
    return <span className="data-mono text-[10px] uppercase tracking-[0.16em] text-white/35">Optional</span>;
  }
  if (state === "fresh") {
    return (
      <span className="inline-flex items-center gap-1.5 data-mono text-[10px] uppercase tracking-[0.16em] text-emerald-400/90">
        <CheckCircle2 size={12} strokeWidth={2} /> Fresh
      </span>
    );
  }
  if (state === "stale") {
    return (
      <span className="inline-flex items-center gap-1.5 data-mono text-[10px] uppercase tracking-[0.16em] text-brass">
        <AlertTriangle size={12} strokeWidth={2} /> Stale
      </span>
    );
  }
  if (state === "never") {
    return (
      <span className="inline-flex items-center gap-1.5 data-mono text-[10px] uppercase tracking-[0.16em] text-brass">
        <AlertTriangle size={12} strokeWidth={2} /> Never run
      </span>
    );
  }
  return (
    <span className="data-mono text-[10px] uppercase tracking-[0.16em] text-white/40">{state}</span>
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

  const fetchSummary = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/admin/scrapers`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        if (res.status === 401 || res.status === 403) {
          localStorage.removeItem("admin_token");
          setToken(null);
          throw new Error("Session expired. Sign in again.");
        }
        throw new Error(`Failed: ${res.status}`);
      }
      setData(await res.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load");
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
          <h1 className="heading-display text-2xl text-white/90 text-center">Scrapers</h1>
          <input
            type="password"
            value={pwInput}
            onChange={(e) => setPwInput(e.target.value)}
            placeholder="Admin password"
            className="w-full h-12 px-4 bg-navy-light border border-white/10 text-white body-text text-base focus:border-brass focus:outline-none"
          />
          <button type="submit" className="w-full h-12 bg-brass text-white body-text font-medium">
            Sign in
          </button>
        </form>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-end justify-between mb-8 gap-6 flex-wrap">
          <div>
            <h1 className="heading-display text-2xl text-white/90">Scrapers</h1>
            <p className="body-text text-sm text-white/40 mt-1">
              Health of every ingestion source. Auto-refreshes every minute.
            </p>
          </div>
          <div className="flex items-center gap-4 text-white/35">
            {data?.as_of && (
              <span className="data-mono text-[11px] uppercase tracking-[0.16em]">
                As of {fmtDateTime(data.as_of)}
              </span>
            )}
            <button
              onClick={fetchSummary}
              disabled={loading}
              className="inline-flex items-center gap-1.5 data-mono text-[11px] uppercase tracking-[0.16em] text-white/40 hover:text-white/70 transition-colors disabled:opacity-40"
            >
              <RefreshCw size={12} strokeWidth={2} className={loading ? "animate-spin" : ""} />
              Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="border border-brass/40 bg-brass/5 px-4 py-3 mb-6 body-text text-sm text-brass">
            {error}
          </div>
        )}

        {/* Stale banner */}
        {data && (() => {
          const stale = data.sources.filter((s) => s.state === "stale" || s.state === "never");
          if (stale.length === 0) return null;
          return (
            <div className="border border-brass/30 bg-brass/5 px-4 py-3 mb-6 flex items-start gap-3">
              <AlertTriangle size={16} className="text-brass flex-shrink-0 mt-0.5" />
              <div className="flex-1">
                <p className="body-text text-sm text-white/85">
                  <span className="text-brass font-medium">{stale.length} source{stale.length === 1 ? " is" : "s are"} stale.</span>{" "}
                  {stale.map((s) => s.label).join(", ")}.
                </p>
                <p className="body-text text-xs text-white/45 mt-1">
                  Watchdog runs every 15 min and emails on breach. Cooldown 4h per source.
                </p>
              </div>
            </div>
          );
        })()}

        {/* Sources table */}
        <div className="border border-white/10 rounded-sm overflow-hidden">
          <div className="grid grid-cols-[1fr_120px_140px_120px_60px] gap-4 px-4 py-3 bg-white/[0.03] border-b border-white/10 data-mono text-[10px] uppercase tracking-[0.16em] text-white/35">
            <span>Source</span>
            <span className="text-right">Last success</span>
            <span className="text-right">7-day runs / fail</span>
            <span className="text-right">State</span>
            <span></span>
          </div>

          {data?.sources.map((src) => {
            const open = openRow === src.source;
            return (
              <div key={src.source} className="border-b border-white/5 last:border-b-0">
                <button
                  onClick={() => handleRowClick(src.source)}
                  className="w-full grid grid-cols-[1fr_120px_140px_120px_60px] gap-4 px-4 py-3.5 items-center text-left hover:bg-white/[0.025] transition-colors"
                >
                  <div className="min-w-0">
                    <p className="body-text text-sm text-white/85 truncate">{src.label}</p>
                    <p className="data-mono text-[10px] text-white/35 mt-0.5">
                      {src.source} · {src.cadence}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="data-mono text-xs text-white/65 tabular-nums">
                      {fmtAge(src.age_seconds)}
                    </p>
                    <p className="data-mono text-[10px] text-white/30">
                      {fmtDateTime(src.last_success)}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="data-mono text-xs text-white/65 tabular-nums">
                      {src.runs_7d} <span className="text-white/30">/</span>{" "}
                      <span className={src.failed_7d > 0 ? "text-brass" : "text-white/30"}>
                        {src.failed_7d}
                      </span>
                    </p>
                    {src.new_records_7d > 0 && (
                      <p className="data-mono text-[10px] text-emerald-400/70">
                        +{src.new_records_7d.toLocaleString()} rows
                      </p>
                    )}
                  </div>
                  <div className="text-right">
                    <StatePill state={src.state} optional={src.optional} />
                  </div>
                  <div className="text-right text-white/30">
                    {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </div>
                </button>

                {open && (
                  <div className="px-4 pb-4 pt-1 bg-white/[0.015]">
                    <p className="data-mono text-[10px] uppercase tracking-[0.16em] text-white/35 mb-2">
                      Recent runs
                    </p>
                    {(runs[src.source] ?? []).length === 0 ? (
                      <p className="body-text text-xs text-white/35 italic py-3">
                        {runs[src.source] === undefined ? "Loading…" : "No runs on record."}
                      </p>
                    ) : (
                      <div className="overflow-x-auto">
                        <table className="w-full data-mono text-[11px]">
                          <thead>
                            <tr className="text-white/35">
                              <th className="text-left py-1.5 pr-3 font-normal">Started</th>
                              <th className="text-right py-1.5 pr-3 font-normal">Duration</th>
                              <th className="text-right py-1.5 pr-3 font-normal">Status</th>
                              <th className="text-right py-1.5 pr-3 font-normal">Found</th>
                              <th className="text-right py-1.5 pr-3 font-normal">New</th>
                              <th className="text-left py-1.5 font-normal">Error</th>
                            </tr>
                          </thead>
                          <tbody>
                            {runs[src.source]!.map((r) => (
                              <tr key={r.id} className="border-t border-white/5 text-white/70">
                                <td className="py-1.5 pr-3 tabular-nums">{fmtDateTime(r.started_at)}</td>
                                <td className="py-1.5 pr-3 tabular-nums text-right text-white/45">
                                  {r.duration_seconds != null ? `${r.duration_seconds.toFixed(1)}s` : "—"}
                                </td>
                                <td className="py-1.5 pr-3 text-right">
                                  <span className={
                                    r.status === "completed" ? "text-emerald-400/80"
                                    : r.status === "failed" ? "text-brass"
                                    : "text-white/40"
                                  }>
                                    {r.status ?? "—"}
                                  </span>
                                </td>
                                <td className="py-1.5 pr-3 text-right tabular-nums">{r.records_found ?? "—"}</td>
                                <td className="py-1.5 pr-3 text-right tabular-nums">{r.records_new ?? "—"}</td>
                                <td className="py-1.5 text-brass/80 truncate max-w-xs" title={r.error_message ?? undefined}>
                                  {r.error_message ? r.error_message.slice(0, 80) : ""}
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          {(!data || data.sources.length === 0) && (
            <div className="px-4 py-6 body-text text-sm text-white/40 italic flex items-center gap-2">
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
