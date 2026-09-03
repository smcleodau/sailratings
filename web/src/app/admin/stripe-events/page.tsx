"use client";

/**
 * /admin/stripe-events — visibility into the Stripe webhook ledger
 * (PAY-01-09). Shows every event received at POST /v1/checkout/webhook;
 * "parked" events are subscription events that could not be matched to a
 * user and need manual attention.
 */

import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, CreditCard, Loader2, RefreshCw } from "lucide-react";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

interface StripeEventRow {
  id: number;
  event_id: string;
  type: string | null;
  livemode: boolean;
  created_at: string | null;
  processed_at: string | null;
  error: string | null;
}

interface StripeEventsResponse {
  counts: { total: number; parked: number; failed: number };
  events: StripeEventRow[];
}

function fmtDate(iso: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function StateBadge({ row }: { row: StripeEventRow }) {
  if (row.error?.startsWith("parked:")) {
    return (
      <span className="admin-mono-font text-[9px] uppercase tracking-[0.14em] px-2 py-0.5 border rounded-[2px] text-[var(--sr-status-warning)] border-[var(--sr-status-warning)]/40 bg-[var(--sr-status-warning)]/5">
        parked
      </span>
    );
  }
  if (row.error) {
    return (
      <span className="admin-mono-font text-[9px] uppercase tracking-[0.14em] px-2 py-0.5 border rounded-[2px] text-[var(--sr-action-pressed)] border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/5">
        failed
      </span>
    );
  }
  if (row.processed_at) {
    return (
      <span className="admin-mono-font text-[9px] uppercase tracking-[0.14em] px-2 py-0.5 border rounded-[2px] text-[var(--sr-status-success)] border-[var(--sr-status-success)]/40 bg-[var(--sr-status-success)]/5">
        processed
      </span>
    );
  }
  return (
    <span className="admin-mono-font text-[9px] uppercase tracking-[0.14em] px-2 py-0.5 border rounded-[2px] text-[var(--sr-text-label)] border-[var(--sr-border-subtle)]">
      received
    </span>
  );
}

export default function StripeEventsPage() {
  const [token, setToken] = useState<string | null>(null);
  const [data, setData] = useState<StripeEventsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [parkedOnly, setParkedOnly] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const t =
      localStorage.getItem("admin_token") ||
      process.env.NEXT_PUBLIC_ADMIN_PASSWORD ||
      "";
    if (t) setToken(t);
  }, []);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${API_BASE}/admin/stripe-events?parked_only=${parkedOnly}`,
        { headers: { Authorization: `Bearer ${token}` } }
      );
      if (res.status === 401) {
        localStorage.removeItem("admin_token");
        setToken(null);
        return;
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setData(await res.json());
    } catch (e: any) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }, [token, parkedOnly]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="heading-display text-xl text-[var(--sr-text-primary)] flex items-center gap-2">
            <CreditCard size={18} /> Stripe webhook events
          </h1>
          <p className="text-[12px] text-[var(--sr-text-tertiary)] mt-1">
            Idempotency ledger for POST /v1/checkout/webhook. Parked events
            are subscription events with no matching user — link the user, then
            ask Stripe to resend the event.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="flex items-center gap-2 text-[12px] text-[var(--sr-text-secondary)]">
            <input
              type="checkbox"
              checked={parkedOnly}
              onChange={(e) => setParkedOnly(e.target.checked)}
            />
            Parked only
          </label>
          <button
            onClick={load}
            disabled={loading}
            className="inline-flex items-center gap-1.5 text-[12px] px-3 py-1.5 border border-[var(--sr-border-subtle)] rounded-[3px] text-[var(--sr-text-primary)] hover:bg-[var(--sr-surface-interactive)]/40 disabled:opacity-50"
          >
            {loading ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
            Refresh
          </button>
        </div>
      </div>

      {data && (
        <div className="grid grid-cols-3 gap-3 max-w-xl">
          <div className="border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] px-4 py-3">
            <div className="heading-display text-2xl text-[var(--sr-text-primary)]">
              {data.counts.total}
            </div>
            <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mt-1">
              Total events
            </div>
          </div>
          <div className="border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] px-4 py-3">
            <div className="heading-display text-2xl text-[var(--sr-status-warning)]">
              {data.counts.parked}
            </div>
            <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mt-1">
              Parked
            </div>
          </div>
          <div className="border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] rounded-[4px] px-4 py-3">
            <div className="heading-display text-2xl text-[var(--sr-action-pressed)]">
              {data.counts.failed}
            </div>
            <div className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mt-1">
              Failed
            </div>
          </div>
        </div>
      )}

      {error && (
        <p className="text-[12px] text-[var(--sr-action-pressed)] flex items-center gap-1">
          <AlertTriangle size={13} /> {error}
        </p>
      )}

      <div className="border border-[var(--sr-border-subtle)] rounded-[4px] overflow-x-auto bg-[var(--sr-surface-card)]">
        <table className="w-full text-[12px]">
          <thead>
            <tr className="text-left admin-mono-font text-[9px] uppercase tracking-[0.14em] text-[var(--sr-text-label)] border-b border-[var(--sr-border-subtle)]">
              <th className="px-3 py-2">Event</th>
              <th className="px-3 py-2">Type</th>
              <th className="px-3 py-2">Mode</th>
              <th className="px-3 py-2">State</th>
              <th className="px-3 py-2">Received</th>
              <th className="px-3 py-2">Processed</th>
              <th className="px-3 py-2">Error</th>
            </tr>
          </thead>
          <tbody>
            {data?.events?.length ? (
              data.events.map((e) => (
                <tr
                  key={e.id}
                  className="border-b border-[var(--sr-border-subtle)]/60 last:border-0"
                >
                  <td className="px-3 py-2 admin-mono-font text-[11px] text-[var(--sr-text-primary)]">
                    {e.event_id}
                  </td>
                  <td className="px-3 py-2 text-[var(--sr-text-secondary)]">
                    {e.type ?? "—"}
                  </td>
                  <td className="px-3 py-2 admin-mono-font text-[11px] text-[var(--sr-text-tertiary)]">
                    {e.livemode ? "live" : "test"}
                  </td>
                  <td className="px-3 py-2">
                    <StateBadge row={e} />
                  </td>
                  <td className="px-3 py-2 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                    {fmtDate(e.created_at)}
                  </td>
                  <td className="px-3 py-2 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                    {e.processed_at ? (
                      fmtDate(e.processed_at)
                    ) : (
                      <span className="text-[var(--sr-text-tertiary)]">—</span>
                    )}
                  </td>
                  <td className="px-3 py-2 text-[11px] text-[var(--sr-action-pressed)] max-w-[320px] truncate">
                    {e.error ?? (
                      <span className="inline-flex items-center gap-1 text-[var(--sr-text-tertiary)]">
                        <CheckCircle2 size={11} /> —
                      </span>
                    )}
                  </td>
                </tr>
              ))
            ) : (
              <tr>
                <td
                  colSpan={7}
                  className="px-3 py-6 text-center text-[var(--sr-text-tertiary)]"
                >
                  {loading
                    ? "Loading…"
                    : parkedOnly
                      ? "No parked events — every subscription event matched a user."
                      : "No Stripe events received yet."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
