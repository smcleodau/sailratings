"use client";

import { useEffect, useState, useCallback } from "react";
import {
  Check, X, ExternalLink, Loader2, Plus, AlertTriangle,
  ChevronDown, ChevronRight,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

type DiscoveryStatus = "pending" | "confirmed" | "rejected" | "ingested" | "failed";

interface Discovery {
  id: number;
  discovered_at: string;
  source_url: string;
  source_type: string;
  seed_url: string | null;
  scoring_platform: string | null;
  platform_ids: Record<string, unknown> | null;
  title: string | null;
  event_date: string | null;
  event_location: string | null;
  confidence: number | null;
  status: DiscoveryStatus;
  error_message: string | null;
  confirmed_at: string | null;
  ingested_at: string | null;
  notes: string | null;
}

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
  });
}

function PlatformBadge({ p }: { p: string | null }) {
  const colors: Record<string, string> = {
    sailsys: "bg-blue-500/15 text-blue-300 border-blue-400/30",
    topyacht: "bg-emerald-500/15 text-emerald-300 border-emerald-400/30",
    sailwave: "bg-amber-500/15 text-amber-300 border-amber-400/30",
    yachtscoring: "bg-purple-500/15 text-purple-300 border-purple-400/30",
    pdf: "bg-rose-500/15 text-rose-300 border-rose-400/30",
    none: "bg-white/5 text-white/40 border-white/10",
    unknown: "bg-white/5 text-white/40 border-white/10",
  };
  const klass = colors[p || "unknown"] ?? colors.unknown;
  return (
    <span className={`data-mono text-[10px] uppercase tracking-[0.14em] px-2 py-0.5 border ${klass}`}>
      {p || "unknown"}
    </span>
  );
}

function StatusBadge({ s }: { s: DiscoveryStatus }) {
  const map: Record<DiscoveryStatus, { label: string; cls: string }> = {
    pending: { label: "Pending", cls: "text-white/60" },
    confirmed: { label: "Confirmed", cls: "text-blue-300" },
    ingested: { label: "Ingested", cls: "text-emerald-400" },
    rejected: { label: "Rejected", cls: "text-white/35 italic" },
    failed: { label: "Failed", cls: "text-brass" },
  };
  const v = map[s] || { label: s, cls: "text-white/40" };
  return (
    <span className={`data-mono text-[10px] uppercase tracking-[0.14em] ${v.cls}`}>
      {v.label}
    </span>
  );
}

function Confidence({ v }: { v: number | null }) {
  if (v == null) return <span className="text-white/30">—</span>;
  const pct = Math.round(v * 100);
  const cls =
    v >= 0.85 ? "text-emerald-400"
    : v >= 0.6 ? "text-white/70"
    : "text-brass";
  return (
    <span className={`data-mono text-xs tabular-nums ${cls}`}>{pct}%</span>
  );
}

export default function DiscoveryPage() {
  const [token, setToken] = useState<string | null>(null);
  const [pw, setPw] = useState("");
  const [rows, setRows] = useState<Discovery[]>([]);
  const [loading, setLoading] = useState(false);
  const [statusFilter, setStatusFilter] = useState<DiscoveryStatus | "all">("pending");
  const [error, setError] = useState<string | null>(null);
  const [seedUrl, setSeedUrl] = useState("");
  const [seedMode, setSeedMode] = useState<"map" | "single">("map");
  const [seedLimit, setSeedLimit] = useState(20);
  const [seeding, setSeeding] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [busy, setBusy] = useState<number | null>(null);

  useEffect(() => {
    const t = localStorage.getItem("admin_token");
    if (t) setToken(t);
  }, []);

  const fetchRows = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const q = statusFilter === "all" ? "" : `?status=${statusFilter}`;
      const res = await fetch(`${API_BASE}/admin/discovery${q}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        if (res.status === 401) {
          localStorage.removeItem("admin_token");
          setToken(null);
          throw new Error("Session expired.");
        }
        throw new Error(`Failed: ${res.status}`);
      }
      const data = await res.json();
      setRows(data.discoveries || []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setLoading(false);
    }
  }, [token, statusFilter]);

  useEffect(() => {
    fetchRows();
  }, [fetchRows]);

  const handleSeed = async () => {
    if (!token || !seedUrl.trim()) return;
    setSeeding(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/admin/discovery/seed`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({
          url: seedUrl.trim(),
          single: seedMode === "single",
          limit: seedLimit,
        }),
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`Seed failed (${res.status}): ${txt.slice(0, 200)}`);
      }
      const d = await res.json();
      setSeedUrl("");
      await fetchRows();
      if (d.processed != null) {
        setError(`Processed ${d.processed} URL${d.processed === 1 ? "" : "s"}.`);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Seed failed");
    } finally {
      setSeeding(false);
    }
  };

  const handleAction = async (id: number, action: "confirm" | "reject") => {
    if (!token) return;
    setBusy(id);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/admin/discovery/${id}/${action}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        const txt = await res.text().catch(() => "");
        throw new Error(`${action} failed (${res.status}): ${txt.slice(0, 200)}`);
      }
      await fetchRows();
    } catch (e) {
      setError(e instanceof Error ? e.message : `${action} failed`);
    } finally {
      setBusy(null);
    }
  };

  if (!token) {
    return (
      <div className="flex-1 flex items-center justify-center px-6">
        <form
          className="w-full max-w-sm space-y-4"
          onSubmit={(e) => {
            e.preventDefault();
            if (pw.trim()) {
              localStorage.setItem("admin_token", pw.trim());
              setToken(pw.trim());
            }
          }}
        >
          <h1 className="heading-display text-2xl text-white/90 text-center">Discovery</h1>
          <input
            type="password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            placeholder="Admin password"
            className="w-full h-12 px-4 bg-navy-light border border-white/10 text-white body-text"
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
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="heading-display text-2xl text-white/90">Event Discovery</h1>
          <p className="body-text text-sm text-white/40 mt-1">
            Crawler finds sailing-event URLs, Claude identifies the scoring platform. Confirm to ingest.
          </p>
        </div>

        {/* Seed form */}
        <div className="border border-white/10 bg-white/[0.02] rounded-sm p-4 mb-6">
          <div className="flex items-baseline gap-3 mb-3">
            <Plus size={14} className="text-brass" />
            <span className="data-mono text-[11px] uppercase tracking-[0.16em] text-white/60">
              Crawl a URL
            </span>
          </div>
          <div className="flex flex-col sm:flex-row gap-3 items-stretch sm:items-center">
            <input
              type="url"
              value={seedUrl}
              onChange={(e) => setSeedUrl(e.target.value)}
              placeholder="https://www.brisbanetogladstone.com/2026-race-results/"
              className="flex-1 h-10 px-3 bg-navy-light border border-white/10 text-white body-text text-sm focus:border-brass/60 focus:outline-none"
            />
            <select
              value={seedMode}
              onChange={(e) => setSeedMode(e.target.value as "map" | "single")}
              className="h-10 px-3 bg-navy-light border border-white/10 text-white body-text text-sm"
            >
              <option value="single">Single page</option>
              <option value="map">Map + crawl subpages</option>
            </select>
            {seedMode === "map" && (
              <input
                type="number"
                value={seedLimit}
                min={1}
                max={100}
                onChange={(e) => setSeedLimit(parseInt(e.target.value) || 20)}
                className="w-20 h-10 px-3 bg-navy-light border border-white/10 text-white body-text text-sm"
                title="Max subpages to process"
              />
            )}
            <button
              onClick={handleSeed}
              disabled={seeding || !seedUrl.trim()}
              className="h-10 px-4 bg-brass text-navy body-text text-sm font-semibold uppercase tracking-[0.08em] disabled:opacity-40"
            >
              {seeding ? (
                <span className="inline-flex items-center gap-2">
                  <Loader2 size={14} className="animate-spin" />
                  Crawling
                </span>
              ) : "Crawl"}
            </button>
          </div>
        </div>

        {error && (
          <div className="border border-brass/40 bg-brass/5 px-4 py-3 mb-6 body-text text-sm text-brass">
            {error}
          </div>
        )}

        {/* Filter pills */}
        <div className="flex items-center gap-2 mb-4">
          {(["pending", "confirmed", "ingested", "failed", "rejected", "all"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`data-mono text-[10px] uppercase tracking-[0.14em] px-3 py-1.5 border ${
                statusFilter === s
                  ? "border-brass text-brass bg-brass/10"
                  : "border-white/10 text-white/40 hover:text-white/70"
              }`}
            >
              {s}
            </button>
          ))}
          <span className="data-mono text-[10px] text-white/30 ml-auto">
            {rows.length} row{rows.length === 1 ? "" : "s"}
          </span>
        </div>

        {/* Table */}
        <div className="border border-white/10 rounded-sm overflow-hidden">
          <div className="grid grid-cols-[1fr_110px_90px_120px_180px] gap-4 px-4 py-3 bg-white/[0.03] border-b border-white/10 data-mono text-[10px] uppercase tracking-[0.16em] text-white/35">
            <span>Title / URL</span>
            <span>Platform</span>
            <span className="text-right">Confidence</span>
            <span>Status</span>
            <span className="text-right">Actions</span>
          </div>

          {loading && (
            <div className="px-4 py-6 flex items-center gap-2 text-white/40 body-text text-sm">
              <Loader2 size={14} className="animate-spin" />
              Loading
            </div>
          )}

          {!loading && rows.length === 0 && (
            <div className="px-4 py-8 body-text text-sm text-white/40 italic text-center">
              No discoveries for this filter.
              {statusFilter === "pending" && " Paste a URL above to start."}
            </div>
          )}

          {rows.map((d) => {
            const open = expanded === d.id;
            return (
              <div key={d.id} className="border-b border-white/5 last:border-b-0">
                <div
                  className="grid grid-cols-[1fr_110px_90px_120px_180px] gap-4 px-4 py-3 items-center cursor-pointer hover:bg-white/[0.02]"
                  onClick={() => setExpanded(open ? null : d.id)}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      {open ? <ChevronDown size={12} className="text-white/30" />
                            : <ChevronRight size={12} className="text-white/30" />}
                      <p className="body-text text-sm text-white/85 truncate">
                        {d.title || d.source_url}
                      </p>
                    </div>
                    <p className="data-mono text-[10px] text-white/35 mt-0.5 truncate ml-5">
                      {d.event_date || ""}{d.event_location ? " · " + d.event_location : ""}
                      {!d.title ? "" : "  "}
                      <a
                        href={d.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="hover:text-white/60 inline-flex items-center gap-1"
                      >
                        {d.source_url.replace(/^https?:\/\//, "").slice(0, 60)}
                        <ExternalLink size={10} />
                      </a>
                    </p>
                  </div>
                  <div>
                    <PlatformBadge p={d.scoring_platform} />
                  </div>
                  <div className="text-right">
                    <Confidence v={d.confidence} />
                  </div>
                  <div>
                    <StatusBadge s={d.status} />
                  </div>
                  <div className="flex items-center justify-end gap-2">
                    {(d.status === "pending" || d.status === "failed") && (
                      <>
                        <button
                          onClick={(e) => { e.stopPropagation(); handleAction(d.id, "confirm"); }}
                          disabled={busy === d.id}
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs bg-brass/15 text-brass border border-brass/30 hover:bg-brass/25 disabled:opacity-30"
                          title="Confirm + ingest"
                        >
                          {busy === d.id
                            ? <Loader2 size={12} className="animate-spin" />
                            : <Check size={12} strokeWidth={2.5} />}
                          Confirm
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); handleAction(d.id, "reject"); }}
                          disabled={busy === d.id}
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs text-white/40 border border-white/10 hover:text-white/70 disabled:opacity-30"
                        >
                          <X size={12} strokeWidth={2.5} />
                          Reject
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {open && (
                  <div className="px-4 pb-4 pt-1 ml-5 bg-white/[0.015]">
                    <dl className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-1.5 data-mono text-[11px] text-white/60">
                      <dt className="text-white/35">discovered_at</dt>
                      <dd>{fmtDate(d.discovered_at)}</dd>
                      <dt className="text-white/35">source_type</dt>
                      <dd>{d.source_type}{d.seed_url ? `  (seed: ${d.seed_url})` : ""}</dd>
                      <dt className="text-white/35">platform_ids</dt>
                      <dd className="font-mono text-[11px] text-white/75 break-all">
                        {JSON.stringify(d.platform_ids || {}, null, 0)}
                      </dd>
                      {d.error_message && (
                        <>
                          <dt className="text-white/35">error</dt>
                          <dd className="text-brass/90">{d.error_message}</dd>
                        </>
                      )}
                      {d.notes && (
                        <>
                          <dt className="text-white/35">notes</dt>
                          <dd>{d.notes}</dd>
                        </>
                      )}
                      {d.confirmed_at && (
                        <>
                          <dt className="text-white/35">confirmed_at</dt>
                          <dd>{fmtDate(d.confirmed_at)}</dd>
                        </>
                      )}
                      {d.ingested_at && (
                        <>
                          <dt className="text-white/35">ingested_at</dt>
                          <dd className="text-emerald-400">{fmtDate(d.ingested_at)}</dd>
                        </>
                      )}
                    </dl>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <p className="data-mono text-[10px] uppercase tracking-[0.14em] text-white/25 mt-6">
          Crawler: Firecrawl  ·  Extractor: Claude Sonnet 4.5  ·  Confidence threshold for auto-ingest: 85%
        </p>
      </div>
    </div>
  );
}
