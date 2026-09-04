"use client";

/**
 * /admin/identity — the human adjudication queue (DP-04-05).
 *
 * Prioritised queue of uncertain / high-impact identity-match candidates.
 * Each case renders as a MatchCard (AD-01-04): side-by-side source
 * evidence, score explanation, downstream impact, and reversible actions.
 * Decisions write through the shared DecisionRequestV1 contract — the
 * same contract the automatic resolver uses — and high-impact merges
 * require a second, distinct reviewer (enforced by the backend).
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  RefreshIcon,
} from "@/components/admin/AdminIcons";
import {
  MatchCard,
  type AdjudicationAction,
  type MatchCardData,
} from "./MatchCard";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

interface ResolutionRecord {
  resolution_id: string;
  case_id: string;
  decision: string;
  status: string;
  decided_by: string;
  decided_at: string;
  decided_by_chain: string[];
  undo_of: string | null;
}

export default function IdentityAdjudicationPage() {
  const [token, setToken] = useState<string | null>(null);
  const [reviewer, setReviewer] = useState<string>("");
  const [queue, setQueue] = useState<MatchCardData[]>([]);
  const [resolutions, setResolutions] = useState<ResolutionRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [acting, setActing] = useState<Set<string>>(new Set());

  useEffect(() => {
    const stored =
      (typeof window !== "undefined" ? localStorage.getItem("admin_token") : null) ||
      process.env.NEXT_PUBLIC_ADMIN_PASSWORD ||
      "sailfast2026";
    if (stored) {
      if (typeof window !== "undefined") localStorage.setItem("admin_token", stored);
      setToken(stored);
    }
    const who =
      (typeof window !== "undefined" ? localStorage.getItem("admin_reviewer") : null) || "";
    setReviewer(who);
  }, []);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const [qRes, rRes] = await Promise.all([
        fetch(`${API_BASE}/admin/adjudication/queue?limit=100`, {
          headers: { Authorization: `Bearer ${token}` },
        }),
        fetch(`${API_BASE}/admin/adjudication/resolutions?limit=100`, {
          headers: { Authorization: `Bearer ${token}` },
        }),
      ]);
      if (qRes.status === 401) {
        localStorage.removeItem("admin_token");
        setToken(null);
        return;
      }
      if (!qRes.ok) throw new Error(`Queue load failed: ${qRes.status}`);
      setQueue(await qRes.json());
      if (rRes.ok) setResolutions(await rRes.json());
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load queue");
    } finally {
      setLoading(false);
    }
  }, [token]);

  useEffect(() => {
    void load();
  }, [load]);

  const decidedBy = useMemo(
    () => `human:${reviewer.trim() || "anonymous"}`,
    [reviewer],
  );

  const decide = async (caseId: string, action: AdjudicationAction) => {
    if (!token) return;
    setActing((prev) => new Set(prev).add(caseId));
    setNotice(null);
    try {
      const res = await fetch(`${API_BASE}/admin/adjudication/decide`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          case_id: caseId,
          decision: action,
          decided_by: decidedBy,
        }),
      });
      if (res.status === 409) {
        const detail = (await res.json()).detail;
        setError(detail || "Decision conflict");
        return;
      }
      if (!res.ok) throw new Error(`Decision failed: ${res.status}`);
      const record: ResolutionRecord = await res.json();
      if (record.status === "pending_second_review") {
        setNotice(
          "First review recorded — a second, distinct reviewer must confirm this high-impact merge.",
        );
      }
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Decision failed");
    } finally {
      setActing((prev) => {
        const next = new Set(prev);
        next.delete(caseId);
        return next;
      });
    }
  };

  const reverse = async (resolutionId: string) => {
    if (!token) return;
    setNotice(null);
    try {
      const res = await fetch(`${API_BASE}/admin/adjudication/reverse`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          resolution_id: resolutionId,
          decided_by: decidedBy,
          rationale: "undo from adjudication queue",
        }),
      });
      if (!res.ok) throw new Error(`Undo failed: ${res.status}`);
      setNotice("Decision reversed — the case has been requeued.");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Undo failed");
    }
  };

  const lastResolutionFor = (caseId: string): string | null => {
    const forCase = resolutions.filter(
      (r) => r.case_id === caseId && r.status === "applied" && !r.undo_of,
    );
    return forCase.length ? forCase[forCase.length - 1].resolution_id : null;
  };

  return (
    <main className="mx-auto max-w-5xl px-6 py-8 space-y-6">
      <header className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-[var(--sr-text-primary)]">
            Identity adjudication
          </h1>
          <p className="text-sm text-[var(--sr-text-secondary)] mt-1 max-w-2xl">
            Uncertain and high-impact match candidates, highest cost first.
            Humans see a case only where uncertainty or cost warrants it —
            everything else is resolved automatically. Every action is
            reversible; high-impact merges need two distinct reviewers.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-xs text-[var(--sr-text-secondary)]">
            Reviewer
            <input
              value={reviewer}
              onChange={(e) => {
                setReviewer(e.target.value);
                if (typeof window !== "undefined") {
                  localStorage.setItem("admin_reviewer", e.target.value);
                }
              }}
              placeholder="your name"
              className="rounded-md border border-[var(--sr-marine-600)]/40 bg-transparent px-2 py-1 text-[var(--sr-text-primary)] w-36"
            />
          </label>
          <button
            type="button"
            onClick={() => void load()}
            className="flex items-center gap-2 rounded-lg border border-[var(--sr-marine-600)]/40 px-3 py-2 text-xs text-[var(--sr-text-primary)] hover:bg-[var(--sr-marine-600)]/20"
          >
            <RefreshIcon size={13} className={loading ? "animate-spin" : ""} />
            Refresh
          </button>
        </div>
      </header>

      {error && (
        <div className="rounded-lg border border-[var(--sr-status-danger)]/40 bg-[var(--sr-status-danger)]/10 px-4 py-3 text-sm text-[var(--sr-status-danger)]">
          {error}
        </div>
      )}
      {notice && (
        <div className="rounded-lg border border-[var(--sr-status-warning)]/40 bg-[var(--sr-status-warning)]/10 px-4 py-3 text-sm text-[var(--sr-status-warning)]">
          {notice}
        </div>
      )}

      <section className="space-y-4">
        <div className="text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-secondary)]">
          {queue.length} open case{queue.length === 1 ? "" : "s"} — prioritised
          by impact, then uncertainty
        </div>
        {queue.length === 0 && !loading && (
          <div className="rounded-xl border border-[var(--sr-marine-600)]/30 px-6 py-12 text-center text-sm text-[var(--sr-text-secondary)]">
            Queue is clear — no uncertain or high-impact candidates pending.
          </div>
        )}
        {queue.map((item, idx) => (
          <MatchCard
            key={item.case_id}
            item={item}
            acting={acting.has(item.case_id)}
            isTop={idx === 0}
            onDecide={decide}
            onReverse={reverse}
            lastResolutionId={lastResolutionFor(item.case_id)}
          />
        ))}
      </section>
    </main>
  );
}
