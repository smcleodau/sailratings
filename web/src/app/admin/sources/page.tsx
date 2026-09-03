"use client";

/**
 * /admin/sources — the source register control surface (AD-01-16).
 *
 * One hairline row per ``data_sources`` register entry, joined with the
 * ``source_schedule_state`` mirror (GET /admin/scrapers/schedule-state):
 *
 *   · a pause/resume toggle backed by POST /admin/scrapers/{slug}/pause and
 *     …/resume — the backend flips the Temporal schedule and the mirror row
 *     atomically (when Temporal is unreachable the mirror still flips and
 *     the API returns 503 with the desired state recorded; the row shows
 *     that honestly instead of pretending the toggle failed)
 *   · ``data_sources.robots_checked_at`` — when we last read the source's
 *     robots.txt (null = never checked)
 *   · legal status, cadence, adapter status, and the latest ledger run
 */

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangleIcon,
  CheckCircleIcon,
  ExternalLinkIcon,
  PauseIcon,
  PlayIcon,
  RefreshIcon,
  SpinnerIcon,
} from "@/components/admin/AdminIcons";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

interface SourceRow {
  slug: string;
  display_name: string;
  base_url: string | null;
  category: string | null;
  legal_status: string | null;
  enabled: boolean;
  cadence: string | null;
  adapter_status: string | null;
  adapter_class: string | null;
  robots_checked_at: string | null;
  robots_disallow: string[] | null;
  contact_email: string | null;
  schedule_id: string | null;
  schedule_paused: boolean | null;
  schedule_synced_at: string | null;
  last_run_status: string | null;
  last_run_at: string | null;
}

interface ToggleOutcome {
  slug: string;
  ok: boolean;
  /** 503 = Temporal unreachable; the mirror flipped anyway. */
  degraded: boolean;
  detail?: string;
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

function fmtAge(iso: string | null): string {
  if (!iso) return "never";
  const diffSec = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (diffSec < 60) return `${diffSec}s ago`;
  if (diffSec < 3600) return `${Math.floor(diffSec / 60)}m ago`;
  if (diffSec < 86400) return `${(diffSec / 3600).toFixed(1)}h ago`;
  return `${(diffSec / 86400).toFixed(1)}d ago`;
}

function LegalPill({ status }: { status: string | null }) {
  const s = (status ?? "unknown").toLowerCase();
  const cls =
    s === "ok" || s === "allowed" || s === "approved"
      ? "!border-[var(--sr-status-success)]/40 !text-[var(--sr-status-success)]"
      : s === "blocked" || s === "disallowed" || s === "denied"
        ? "!border-[var(--sr-status-danger)]/40 !text-[var(--sr-status-danger)]"
        : s === "review" || s === "pending" || s === "unknown"
          ? "!border-[var(--sr-status-warning)]/40 !text-[var(--sr-status-warning)]"
          : "";
  return (
    <span className={`admin-pill ${cls}`} title={`legal_status: ${status ?? "null"}`}>
      {status ?? "unknown"}
    </span>
  );
}

function AdapterPill({ status }: { status: string | null }) {
  const s = (status ?? "planned").toLowerCase();
  const cls =
    s === "live" || s === "active"
      ? "!border-[var(--sr-status-success)]/40 !text-[var(--sr-status-success)]"
      : s === "broken" || s === "disabled"
        ? "!border-[var(--sr-status-danger)]/40 !text-[var(--sr-status-danger)]"
        : "";
  return <span className={`admin-pill ${cls}`}>{status ?? "planned"}</span>;
}

/** The robots.txt check state, from data_sources.robots_checked_at. */
function RobotsCell({ row }: { row: SourceRow }) {
  if (!row.robots_checked_at) {
    return (
      <span
        data-testid={`robots-never-${row.slug}`}
        className="admin-pill !border-[var(--sr-status-warning)]/40 !text-[var(--sr-status-warning)]"
        title="robots.txt has never been fetched for this source"
      >
        <AlertTriangleIcon size={9} strokeWidth={2} />
        never checked
      </span>
    );
  }
  const disallowed = (row.robots_disallow ?? []).length > 0;
  return (
    <span
      data-testid={`robots-checked-${row.slug}`}
      className={`admin-pill ${
        disallowed
          ? "!border-[var(--sr-status-danger)]/40 !text-[var(--sr-status-danger)]"
          : "!border-[var(--sr-status-success)]/40 !text-[var(--sr-status-success)]"
      }`}
      title={`robots.txt checked ${fmtDateTime(row.robots_checked_at)}${
        disallowed
          ? ` · disallows: ${(row.robots_disallow ?? []).slice(0, 3).join(", ")}`
          : ""
      }`}
    >
      <CheckCircleIcon size={9} strokeWidth={2} />
      {fmtAge(row.robots_checked_at)}
    </span>
  );
}

function LastRunCell({ row }: { row: SourceRow }) {
  if (!row.last_run_at) {
    return (
      <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
        never run
      </span>
    );
  }
  const cls =
    row.last_run_status === "completed"
      ? "text-[var(--sr-status-success)]"
      : row.last_run_status === "failed"
        ? "text-[var(--sr-status-danger)]"
        : "text-[var(--sr-status-info)]";
  return (
    <span className="admin-mono-font text-[10px] text-[var(--sr-text-secondary)] tabular-nums">
      <span className={cls}>{row.last_run_status ?? "—"}</span>
      {" · "}
      {fmtAge(row.last_run_at)}
    </span>
  );
}

export default function SourcesPage() {
  const [token, setToken] = useState<string | null>(null);
  const [pw, setPw] = useState("");
  const [rows, setRows] = useState<SourceRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [outcomes, setOutcomes] = useState<Record<string, ToggleOutcome>>({});

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

  const fetchRows = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/admin/scrapers/schedule-state`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        if (res.status === 401 || res.status === 403) {
          localStorage.removeItem("admin_token");
          setToken(null);
          throw new Error("Session expired.");
        }
        throw new Error(`Failed: ${res.status}`);
      }
      const data = await res.json();
      setRows(data.scrapers ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    fetchRows();
  }, [fetchRows]);

  /**
   * Flip source_schedule_state.paused via the control plane.  The backend
   * flips Temporal and the mirror together; a 503 means Temporal was
   * unreachable but the desired state was still recorded — we surface that
   * as a degraded-but-applied outcome rather than an error.
   */
  const togglePaused = useCallback(
    async (row: SourceRow) => {
      if (!token || busy) return;
      const target = !(row.schedule_paused ?? false);
      setBusy(row.slug);
      setOutcomes((prev) => {
        const next = { ...prev };
        delete next[row.slug];
        return next;
      });
      try {
        const res = await fetch(
          `${API_BASE}/admin/scrapers/${encodeURIComponent(row.slug)}/${target ? "pause" : "resume"}`,
          {
            method: "POST",
            headers: { Authorization: `Bearer ${token}` },
          },
        );
        const body = await res.json().catch(() => ({}));
        if (res.ok) {
          setOutcomes((prev) => ({
            ...prev,
            [row.slug]: { slug: row.slug, ok: true, degraded: false },
          }));
        } else if (res.status === 503) {
          // Mirror flipped, Temporal unreachable — desired state recorded.
          setOutcomes((prev) => ({
            ...prev,
            [row.slug]: {
              slug: row.slug,
              ok: true,
              degraded: true,
              detail: body?.detail,
            },
          }));
        } else {
          throw new Error(body?.detail ?? `${res.status}`);
        }
      } catch (e) {
        setOutcomes((prev) => ({
          ...prev,
          [row.slug]: {
            slug: row.slug,
            ok: false,
            degraded: false,
            detail: e instanceof Error ? e.message : "toggle failed",
          },
        }));
      } finally {
        setBusy(null);
        await fetchRows();
      }
    },
    [token, busy, fetchRows],
  );

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
          <h1 className="heading-display text-2xl text-[var(--sr-text-primary)] text-center mb-6">
            Sources
          </h1>
          <input
            type="password"
            value={pw}
            onChange={(e) => setPw(e.target.value)}
            placeholder="Admin password"
            className="w-full h-12 px-4 bg-[var(--sr-surface-card)] border border-[var(--sr-border-strong)] text-[var(--sr-text-primary)] text-[13px] placeholder:text-[var(--sr-text-tertiary)] focus:border-[var(--sr-dusk)] focus:ring-1 focus:ring-[var(--sr-dusk)]/40 outline-none transition-all rounded-md"
          />
          <button
            type="submit"
            className="w-full h-12 bg-[var(--sr-dusk)] text-white text-[13px] font-medium hover:bg-[var(--sr-link)] transition-colors rounded-md"
          >
            Sign in
          </button>
        </form>
      </div>
    );
  }

  const pausedCount = rows.filter((r) => r.schedule_paused).length;
  const robotsUnchecked = rows.filter((r) => !r.robots_checked_at).length;

  return (
    <div data-testid="sources-page" className="flex-1 overflow-y-auto">
      <div className="max-w-6xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-end justify-between mb-8 gap-6 flex-wrap">
          <div>
            <h1 className="heading-display text-2xl text-[var(--sr-text-primary)]">
              Sources
            </h1>
            <p className="text-[13px] text-[var(--sr-text-tertiary)] mt-1">
              The data_sources register joined with its schedule mirror. Pause
              stops the Temporal schedule; robots shows the last robots.txt
              check.
            </p>
          </div>
          <div className="flex items-center gap-3 text-[var(--sr-text-label)]">
            <span className="admin-mono-font text-[10px] uppercase tracking-[0.16em]">
              {rows.length} registered · {pausedCount} paused ·{" "}
              {robotsUnchecked} robots-unchecked
            </span>
            <button
              onClick={fetchRows}
              disabled={loading}
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)] border border-[var(--sr-border-subtle)] rounded-md px-2.5 py-1.5 transition-colors disabled:opacity-40"
            >
              <RefreshIcon
                size={12}
                strokeWidth={2}
                className={loading ? "animate-spin" : ""}
              />
              Refresh
            </button>
          </div>
        </div>

        {error && (
          <div className="border border-[var(--sr-status-danger)]/40 bg-[var(--sr-status-danger)]/10 px-4 py-3 mb-6 text-[13px] text-[var(--sr-status-danger)] rounded-md">
            {error}
          </div>
        )}

        {/* Register table */}
        <div className="admin-table-container">
          <div className="admin-table-header grid grid-cols-[1.8fr_1fr_0.9fr_0.9fr_1fr_1.2fr_110px] gap-4 admin-mono-font text-[9px] uppercase tracking-[0.16em]">
            <span>Source</span>
            <span>Cadence</span>
            <span>Legal</span>
            <span>Adapter</span>
            <span>Robots</span>
            <span>Last run</span>
            <span className="text-right">Schedule</span>
          </div>

          {loading && rows.length === 0 && (
            <div className="px-4 py-6 flex items-center gap-2 text-[var(--sr-text-label)] text-[13px]">
              <SpinnerIcon size={14} className="animate-spin" />
              Loading the register…
            </div>
          )}

          {!loading && rows.length === 0 && (
            <div className="px-4 py-8 text-[13px] text-[var(--sr-text-label)] italic text-center">
              No sources registered in data_sources.
            </div>
          )}

          {rows.map((row) => {
            const paused = row.schedule_paused ?? false;
            const outcome = outcomes[row.slug];
            return (
              <div
                key={row.slug}
                data-testid={`source-row-${row.slug}`}
                className="grid grid-cols-[1.8fr_1fr_0.9fr_0.9fr_1fr_1.2fr_110px] gap-4 px-4 py-3 items-center border-b border-[var(--sr-border-subtle)] last:border-b-0 hover:bg-[var(--sr-surface-interactive)] transition-colors"
              >
                {/* Source identity */}
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <p className="text-[13px] text-[var(--sr-text-primary)] truncate font-medium">
                      {row.display_name}
                    </p>
                    {row.base_url && (
                      <a
                        href={row.base_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="text-[var(--sr-text-label)] hover:text-[var(--sr-link)] transition-colors flex-shrink-0"
                        title={row.base_url}
                      >
                        <ExternalLinkIcon size={11} />
                      </a>
                    )}
                  </div>
                  <p className="admin-mono-font text-[10px] text-[var(--sr-text-label)] mt-0.5 truncate">
                    {row.slug}
                    {row.category ? ` · ${row.category}` : ""}
                  </p>
                  {outcome && (
                    <p
                      data-testid={`toggle-outcome-${row.slug}`}
                      className={`admin-mono-font text-[9px] mt-1 ${
                        outcome.degraded
                          ? "text-[var(--sr-status-warning)]"
                          : outcome.ok
                            ? "text-[var(--sr-status-success)]"
                            : "text-[var(--sr-status-danger)]"
                      }`}
                    >
                      {outcome.degraded
                        ? "Mirror flipped — Temporal unreachable; desired state recorded."
                        : outcome.ok
                          ? paused
                            ? "Resumed."
                            : "Paused."
                          : `Failed: ${outcome.detail ?? "unknown error"}`}
                    </p>
                  )}
                </div>

                {/* Cadence */}
                <div className="admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                  {row.cadence ?? "—"}
                  {!row.enabled && (
                    <span className="admin-pill ml-1.5">disabled</span>
                  )}
                </div>

                {/* Legal */}
                <div>
                  <LegalPill status={row.legal_status} />
                </div>

                {/* Adapter */}
                <div>
                  <AdapterPill status={row.adapter_status} />
                </div>

                {/* Robots */}
                <div>
                  <RobotsCell row={row} />
                </div>

                {/* Last run */}
                <div>
                  <LastRunCell row={row} />
                </div>

                {/* Pause/resume toggle — source_schedule_state.paused */}
                <div className="flex items-center justify-end gap-2">
                  <span
                    className={`admin-mono-font text-[9px] uppercase tracking-[0.12em] ${
                      paused
                        ? "text-[var(--sr-status-warning)]"
                        : "text-[var(--sr-text-label)]"
                    }`}
                  >
                    {paused ? "paused" : "live"}
                  </span>
                  <button
                    role="switch"
                    aria-checked={paused}
                    aria-label={`${paused ? "Resume" : "Pause"} ${row.display_name}`}
                    data-testid={`pause-toggle-${row.slug}`}
                    disabled={busy === row.slug}
                    onClick={() => togglePaused(row)}
                    className={`relative w-9 h-5 rounded-full border transition-colors disabled:opacity-40 ${
                      paused
                        ? "bg-[var(--sr-status-warning)]/25 border-[var(--sr-status-warning)]/50"
                        : "bg-[var(--sr-dusk-interactive)] border-[var(--sr-border-strong)]"
                    }`}
                  >
                    {busy === row.slug ? (
                      <SpinnerIcon
                        size={10}
                        className="animate-spin absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-[var(--sr-text-label)]"
                      />
                    ) : (
                      <span
                        className={`absolute top-[3px] w-3.5 h-3.5 rounded-full transition-all ${
                          paused
                            ? "left-[3px] bg-[var(--sr-status-warning)]"
                            : "left-[19px] bg-[var(--sr-link)]"
                        }`}
                      />
                    )}
                  </button>
                  <span className="text-[var(--sr-text-label)] w-4">
                    {paused ? (
                      <PauseIcon size={12} />
                    ) : (
                      <PlayIcon size={12} />
                    )}
                  </span>
                </div>
              </div>
            );
          })}
        </div>

        <p className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-label)] mt-6">
          Toggle writes source_schedule_state.paused and the Temporal schedule
          · robots column reads data_sources.robots_checked_at
        </p>
      </div>
    </div>
  );
}
