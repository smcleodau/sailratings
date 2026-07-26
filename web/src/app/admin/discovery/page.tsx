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
    sailsys: "bg-[#2B6CB0]/15 text-[#2B6CB0] border-[#2B6CB0]/30",
    topyacht: "bg-[#2E7D54]/15 text-[#2E7D54] border-[#2E7D54]/30",
    sailwave: "bg-[#8A6613]/15 text-[#8A6613] border-[#8A6613]/30",
    yachtscoring: "bg-[#6B46C1]/15 text-[#6B46C1] border-[#6B46C1]/30",
    pdf: "bg-[#C92B12]/15 text-[#C92B12] border-[#C92B12]/30",
    none: "bg-black/5 text-[#7E948F] border-[#0C5F5C]/12",
    unknown: "bg-black/5 text-[#7E948F] border-[#0C5F5C]/12",
  };
  const klass = colors[p || "unknown"] ?? colors.unknown;
  return (
    <span className={`admin-mono-font text-[9px] uppercase tracking-[0.14em] px-2 py-0.5 border rounded-[2px] ${klass}`}>
      {p || "unknown"}
    </span>
  );
}

function StatusBadge({ s }: { s: DiscoveryStatus }) {
  const map: Record<DiscoveryStatus, { label: string; cls: string }> = {
    pending: { label: "Pending", cls: "text-[#52655F]" },
    confirmed: { label: "Confirmed", cls: "text-[#2B6CB0]" },
    ingested: { label: "Ingested", cls: "text-[#2E7D54]" },
    rejected: { label: "Rejected", cls: "text-[#7E948F] italic" },
    failed: { label: "Failed", cls: "text-[#C92B12]" },
  };
  const v = map[s] || { label: s, cls: "text-[#7E948F]" };
  return (
    <span className={`admin-mono-font text-[9px] uppercase tracking-[0.14em] ${v.cls}`}>
      {v.label}
    </span>
  );
}

function Confidence({ v }: { v: number | null }) {
  if (v == null) return <span className="text-[#7E948F]">—</span>;
  const pct = Math.round(v * 100);
  const cls =
    v >= 0.85 ? "text-[#2E7D54]"
    : v >= 0.6 ? "text-[#52655F]"
    : "text-[#C92B12]";
  return (
    <span className={`admin-mono-font text-[11px] tabular-nums ${cls}`}>{pct}%</span>
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
      <div className="flex-1 flex items-center justify-center px-6 bg-[#F3F1EC]">
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
          <h1 className="heading-display text-2xl text-[#162423] text-center mb-6">Discovery</h1>
          <input
            type="password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
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
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="heading-display text-2xl text-[#162423]">Event Discovery</h1>
          <p className="text-[13px] text-[#52655F] mt-1">
            Crawler finds sailing-event URLs, Claude identifies the scoring platform. Confirm to ingest.
          </p>
        </div>

        {/* Seed form */}
        <div className="border border-[#0C5F5C]/12 bg-white rounded-[4px] shadow-sm p-4 mb-6">
          <div className="flex items-baseline gap-3 mb-3">
            <Plus size={14} className="text-[#0C5F5C]" />
            <span className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[#7E948F]">
              Crawl a URL
            </span>
          </div>
          <div className="flex flex-col sm:flex-row gap-3 items-stretch sm:items-center">
            <input
              type="url"
              value={seedUrl}
              onChange={(e) => setSeedUrl(e.target.value)}
              placeholder="https://www.brisbanetogladstone.com/2026-race-results/"
              className="flex-1 h-10 px-3 bg-[#F6F4EE] border border-[#0C5F5C]/25 text-[#162423] text-[13px] focus:border-[#0C5F5C] focus:ring-1 focus:ring-[#0C5F5C]/20 outline-none transition-all rounded-[4px]"
            />
            <select
              value={seedMode}
              onChange={(e) => setSeedMode(e.target.value as "map" | "single")}
              className="h-10 px-3 bg-[#F6F4EE] border border-[#0C5F5C]/25 text-[#162423] text-[13px] rounded-[4px] outline-none focus:border-[#0C5F5C]"
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
                className="w-20 h-10 px-3 bg-[#F6F4EE] border border-[#0C5F5C]/25 text-[#162423] text-[13px] rounded-[4px] outline-none focus:border-[#0C5F5C]"
                title="Max subpages to process"
              />
            )}
            <button
              onClick={handleSeed}
              disabled={seeding || !seedUrl.trim()}
              className="h-10 px-4 bg-[#0C5F5C] text-white text-[11px] font-medium uppercase tracking-[0.08em] hover:bg-[#3E9B95] transition-colors rounded-[4px] shadow-sm disabled:opacity-40"
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
          <div className="border border-[#C92B12]/40 bg-[#C92B12]/5 px-4 py-3 mb-6 text-[13px] text-[#C92B12] rounded-[4px]">
            {error}
          </div>
        )}

        {/* Filter pills */}
        <div className="flex items-center gap-2 mb-4">
          {(["pending", "confirmed", "ingested", "failed", "rejected", "all"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`admin-mono-font text-[9px] uppercase tracking-[0.14em] px-3 py-1.5 border rounded-[4px] transition-colors ${
                statusFilter === s
                  ? "border-[#0C5F5C] text-[#0C5F5C] bg-[#E6F0EE]"
                  : "border-[#0C5F5C]/12 text-[#7E948F] bg-white hover:text-[#162423]"
              }`}
            >
              {s}
            </button>
          ))}
          <span className="admin-mono-font text-[9px] text-[#7E948F] ml-auto">
            {rows.length} row{rows.length === 1 ? "" : "s"}
          </span>
        </div>

        {/* Table */}
        <div className="border border-[#0C5F5C]/12 bg-white rounded-[4px] shadow-sm overflow-hidden">
          <div className="grid grid-cols-[1fr_110px_90px_120px_180px] gap-4 px-4 py-3 bg-[#F6F4EE] border-b border-[#0C5F5C]/12 admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[#7E948F] font-medium">
            <span>Title / URL</span>
            <span>Platform</span>
            <span className="text-right">Confidence</span>
            <span>Status</span>
            <span className="text-right">Actions</span>
          </div>

          {loading && (
            <div className="px-4 py-6 flex items-center gap-2 text-[#7E948F] text-[13px]">
              <Loader2 size={14} className="animate-spin" />
              Loading
            </div>
          )}

          {!loading && rows.length === 0 && (
            <div className="px-4 py-8 text-[13px] text-[#7E948F] italic text-center">
              No discoveries for this filter.
              {statusFilter === "pending" && " Paste a URL above to start."}
            </div>
          )}

          {rows.map((d) => {
            const open = expanded === d.id;
            return (
              <div key={d.id} className="border-b border-[#0C5F5C]/12 last:border-b-0">
                <div
                  className="grid grid-cols-[1fr_110px_90px_120px_180px] gap-4 px-4 py-3 items-center cursor-pointer hover:bg-[#F6F4EE] transition-colors"
                  onClick={() => setExpanded(open ? null : d.id)}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      {open ? <ChevronDown size={12} className="text-[#7E948F]" />
                            : <ChevronRight size={12} className="text-[#7E948F]" />}
                      <p className="text-[13px] text-[#162423] truncate font-medium">
                        {d.title || d.source_url}
                      </p>
                    </div>
                    <p className="admin-mono-font text-[10px] text-[#7E948F] mt-0.5 truncate ml-5">
                      {d.event_date || ""}{d.event_location ? " · " + d.event_location : ""}
                      {!d.title ? "" : "  "}
                      <a
                        href={d.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="hover:text-[#0C5F5C] inline-flex items-center gap-1"
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
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs bg-[#0C5F5C] text-white hover:bg-[#3E9B95] disabled:opacity-30 rounded-[4px] shadow-sm transition-colors"
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
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs border border-[#C92B12]/40 text-[#C92B12] hover:bg-[#C92B12]/10 disabled:opacity-30 rounded-[4px] transition-colors"
                        >
                          <X size={12} strokeWidth={2.5} />
                          Reject
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {open && (
                  <div className="px-4 pb-4 pt-1 ml-5 bg-[#F6F4EE]/50">
                    <dl className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-1.5 admin-mono-font text-[10px] text-[#52655F]">
                      <dt className="text-[#7E948F]">discovered_at</dt>
                      <dd>{fmtDate(d.discovered_at)}</dd>
                      <dt className="text-[#7E948F]">source_type</dt>
                      <dd>{d.source_type}{d.seed_url ? `  (seed: ${d.seed_url})` : ""}</dd>
                      <dt className="text-[#7E948F]">platform_ids</dt>
                      <dd className="font-mono text-[10px] text-[#162423] break-all bg-white border border-[#0C5F5C]/12 px-2 py-1 rounded-[2px]">
                        {JSON.stringify(d.platform_ids || {}, null, 0)}
                      </dd>
                      {d.error_message && (
                        <>
                          <dt className="text-[#7E948F]">error</dt>
                          <dd className="text-[#C92B12] font-semibold">{d.error_message}</dd>
                        </>
                      )}
                      {d.notes && (
                        <>
                          <dt className="text-[#7E948F]">notes</dt>
                          <dd className="text-[#162423]">{d.notes}</dd>
                        </>
                      )}
                      {d.confirmed_at && (
                        <>
                          <dt className="text-[#7E948F]">confirmed_at</dt>
                          <dd>{fmtDate(d.confirmed_at)}</dd>
                        </>
                      )}
                      {d.ingested_at && (
                        <>
                          <dt className="text-[#7E948F]">ingested_at</dt>
                          <dd className="text-[#2E7D54]">{fmtDate(d.ingested_at)}</dd>
                        </>
                      )}
                    </dl>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <p className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[#7E948F] mt-6">
          Crawler: Firecrawl  ·  Extractor: Claude Sonnet 4.5  ·  Confidence threshold for auto-ingest: 85%
        </p>
      </div>
    </div>
  );
}
