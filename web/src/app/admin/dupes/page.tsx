"use client";

/**
 * /admin/dupes — the duplicate-boats review queue (AD-01-14).
 *
 * Each pending ``dupe_review_queue`` cluster renders as one card with a
 * column per boat (2–5), ordered by evidence.  The most-evidenced boat is
 * highlighted and pre-selected as the merge target; its Signal merge
 * button is the one action that writes — every FK re-point + the
 * boat_merges snapshot happen in a single transaction behind
 * POST /v1/admin/dupes/clusters/{id}/merge.  The card footer carries the
 * reason select + "Not duplicates" and "Skip".
 *
 * A decided card animates out (collapse + fade), the queue count in the
 * header and the sidebar decrement immediately, and the next cluster takes
 * its place.  Decisions land in the merge history at
 * /admin/dupes/history.
 */

import React, { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  GitMerge,
  History,
  Minus,
  RefreshCw,
  SkipForward,
  X,
} from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

/* ── Types (mirror the dupe-review-v1 contract) ────────────────────────── */

interface DupeBoat {
  id: number;
  boat_id: number;
  boat_name: string | null;
  country: string | null;
  sail_number: string | null;
  design: string | null;
  year_built: number | null;
  race_results: number;
  cert_count: number;
  latest_activity: string | null;
  owner: string | null;
  why: string | null;
}

interface DupeCluster {
  cluster_id: string;
  tier: string;
  size: number;
  countries: string[];
  boats: DupeBoat[];
}

interface DupesResponse {
  clusters: DupeCluster[];
  next_cursor: string | null;
  total: number;
  pending_total: number;
}

interface DupeMeta {
  tiers: string[];
  sizes: number[];
  countries: string[];
  not_dupe_reasons: string[];
}

type Decision = "MERGED" | "NOT_DUPE" | "SKIPPED";

const REASON_LABELS: Record<string, string> = {
  different_design: "Different design",
  different_year: "Different year",
  different_region: "Different region",
  name_coincidence: "Name coincidence",
  other: "Other",
};

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

/** Decrement the "Data quality" sidebar count immediately after a decision. */
function decrementSidebarCount() {
  const el = document.querySelector('[data-testid="sidebar-count-data_quality"]');
  if (!el) return;
  const n = parseInt(el.textContent ?? "", 10);
  if (Number.isFinite(n)) el.textContent = String(Math.max(0, n - 1));
}

/* ── Page ──────────────────────────────────────────────────────────────── */

export default function DuplicateBoatsPage() {
  // Lazy initializer keeps the localStorage read out of an effect (React
  // compiler lint); getAdminToken() returns the shared admin token.
  const [token, setToken] = useState<string | null>(() => getAdminToken());
  const [clusters, setClusters] = useState<DupeCluster[]>([]);
  const [meta, setMeta] = useState<DupeMeta | null>(null);
  const [pendingTotal, setPendingTotal] = useState<number>(0);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Filter chips — empty means "all".
  const [tier, setTier] = useState<string | null>(null);
  const [size, setSize] = useState<number | null>(null);
  const [country, setCountry] = useState<string | null>(null);

  // Cards mid-animation (decision taken, collapse before unmount).
  const [exiting, setExiting] = useState<Set<string>>(new Set());
  const [acting, setActing] = useState<Set<string>>(new Set());

  const buildQuery = useCallback(
    (cursor?: string) => {
      const p = new URLSearchParams();
      if (tier) p.set("tier", tier);
      if (size) p.set("size", String(size));
      if (country) p.set("country", country);
      p.set("limit", "20");
      if (cursor) p.set("cursor", cursor);
      return p.toString();
    },
    [tier, size, country],
  );

  const load = useCallback(
    async (cursor?: string, append = false) => {
      if (!token) return;
      setLoading(true);
      setError(null);
      try {
        const [dupesRes, metaRes] = await Promise.all([
          fetch(`${API_BASE}/admin/dupes?${buildQuery(cursor)}`, {
            headers: { Authorization: `Bearer ${token}` },
          }),
          cursor
            ? Promise.resolve(null)
            : fetch(`${API_BASE}/admin/dupes/meta`, {
                headers: { Authorization: `Bearer ${token}` },
              }),
        ]);
        if (dupesRes.status === 401) {
          localStorage.removeItem("admin_token");
          setToken(null);
          return;
        }
        if (!dupesRes.ok) throw new Error(`Queue load failed: ${dupesRes.status}`);
        const body: DupesResponse = await dupesRes.json();
        setClusters((prev) => (append ? [...prev, ...body.clusters] : body.clusters));
        setPendingTotal(body.pending_total);
        setNextCursor(body.next_cursor);
        if (metaRes && metaRes.ok) setMeta(await metaRes.json());
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed to load queue");
      } finally {
        setLoading(false);
      }
    },
    [token, buildQuery],
  );

  useEffect(() => {
    void load();
  }, [load]);

  const removeCluster = useCallback((clusterId: string, message: string) => {
    // Animate out first, then unmount; the header + sidebar decrement now.
    setExiting((prev) => new Set(prev).add(clusterId));
    setPendingTotal((n) => Math.max(0, n - 1));
    decrementSidebarCount();
    setNotice(message);
    window.setTimeout(() => {
      setClusters((prev) => prev.filter((c) => c.cluster_id !== clusterId));
      setExiting((prev) => {
        const next = new Set(prev);
        next.delete(clusterId);
        return next;
      });
    }, 320);
  }, []);

  const decide = useCallback(
    async (
      cluster: DupeCluster,
      action: Decision,
      payload: Record<string, unknown>,
    ) => {
      if (!token) return;
      const cid = cluster.cluster_id;
      setActing((prev) => new Set(prev).add(cid));
      setError(null);
      setNotice(null);
      const reviewer =
        (typeof window !== "undefined" &&
          localStorage.getItem("admin_reviewer")) ||
        "admin";
      const path =
        action === "MERGED"
          ? "merge"
          : action === "NOT_DUPE"
            ? "not-dupe"
            : "skip";
      try {
        const res = await fetch(
          `${API_BASE}/admin/dupes/clusters/${encodeURIComponent(cid)}/${path}`,
          {
            method: "POST",
            headers: {
              Authorization: `Bearer ${token}`,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ ...payload, reviewed_by: reviewer }),
          },
        );
        if (res.status === 409) {
          const detail = (await res.json()).detail;
          setError(detail || "Cluster already decided");
          return;
        }
        if (!res.ok) {
          const detail = (await res.json().catch(() => ({}))).detail;
          throw new Error(detail || `Decision failed: ${res.status}`);
        }
        const body = await res.json();
        if (action === "MERGED") {
          removeCluster(
            cid,
            `Merged ${body.loser_ids.length} boat${body.loser_ids.length === 1 ? "" : "s"} into #${body.winner_id}.`,
          );
        } else if (action === "NOT_DUPE") {
          removeCluster(cid, `Marked ${cid} as not duplicates.`);
        } else {
          removeCluster(cid, `Skipped ${cid}.`);
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : "Decision failed");
      } finally {
        setActing((prev) => {
          const next = new Set(prev);
          next.delete(cid);
          return next;
        });
      }
    },
    [token, removeCluster],
  );

  const activeFilters = useMemo(
    () => [tier, size?.toString(), country].filter(Boolean).length,
    [tier, size, country],
  );

  return (
    <div className="flex-1 overflow-y-auto bg-[var(--sr-surface-page)]">
      <div className="max-w-6xl mx-auto px-6 py-8 space-y-6">
        {/* Header */}
        <div className="flex items-end justify-between gap-6 flex-wrap">
          <div>
            <h1 className="heading-display text-2xl text-[var(--sr-text-primary)]">
              Duplicate boats
            </h1>
            <p className="text-[13px] text-[var(--sr-text-tertiary)] mt-1">
              Human verdicts for the pending dupe_review_queue clusters. The
              most-evidenced boat is pre-selected as the merge target.
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span
              data-testid="dupes-pending-count"
              className="admin-mono-font text-[11px] tabular-nums text-[var(--sr-text-secondary)] border border-[var(--sr-border-subtle)] rounded-[4px] px-2.5 py-1"
            >
              {pendingTotal} pending
            </span>
            <Link
              href="/admin/dupes/history"
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.14em] text-[var(--sr-text-secondary)] hover:text-[var(--sr-text-primary)] border border-[var(--sr-border-subtle)] rounded-[4px] px-3 py-2 transition-colors"
            >
              <History size={12} strokeWidth={2} />
              Merge history
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

        {/* Filter chips */}
        <div
          data-testid="dupes-filters"
          className="flex items-center gap-2 flex-wrap"
        >
          <span className="admin-mono-font text-[9px] uppercase tracking-[0.18em] text-[var(--sr-text-label)] mr-1">
            Filters
          </span>
          {(meta?.tiers ?? []).map((t) => (
            <FilterChip
              key={`tier-${t}`}
              label={`Tier ${t}`}
              active={tier === t}
              onClick={() => setTier((cur) => (cur === t ? null : t))}
              testId={`chip-tier-${t}`}
            />
          ))}
          {(meta?.sizes ?? []).map((s) => (
            <FilterChip
              key={`size-${s}`}
              label={`${s} boats`}
              active={size === s}
              onClick={() => setSize((cur) => (cur === s ? null : s))}
              testId={`chip-size-${s}`}
            />
          ))}
          {(meta?.countries ?? []).map((c) => (
            <FilterChip
              key={`country-${c}`}
              label={c}
              active={country === c}
              onClick={() => setCountry((cur) => (cur === c ? null : c))}
              testId={`chip-country-${c}`}
            />
          ))}
          {activeFilters > 0 && (
            <button
              onClick={() => {
                setTier(null);
                setSize(null);
                setCountry(null);
              }}
              className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-status-danger)] hover:underline"
            >
              <X size={10} strokeWidth={2} />
              Clear
            </button>
          )}
        </div>

        {error && (
          <div
            role="alert"
            className="flex items-center gap-2 border border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/5 text-[var(--sr-action-pressed)] rounded-[4px] px-4 py-3 text-[13px]"
          >
            <AlertTriangle size={14} strokeWidth={2} />
            {error}
          </div>
        )}
        {notice && (
          <div
            data-testid="decision-notice"
            className="flex items-center gap-2 border border-[var(--sr-status-success)]/40 bg-[var(--sr-status-success)]/5 text-[var(--sr-status-success)] rounded-[4px] px-4 py-3 text-[13px]"
          >
            <Check size={14} strokeWidth={2} />
            {notice}
          </div>
        )}

        {/* Cluster cards */}
        <div className="space-y-4">
          {clusters.map((cluster) => (
            <ClusterCard
              key={cluster.cluster_id}
              cluster={cluster}
              exiting={exiting.has(cluster.cluster_id)}
              acting={acting.has(cluster.cluster_id)}
              reasons={meta?.not_dupe_reasons ?? Object.keys(REASON_LABELS)}
              onDecide={decide}
            />
          ))}
          {!loading && clusters.length === 0 && (
            <div
              data-testid="dupes-empty"
              className="border border-dashed border-[var(--sr-border-strong)] rounded-[4px] px-6 py-12 text-center"
            >
              <p className="text-[13px] text-[var(--sr-text-secondary)]">
                {activeFilters > 0
                  ? "No pending clusters match the active filters."
                  : "Queue clear — no pending duplicate-boat clusters."}
              </p>
            </div>
          )}
        </div>

        {nextCursor && (
          <div className="flex justify-center pt-2">
            <button
              onClick={() => void load(nextCursor, true)}
              disabled={loading}
              data-testid="dupes-load-more"
              className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.14em] text-[var(--sr-text-secondary)] hover:text-[var(--sr-text-primary)] border border-[var(--sr-border-subtle)] rounded-[4px] px-4 py-2 transition-colors disabled:opacity-50"
            >
              <ChevronDown size={12} strokeWidth={2} />
              Load more clusters
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Filter chip ───────────────────────────────────────────────────────── */

function FilterChip({
  label,
  active,
  onClick,
  testId,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  testId: string;
}) {
  return (
    <button
      data-testid={testId}
      aria-pressed={active}
      onClick={onClick}
      className={`admin-mono-font text-[10px] uppercase tracking-[0.12em] rounded-full border px-2.5 py-1 transition-colors ${
        active
          ? "border-[var(--sr-dusk)] bg-[var(--sr-dusk-interactive)] text-[var(--sr-text-primary)]"
          : "border-[var(--sr-border-subtle)] text-[var(--sr-text-tertiary)] hover:text-[var(--sr-text-primary)] hover:border-[var(--sr-border-strong)]"
      }`}
    >
      {label}
    </button>
  );
}

/* ── Cluster card ──────────────────────────────────────────────────────── */

function ClusterCard({
  cluster,
  exiting,
  acting,
  reasons,
  onDecide,
}: {
  cluster: DupeCluster;
  exiting: boolean;
  acting: boolean;
  reasons: string[];
  onDecide: (
    cluster: DupeCluster,
    action: Decision,
    payload: Record<string, unknown>,
  ) => Promise<void>;
}) {
  // The most-evidenced boat (first column) is the pre-selected merge target.
  const [winnerId, setWinnerId] = useState<number>(cluster.boats[0]?.boat_id);
  const [reason, setReason] = useState<string>(reasons[0] ?? "different_design");

  return (
    <section
      data-testid={`dupe-card-${cluster.cluster_id}`}
      className="border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] overflow-hidden transition-all duration-300 ease-out"
      style={
        exiting
          ? {
              opacity: 0,
              transform: "translateX(24px) scale(0.98)",
              maxHeight: 0,
              marginBottom: 0,
            }
          : { maxHeight: 1200 }
      }
      aria-label={`Duplicate cluster ${cluster.cluster_id}`}
    >
      {/* Card header */}
      <header className="flex items-center justify-between gap-3 px-4 py-3 border-b border-[var(--sr-border-subtle)]">
        <div className="flex items-center gap-2.5 min-w-0">
          <GitMerge
            size={14}
            strokeWidth={1.8}
            className="text-[var(--sr-text-label)] flex-shrink-0"
          />
          <span className="text-[13px] font-semibold text-[var(--sr-text-primary)] truncate">
            {cluster.cluster_id}
          </span>
          <span className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-status-warning)] border border-[var(--sr-status-warning)]/40 rounded-full px-1.5 py-[1px]">
            Tier {cluster.tier}
          </span>
          <span className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-label)]">
            {cluster.size} boats
          </span>
        </div>
        {cluster.boats[0]?.why && (
          <span
            className="admin-mono-font text-[9px] text-[var(--sr-text-tertiary)] truncate"
            title={cluster.boats[0].why}
          >
            {cluster.boats[0].why}
          </span>
        )}
      </header>

      {/* Boat columns */}
      <div
        className="grid divide-x divide-[var(--sr-border-subtle)]"
        style={{
          gridTemplateColumns: `repeat(${Math.min(Math.max(cluster.boats.length, 2), 5)}, minmax(0, 1fr))`,
        }}
      >
        {cluster.boats.map((boat, idx) => {
          const isWinner = boat.boat_id === winnerId;
          const mostEvidenced = idx === 0;
          return (
            <div
              key={boat.boat_id}
              data-testid={`dupe-boat-${boat.boat_id}`}
              className={`px-4 py-3 space-y-2 ${
                isWinner ? "bg-[var(--sr-dusk-interactive)]/40" : ""
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="text-[13px] font-semibold text-[var(--sr-text-primary)] truncate">
                    {boat.boat_name ?? "(unnamed)"}
                  </p>
                  <p className="admin-mono-font text-[10px] text-[var(--sr-text-tertiary)]">
                    #{boat.boat_id}
                    {boat.country ? ` · ${boat.country}` : ""}
                  </p>
                </div>
                {mostEvidenced && (
                  <span
                    data-testid={`most-evidenced-${boat.boat_id}`}
                    className="admin-mono-font text-[8px] uppercase tracking-[0.14em] text-[var(--sr-status-success)] border border-[var(--sr-status-success)]/40 rounded-full px-1.5 py-[1px] flex-shrink-0"
                  >
                    Most evidence
                  </span>
                )}
              </div>

              <dl className="space-y-1 text-[11px]">
                <Field label="Sail" value={boat.sail_number} mono />
                <Field label="Design" value={boat.design} />
                <Field label="Year" value={boat.year_built?.toString()} mono />
                <Field
                  label="Results"
                  value={boat.race_results.toString()}
                  mono
                  strong={boat.race_results > 0}
                />
                <Field
                  label="Certs"
                  value={boat.cert_count.toString()}
                  mono
                  strong={boat.cert_count > 0}
                />
                <Field label="Active" value={boat.latest_activity} mono />
                <Field label="Owner" value={boat.owner} />
              </dl>

              {/* The single Signal merge button lives on the most-evidenced
                  boat; another column's radio retargets the merge. */}
              <div className="pt-1 space-y-1.5">
                <label className="flex items-center gap-1.5 cursor-pointer">
                  <input
                    type="radio"
                    name={`winner-${cluster.cluster_id}`}
                    checked={isWinner}
                    onChange={() => setWinnerId(boat.boat_id)}
                    data-testid={`winner-radio-${boat.boat_id}`}
                    className="accent-[var(--sr-signal-500)]"
                  />
                  <span className="admin-mono-font text-[9px] uppercase tracking-[0.12em] text-[var(--sr-text-label)]">
                    Merge target
                  </span>
                </label>
                {mostEvidenced && (
                  <button
                    onClick={() =>
                      void onDecide(cluster, "MERGED", { winner_id: winnerId })
                    }
                    disabled={acting}
                    data-testid={`merge-button-${cluster.cluster_id}`}
                    className="w-full inline-flex items-center justify-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.14em] font-semibold text-white bg-[var(--sr-signal-500)] hover:bg-[var(--sr-signal-700)] rounded-[4px] px-3 py-2 transition-colors disabled:opacity-50"
                  >
                    <GitMerge size={12} strokeWidth={2} />
                    {isWinner
                      ? `Merge ${cluster.boats.length - 1} into this boat`
                      : `Merge into #${winnerId}`}
                  </button>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Card footer */}
      <footer className="flex items-center justify-between gap-3 px-4 py-3 border-t border-[var(--sr-border-subtle)] bg-[var(--sr-surface-deep)]/40">
        <div className="flex items-center gap-2">
          <label
            htmlFor={`reason-${cluster.cluster_id}`}
            className="admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-label)]"
          >
            Reason
          </label>
          <select
            id={`reason-${cluster.cluster_id}`}
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            data-testid={`reason-select-${cluster.cluster_id}`}
            className="bg-[var(--sr-surface-card)] border border-[var(--sr-border-subtle)] text-[12px] text-[var(--sr-text-primary)] rounded-[4px] px-2 py-1.5 focus:border-[var(--sr-link)] outline-none"
          >
            {reasons.map((r) => (
              <option key={r} value={r}>
                {REASON_LABELS[r] ?? r}
              </option>
            ))}
          </select>
          <button
            onClick={() => void onDecide(cluster, "NOT_DUPE", { reason })}
            disabled={acting}
            data-testid={`not-dupe-button-${cluster.cluster_id}`}
            className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.14em] text-[var(--sr-text-secondary)] hover:text-[var(--sr-text-primary)] border border-[var(--sr-border-subtle)] rounded-[4px] px-3 py-1.5 transition-colors disabled:opacity-50"
          >
            <Minus size={11} strokeWidth={2} />
            Not duplicates
          </button>
        </div>
        <button
          onClick={() => void onDecide(cluster, "SKIPPED", {})}
          disabled={acting}
          data-testid={`skip-button-${cluster.cluster_id}`}
          className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.14em] text-[var(--sr-text-tertiary)] hover:text-[var(--sr-text-secondary)] transition-colors disabled:opacity-50"
        >
          <SkipForward size={11} strokeWidth={2} />
          Skip
        </button>
      </footer>
    </section>
  );
}

function Field({
  label,
  value,
  mono = false,
  strong = false,
}: {
  label: string;
  value?: string | null;
  mono?: boolean;
  strong?: boolean;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <dt className="admin-mono-font text-[8px] uppercase tracking-[0.14em] text-[var(--sr-text-label)]">
        {label}
      </dt>
      <dd
        className={`truncate ${strong ? "text-[var(--sr-text-primary)] font-semibold" : "text-[var(--sr-text-secondary)]"} ${mono ? "admin-mono-font" : ""}`}
      >
        {value ?? <span className="text-[var(--sr-text-tertiary)]">—</span>}
      </dd>
    </div>
  );
}
