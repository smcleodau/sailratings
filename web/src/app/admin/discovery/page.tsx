"use client";

import { useEffect, useState, useCallback } from "react";
import {
  CheckIcon,
  XIcon,
  ExternalLinkIcon,
  SpinnerIcon,
  PlusIcon,
  ChevronDownIcon,
  ChevronRightIcon,
} from "@/components/admin/AdminIcons";

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
    sailsys: "bg-[var(--sr-status-info)]/15 text-[var(--sr-status-info)] border-[var(--sr-status-info)]/30",
    topyacht: "bg-[var(--sr-status-success)]/15 text-[var(--sr-status-success)] border-[var(--sr-status-success)]/30",
    sailwave: "bg-[var(--sr-status-warning)]/15 text-[var(--sr-status-warning)] border-[var(--sr-status-warning)]/30",
    yachtscoring: "bg-[var(--sr-status-comparison)]/15 text-[var(--sr-status-comparison)] border-[var(--sr-status-comparison)]/30",
    pdf: "bg-[var(--sr-status-danger)]/15 text-[var(--sr-status-danger)] border-[var(--sr-status-danger)]/30",
    none: "bg-[var(--sr-surface-interactive)] text-[var(--sr-text-label)] border-[var(--sr-border-subtle)]",
    unknown: "bg-[var(--sr-surface-interactive)] text-[var(--sr-text-label)] border-[var(--sr-border-subtle)]",
  };
  const klass = colors[p || "unknown"] ?? colors.unknown;
  return (
    <span className={`admin-mono-font text-[9px] uppercase tracking-[0.14em] px-2 py-0.5 border rounded-full ${klass}`}>
      {p || "unknown"}
    </span>
  );
}

function StatusBadge({ s }: { s: DiscoveryStatus }) {
  const map: Record<DiscoveryStatus, { label: string; cls: string }> = {
    pending: { label: "Pending", cls: "text-[var(--sr-text-tertiary)]" },
    confirmed: { label: "Confirmed", cls: "text-[var(--sr-status-info)]" },
    ingested: { label: "Ingested", cls: "text-[var(--sr-status-success)]" },
    rejected: { label: "Rejected", cls: "text-[var(--sr-text-label)] italic" },
    failed: { label: "Failed", cls: "text-[var(--sr-status-danger)]" },
  };
  const v = map[s] || { label: s, cls: "text-[var(--sr-text-label)]" };
  return (
    <span className={`admin-mono-font text-[9px] uppercase tracking-[0.14em] ${v.cls}`}>
      {v.label}
    </span>
  );
}

function Confidence({ v }: { v: number | null }) {
  if (v == null) return <span className="text-[var(--sr-text-label)]">—</span>;
  const pct = Math.round(v * 100);
  const cls =
    v >= 0.85 ? "text-[var(--sr-status-success)]"
    : v >= 0.6 ? "text-[var(--sr-text-tertiary)]"
    : "text-[var(--sr-status-danger)]";
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
    const t = localStorage.getItem("admin_token") || process.env.NEXT_PUBLIC_ADMIN_PASSWORD || "sailfast2026";
    if (t) {
      localStorage.setItem("admin_token", t);
      setToken(t);
    }
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
          <h1 className="heading-display text-2xl text-[var(--sr-text-primary)] text-center mb-6">Discovery</h1>
          <input
            type="password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            placeholder="Admin password"
            className="w-full h-12 px-4 bg-[var(--sr-surface-card)] border border-[var(--sr-border-strong)] text-[var(--sr-text-primary)] text-[13px] placeholder:text-[var(--sr-text-tertiary)] focus:border-[var(--sr-dusk)] focus:ring-1 focus:ring-[var(--sr-dusk)]/40 outline-none transition-all rounded-md"
          />
          <button type="submit" className="w-full h-12 bg-[var(--sr-dusk)] text-white text-[13px] font-medium hover:bg-[var(--sr-link)] transition-colors rounded-md">
            Sign in
          </button>
        </form>
      </div>
    );
  }

  return (
    <div data-testid="discovery-page" className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="mb-8">
          <h1 className="heading-display text-2xl text-[var(--sr-text-primary)]">Event Discovery</h1>
          <p className="text-[13px] text-[var(--sr-text-tertiary)] mt-1">
            Crawler finds sailing-event URLs, Claude identifies the scoring platform. Confirm to ingest.
          </p>
        </div>

        {/* Seed form */}
        <div className="border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-md p-4 mb-6">
          <div className="flex items-baseline gap-3 mb-3">
            <PlusIcon size={14} className="text-[var(--sr-dusk)]" />
            <span className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
              Crawl a URL
            </span>
          </div>
          <div className="flex flex-col sm:flex-row gap-3 items-stretch sm:items-center">
            <input
              type="url"
              value={seedUrl}
              onChange={(e) => setSeedUrl(e.target.value)}
              placeholder="https://www.brisbanetogladstone.com/2026-race-results/"
              className="flex-1 h-10 px-3 bg-[var(--sr-surface-deep)] border border-[var(--sr-border-strong)] text-[var(--sr-text-primary)] text-[13px] focus:border-[var(--sr-dusk)] focus:ring-1 focus:ring-[var(--sr-dusk)]/40 outline-none transition-all rounded-md"
            />
            <select
              value={seedMode}
              onChange={(e) => setSeedMode(e.target.value as "map" | "single")}
              className="h-10 px-3 bg-[var(--sr-surface-deep)] border border-[var(--sr-border-strong)] text-[var(--sr-text-primary)] text-[13px] rounded-md outline-none focus:border-[var(--sr-dusk)]"
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
                className="w-20 h-10 px-3 bg-[var(--sr-surface-deep)] border border-[var(--sr-border-strong)] text-[var(--sr-text-primary)] text-[13px] rounded-md outline-none focus:border-[var(--sr-dusk)]"
                title="Max subpages to process"
              />
            )}
            <button
              onClick={handleSeed}
              disabled={seeding || !seedUrl.trim()}
              className="h-10 px-4 bg-[var(--sr-dusk)] text-white text-[11px] font-medium uppercase tracking-[0.08em] hover:bg-[var(--sr-link)] transition-colors rounded-md disabled:opacity-40"
            >
              {seeding ? (
                <span className="inline-flex items-center gap-2">
                  <SpinnerIcon size={14} className="animate-spin" />
                  Crawling
                </span>
              ) : "Crawl"}
            </button>
          </div>
        </div>

        {error && (
          <div className="border border-[var(--sr-status-danger)]/40 bg-[var(--sr-status-danger)]/10 px-4 py-3 mb-6 text-[13px] text-[var(--sr-status-danger)] rounded-md">
            {error}
          </div>
        )}

        {/* Filter pills */}
        <div className="flex items-center gap-2 mb-4">
          {(["pending", "confirmed", "ingested", "failed", "rejected", "all"] as const).map((s) => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`admin-mono-font text-[9px] uppercase tracking-[0.14em] px-3 py-1.5 border rounded-full transition-colors ${
                statusFilter === s
                  ? "border-[var(--sr-dusk)] text-[var(--sr-link)] bg-[var(--sr-dusk-interactive)]"
                  : "border-[var(--sr-border-subtle)] text-[var(--sr-text-label)] bg-transparent hover:text-[var(--sr-text-primary)] hover:border-[var(--sr-border-strong)]"
              }`}
            >
              {s}
            </button>
          ))}
          <span className="admin-mono-font text-[9px] text-[var(--sr-text-label)] ml-auto">
            {rows.length} row{rows.length === 1 ? "" : "s"}
          </span>
        </div>

        {/* Table */}
        <div className="admin-table-container">
          <div className="grid grid-cols-[1fr_110px_90px_120px_180px] gap-4 px-4 py-3 bg-[var(--sr-surface-card)] border-b border-[var(--sr-border-subtle)] admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] font-medium">
            <span>Title / URL</span>
            <span>Platform</span>
            <span className="text-right">Confidence</span>
            <span>Status</span>
            <span className="text-right">Actions</span>
          </div>

          {loading && (
            <div className="px-4 py-6 flex items-center gap-2 text-[var(--sr-text-label)] text-[13px]">
              <SpinnerIcon size={14} className="animate-spin" />
              Loading
            </div>
          )}

          {!loading && rows.length === 0 && (
            <div className="px-4 py-8 text-[13px] text-[var(--sr-text-label)] italic text-center">
              No discoveries for this filter.
              {statusFilter === "pending" && " Paste a URL above to start."}
            </div>
          )}

          {rows.map((d) => {
            const open = expanded === d.id;
            return (
              <div key={d.id} className="border-b border-[var(--sr-border-subtle)] last:border-b-0">
                <div
                  className="grid grid-cols-[1fr_110px_90px_120px_180px] gap-4 px-4 py-3 items-center cursor-pointer hover:bg-[var(--sr-surface-interactive)] transition-colors"
                  onClick={() => setExpanded(open ? null : d.id)}
                >
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      {open ? <ChevronDownIcon size={12} className="text-[var(--sr-text-label)]" />
                            : <ChevronRightIcon size={12} className="text-[var(--sr-text-label)]" />}
                      <p className="text-[13px] text-[var(--sr-text-primary)] truncate font-medium">
                        {d.title || d.source_url}
                      </p>
                    </div>
                    <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] mt-0.5 truncate ml-5">
                      {d.event_date || ""}{d.event_location ? " · " + d.event_location : ""}
                      {!d.title ? "" : "  "}
                      <a
                        href={d.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        onClick={(e) => e.stopPropagation()}
                        className="hover:text-[var(--sr-link)] inline-flex items-center gap-1"
                      >
                        {d.source_url.replace(/^https?:\/\//, "").slice(0, 60)}
                        <ExternalLinkIcon size={10} />
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
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs bg-[var(--sr-dusk)] text-white hover:bg-[var(--sr-link)] disabled:opacity-30 rounded-md transition-colors"
                          title="Confirm + ingest"
                        >
                          {busy === d.id
                            ? <SpinnerIcon size={12} className="animate-spin" />
                            : <CheckIcon size={12} strokeWidth={2.5} />}
                          Confirm
                        </button>
                        <button
                          onClick={(e) => { e.stopPropagation(); handleAction(d.id, "reject"); }}
                          disabled={busy === d.id}
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs border border-[var(--sr-status-danger)]/40 text-[var(--sr-status-danger)] hover:bg-[var(--sr-status-danger)]/10 disabled:opacity-30 rounded-md transition-colors"
                        >
                          <XIcon size={12} strokeWidth={2.5} />
                          Reject
                        </button>
                      </>
                    )}
                  </div>
                </div>

                {open && (
                  <div className="px-4 pb-4 pt-1 ml-5 bg-[var(--sr-surface-interactive)]/50">
                    <dl className="grid grid-cols-[140px_1fr] gap-x-4 gap-y-1.5 admin-mono-font text-[10px] text-[var(--sr-text-tertiary)]">
                      <dt className="text-[var(--sr-text-label)]">discovered_at</dt>
                      <dd>{fmtDate(d.discovered_at)}</dd>
                      <dt className="text-[var(--sr-text-label)]">source_type</dt>
                      <dd>{d.source_type}{d.seed_url ? `  (seed: ${d.seed_url})` : ""}</dd>
                      <dt className="text-[var(--sr-text-label)]">platform_ids</dt>
                      <dd className="font-mono text-[10px] text-[var(--sr-text-primary)] break-all bg-[var(--sr-surface-deep)] border border-[var(--sr-border-subtle)] px-2 py-1 rounded-[2px]">
                        {JSON.stringify(d.platform_ids || {}, null, 0)}
                      </dd>
                      {d.error_message && (
                        <>
                          <dt className="text-[var(--sr-text-label)]">error</dt>
                          <dd className="text-[var(--sr-status-danger)] font-semibold">{d.error_message}</dd>
                        </>
                      )}
                      {d.notes && (
                        <>
                          <dt className="text-[var(--sr-text-label)]">notes</dt>
                          <dd className="text-[var(--sr-text-primary)]">{d.notes}</dd>
                        </>
                      )}
                      {d.confirmed_at && (
                        <>
                          <dt className="text-[var(--sr-text-label)]">confirmed_at</dt>
                          <dd>{fmtDate(d.confirmed_at)}</dd>
                        </>
                      )}
                      {d.ingested_at && (
                        <>
                          <dt className="text-[var(--sr-text-label)]">ingested_at</dt>
                          <dd className="text-[var(--sr-status-success)]">{fmtDate(d.ingested_at)}</dd>
                        </>
                      )}
                    </dl>
                  </div>
                )}
              </div>
            );
          })}
        </div>

        <p className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-label)] mt-6">
          Crawler: Firecrawl  ·  Extractor: Claude Sonnet 4.5  ·  Confidence threshold for auto-ingest: 85%
        </p>
      </div>
    </div>
  );
}
