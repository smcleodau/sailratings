"use client";

/**
 * /admin/dupes/history — the duplicate-boats decision trail (AD-01-14).
 *
 * Every verdict written from /admin/dupes lands here, newest first:
 * merges come from boat_merges (winner ← loser, with the loser snapshot
 * preserved for reversal) and not-dupes from boat_not_dupe.  Backed by
 * GET /v1/admin/dupes/history.
 */

import React, { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeftIcon as ArrowLeft,
  ChevronDownIcon as ChevronDown,
  GitMergeIcon as GitMerge,
  MinusIcon as Minus,
  RefreshIcon as RefreshCw,
} from "@/components/admin/AdminIcons";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

interface HistoryEntry {
  kind: "merged" | "not_dupe";
  at: string | null;
  cluster_key: string;
  winner_id?: number;
  winner_name?: string | null;
  winner_sail?: string | null;
  winner_country?: string | null;
  loser_id?: number;
  loser_name?: string | null;
  loser_sail?: string | null;
  loser_snapshot?: { boat?: Record<string, unknown>; extras?: unknown[] } | null;
  boat_ids?: number[];
  reason?: string;
  reviewed_by?: string | null;
}

interface HistoryResponse {
  entries: HistoryEntry[];
  next_cursor: string | null;
}

function getAdminToken(): string {
  const stored =
    typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
  const token =
    stored || process.env.NEXT_PUBLIC_ADMIN_PASSWORD || "sailfast2026";
  if (typeof window !== "undefined" && !stored) {
    localStorage.setItem("admin_token", token);
  }
  return token;
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function DupeHistoryPage() {
  // Lazy initializer keeps the localStorage read out of an effect.
  const [token, setToken] = useState<string | null>(() => getAdminToken());
  const [entries, setEntries] = useState<HistoryEntry[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [kind, setKind] = useState<"all" | "merged" | "not_dupe">("all");
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const load = useCallback(
    async (cursor?: string, append = false) => {
      if (!token) return;
      setLoading(true);
      setError(null);
      try {
        const p = new URLSearchParams({ limit: "50" });
        if (kind !== "all") p.set("kind", kind);
        if (cursor) p.set("cursor", cursor);
        const res = await fetch(`${API_BASE}/admin/dupes/history?${p}`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (res.status === 401) {
          localStorage.removeItem("admin_token");
          setToken(null);
          return;
        }
        if (!res.ok) throw new Error(`History load failed: ${res.status}`);
        const body: HistoryResponse = await res.json();
        setEntries((prev) => (append ? [...prev, ...body.entries] : body.entries));
        setNextCursor(body.next_cursor);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load history");
      } finally {
        setLoading(false);
      }
    },
    [token, kind],
  );

  useEffect(() => {
    setEntries([]);
    setNextCursor(null);
    void load();
  }, [load]);

  const toggleExpanded = (key: string) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--sr-surface-page)]">
      <div className="max-w-6xl mx-auto px-6 py-8 space-y-6">
        {/* Header */}
        <div className="flex items-end justify-between gap-6 flex-wrap">
          <div>
            <h1 className="heading-display text-2xl text-[var(--sr-text-primary)]">
              Merge history
            </h1>
            <p className="text-[13px] text-[var(--sr-text-tertiary)] mt-1">
              Every merge keeps the loser&apos;s full row in
              boat_merges.loser_snapshot so a wrong call can be reversed.
              Not-dupe verdicts come from boat_not_dupe.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <Link
              href="/admin/dupes"
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.14em] text-[var(--sr-text-secondary)] hover:text-[var(--sr-text-primary)] border border-[var(--sr-border-subtle)] rounded-[4px] px-3 py-2 transition-colors"
            >
              <ArrowLeft size={12} strokeWidth={2} />
              Review queue
            </Link>
            <button
              onClick={() => void load()}
              disabled={loading}
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.14em] text-[var(--sr-text-secondary)] hover:text-[var(--sr-text-primary)] border border-[var(--sr-border-subtle)] rounded-[4px] px-3 py-2 transition-colors disabled:opacity-50"
            >
              <RefreshCw size={12} strokeWidth={2} className={loading ? "animate-spin" : ""} />
              Refresh
            </button>
          </div>
        </div>

        {/* Kind filter chips */}
        <div className="flex items-center gap-2" data-testid="history-filters">
          {(
            [
              ["all", "All"],
              ["merged", "Merges"],
              ["not_dupe", "Not dupes"],
            ] as const
          ).map(([k, label]) => (
            <button
              key={k}
              data-testid={`history-kind-${k}`}
              aria-pressed={kind === k}
              onClick={() => setKind(k)}
              className={`admin-mono-font text-[10px] uppercase tracking-[0.12em] rounded-full border px-2.5 py-1 transition-colors ${
                kind === k
                  ? "border-[var(--sr-dusk)] bg-[var(--sr-dusk-interactive)] text-[var(--sr-text-primary)]"
                  : "border-[var(--sr-border-subtle)] text-[var(--sr-text-tertiary)] hover:text-[var(--sr-text-primary)]"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {error && (
          <div role="alert" className="border border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/5 text-[var(--sr-action-pressed)] rounded-[4px] px-4 py-3 text-[13px]">
            {error}
          </div>
        )}

        {/* Entries */}
        <div className="border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] divide-y divide-[var(--sr-border-subtle)]">
          {entries.map((e, i) => {
            const key = `${e.kind}-${e.cluster_key}-${i}`;
            const isOpen = expanded.has(key);
            return (
              <div key={key} data-testid={`history-entry-${e.kind}`}>
                <button
                  onClick={() => toggleExpanded(key)}
                  className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-[var(--sr-dusk-raised)]/40 transition-colors"
                >
                  {e.kind === "merged" ? (
                    <GitMerge size={14} strokeWidth={1.8} className="text-[var(--sr-signal-500)] flex-shrink-0" />
                  ) : (
                    <Minus size={14} strokeWidth={1.8} className="text-[var(--sr-status-info)] flex-shrink-0" />
                  )}
                  <span className="text-[13px] text-[var(--sr-text-primary)] font-medium min-w-0 truncate">
                    {e.kind === "merged" ? (
                      <>
                        {e.loser_name ?? `#${e.loser_id}`}
                        {e.loser_sail ? ` (${e.loser_sail})` : ""}
                        <span className="text-[var(--sr-text-tertiary)]"> merged into </span>
                        {e.winner_name ?? `#${e.winner_id}`}
                        {e.winner_sail ? ` (${e.winner_sail})` : ""}
                        <span className="text-[var(--sr-text-tertiary)]"> · #{e.winner_id}</span>
                      </>
                    ) : (
                      <>
                        {e.cluster_key}
                        <span className="text-[var(--sr-text-tertiary)]"> kept separate — </span>
                        {e.reason}
                      </>
                    )}
                  </span>
                  <span className="ml-auto flex items-center gap-3 flex-shrink-0">
                    {e.reviewed_by && (
                      <span className="admin-mono-font text-[9px] text-[var(--sr-text-label)]">
                        {e.reviewed_by}
                      </span>
                    )}
                    <span className="admin-mono-font text-[10px] text-[var(--sr-text-tertiary)] tabular-nums">
                      {fmtDateTime(e.at)}
                    </span>
                    <ChevronDown
                      size={12}
                      strokeWidth={2}
                      className={`text-[var(--sr-text-tertiary)] transition-transform ${isOpen ? "rotate-180" : ""}`}
                    />
                  </span>
                </button>
                {isOpen && (
                  <div className="px-4 pb-4 pl-11">
                    {e.kind === "merged" && e.loser_snapshot?.boat && (
                      <div>
                        <p className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-label)] mb-2">
                          Loser snapshot (boat_merges.loser_snapshot)
                        </p>
                        <pre
                          data-testid="history-snapshot"
                          className="admin-mono-font text-[10px] leading-relaxed text-[var(--sr-text-secondary)] bg-[var(--sr-surface-deep)] border border-[var(--sr-border-subtle)] rounded-[4px] px-3 py-2 overflow-x-auto"
                        >
                          {JSON.stringify(e.loser_snapshot.boat, null, 2)}
                        </pre>
                      </div>
                    )}
                    {e.kind === "not_dupe" && (
                      <p className="text-[12px] text-[var(--sr-text-secondary)]">
                        Boats {(e.boat_ids ?? []).map((b) => `#${b}`).join(", ")}{" "}
                        recorded as distinct in boat_not_dupe.
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
          {!loading && entries.length === 0 && (
            <div
              data-testid="history-empty"
              className="px-6 py-12 text-center text-[13px] text-[var(--sr-text-secondary)]"
            >
              No decisions recorded yet.
            </div>
          )}
        </div>

        {nextCursor && (
          <div className="flex justify-center">
            <button
              onClick={() => void load(nextCursor, true)}
              disabled={loading}
              data-testid="history-load-more"
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.14em] text-[var(--sr-text-secondary)] hover:text-[var(--sr-text-primary)] border border-[var(--sr-border-subtle)] rounded-[4px] px-4 py-2 transition-colors disabled:opacity-50"
            >
              <ChevronDown size={12} strokeWidth={2} />
              Load more
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
