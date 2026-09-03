"use client";

/**
 * /admin/orders — Customers zone: Reports & orders (PAY-01-10).
 *
 * Every order with an honest status: an order whose Stripe checkout session
 * was never created is shown as Abandoned (the customer left before paying).
 * Paid orders can be regenerated (re-run report + PDF + email).
 */

import React, { useCallback, useEffect, useState } from "react";
import {
  ExternalLinkIcon,
  FileTextIcon,
  RefreshIcon,
  RotateCwIcon,
  SearchIcon,
} from "@/components/admin/AdminIcons";
import {
  AdminOrder,
  MONEY_CLASS,
  ORDER_STATUS_STYLE,
  OrdersResponse,
  adminFetch,
  formatDateTime,
  formatMoney,
} from "@/components/admin/customers";

const STATUS_FILTERS = ["", "abandoned", "pending", "paid", "generated", "error"] as const;

export default function AdminOrdersPage() {
  const [q, setQ] = useState("");
  const [status, setStatus] = useState<string>("");
  const [data, setData] = useState<OrdersResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [cursorStack, setCursorStack] = useState<number[]>([0]);
  const [cursor, setCursor] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (q.trim()) params.set("q", q.trim());
      if (status) params.set("status", status);
      params.set("cursor", String(cursor));
      setData(await adminFetch<OrdersResponse>(`/admin/orders?${params}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [q, status, cursor]);

  useEffect(() => {
    load();
  }, [load]);

  const regenerate = useCallback(
    async (o: AdminOrder) => {
      setBusy(o.id);
      setError(null);
      try {
        await adminFetch(`/admin/orders/${o.id}/regenerate`, { method: "POST" });
        await load();
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setBusy(null);
      }
    },
    [load],
  );

  const resetPage = useCallback(() => {
    setCursor(0);
    setCursorStack([0]);
  }, []);

  const counts = data?.status_counts ?? {};

  return (
    <div>
      <div className="flex items-end justify-between gap-4 flex-wrap mb-6">
        <div>
          <h1 className="heading-display text-3xl text-[var(--sr-text-primary)]">
            Reports &amp; orders
          </h1>
          <p className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mt-1">
            {data ? `${data.total} orders — ` : ""}
            {Object.entries(counts)
              .sort()
              .map(([k, v]) => `${v} ${ORDER_STATUS_STYLE[k]?.label?.toLowerCase() ?? k}`)
              .join(" · ")}
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)] transition-colors disabled:opacity-40"
        >
          <RefreshIcon size={12} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-2 flex-wrap mb-4">
        <form
          className="relative"
          onSubmit={(e) => {
            e.preventDefault();
            resetPage();
            load();
          }}
        >
          <SearchIcon
            size={13}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--sr-text-tertiary)]"
          />
          <input
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              resetPage();
            }}
            placeholder="Search email, boat or token…"
            className="h-9 w-64 pl-8 pr-3 bg-[var(--sr-surface-card)] border border-[var(--sr-border-strong)] rounded-[3px] text-[13px] text-[var(--sr-text-primary)] placeholder:text-[var(--sr-text-tertiary)] focus:outline-none focus:border-[var(--sr-marine-400)]"
          />
        </form>

        {STATUS_FILTERS.map((s) => {
          const active = status === s;
          const label = s === "" ? "All" : ORDER_STATUS_STYLE[s]?.label ?? s;
          const count = s === "" ? data?.total : counts[s];
          return (
            <button
              key={s || "all"}
              onClick={() => {
                setStatus(s);
                resetPage();
              }}
              className={`h-9 px-3 admin-mono-font text-[10px] uppercase tracking-[0.12em] border rounded-[3px] transition-colors ${
                active
                  ? "border-[var(--sr-marine-400)]/60 text-[var(--sr-text-primary)] bg-[var(--sr-surface-interactive)]"
                  : "border-[var(--sr-border-strong)] text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)]"
              }`}
            >
              {label}
              {typeof count === "number" ? ` (${count})` : ""}
            </button>
          );
        })}
      </div>

      {error && (
        <div className="mb-4 px-4 py-3 border border-[var(--sr-signal-500)]/40 rounded-[6px] text-[13px] text-[var(--sr-signal-500)]">
          {error}
        </div>
      )}

      {/* Orders table */}
      <div className="admin-table-container">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="admin-table-header admin-mono-font text-[9px] uppercase tracking-[0.14em] text-left">
              <th className="px-4 py-2.5">Created</th>
              <th className="px-4 py-2.5">Boat</th>
              <th className="px-4 py-2.5">Email</th>
              <th className="px-4 py-2.5 text-right">Amount</th>
              <th className="px-4 py-2.5">Status</th>
              <th className="px-4 py-2.5">Search query</th>
              <th className="px-4 py-2.5 text-right">Actions</th>
            </tr>
          </thead>
          <tbody>
            {data?.orders.map((o) => (
              <tr key={o.id} className="admin-table-row" data-testid={`order-row-${o.id}`}>
                <td className="px-4 py-3 admin-mono-font text-[11px] text-[var(--sr-text-secondary)] whitespace-nowrap">
                  {formatDateTime(o.created_at)}
                </td>
                <td className="px-4 py-3">
                  <a
                    href={`/boat/${o.boat_id}`}
                    className="text-[var(--sr-link)] hover:text-[var(--sr-link-hover)]"
                  >
                    {o.boat_name ?? `#${o.boat_id}`}
                  </a>
                </td>
                <td className="px-4 py-3 text-[var(--sr-text-secondary)]">
                  {o.email ?? <span className="text-[var(--sr-text-tertiary)]">—</span>}
                </td>
                <td className={`px-4 py-3 text-right admin-mono-font ${MONEY_CLASS}`}>
                  {formatMoney(o.amount)}
                </td>
                <td className="px-4 py-3">
                  <span
                    className={`admin-mono-font text-[9px] uppercase tracking-[0.1em] px-1.5 py-0.5 border rounded-[3px] ${
                      ORDER_STATUS_STYLE[o.status]?.cls ?? ""
                    }`}
                  >
                    {ORDER_STATUS_STYLE[o.status]?.label ?? o.status}
                  </span>
                </td>
                <td className="px-4 py-3 text-[11px] text-[var(--sr-text-tertiary)] max-w-[220px] truncate">
                  {o.search_query ?? "—"}
                </td>
                <td className="px-4 py-3 text-right">
                  <div className="inline-flex items-center gap-2">
                    {o.report_url && (
                      <a
                        href={o.report_url}
                        target="_blank"
                        rel="noreferrer"
                        title="Open report"
                        className="p-1.5 border border-[var(--sr-border-strong)] text-[var(--sr-text-secondary)] rounded-[3px] hover:text-[var(--sr-text-primary)]"
                      >
                        <FileTextIcon size={12} />
                      </a>
                    )}
                    {o.stripe_dashboard_url && (
                      <a
                        href={o.stripe_dashboard_url}
                        target="_blank"
                        rel="noreferrer"
                        title="Open in Stripe"
                        className="p-1.5 border border-[var(--sr-border-strong)] text-[var(--sr-text-secondary)] rounded-[3px] hover:text-[var(--sr-text-primary)]"
                      >
                        <ExternalLinkIcon size={12} />
                      </a>
                    )}
                    {(o.status === "paid" || o.status === "generated" || o.status === "error") && (
                      <button
                        onClick={() => regenerate(o)}
                        disabled={busy === o.id}
                        title="Regenerate report"
                        className="p-1.5 border border-[var(--sr-marine-400)]/50 text-[var(--sr-marine-200)] rounded-[3px] hover:bg-[var(--sr-marine-400)]/10 disabled:opacity-40"
                      >
                        <RotateCwIcon size={12} className={busy === o.id ? "animate-spin" : ""} />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {data && data.orders.length === 0 && (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center text-[13px] text-[var(--sr-text-tertiary)]">
                  No orders match these filters.
                </td>
              </tr>
            )}
            {!data && !error && (
              <tr>
                <td colSpan={7} className="px-4 py-10 text-center admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
                  Loading orders…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {data && (cursor > 0 || data.next_cursor !== null) && (
        <div className="flex items-center justify-end gap-2 mt-3">
          <button
            disabled={cursorStack.length <= 1}
            onClick={() => {
              const stack = [...cursorStack];
              stack.pop();
              const prev = stack[stack.length - 1] ?? 0;
              setCursorStack(stack);
              setCursor(prev);
            }}
            className="h-8 px-3 admin-mono-font text-[10px] uppercase tracking-[0.12em] border border-[var(--sr-border-strong)] text-[var(--sr-text-secondary)] rounded-[3px] hover:text-[var(--sr-text-primary)] disabled:opacity-40"
          >
            Prev
          </button>
          <button
            disabled={data.next_cursor === null}
            onClick={() => {
              if (data.next_cursor === null) return;
              setCursorStack([...cursorStack, data.next_cursor]);
              setCursor(data.next_cursor);
            }}
            className="h-8 px-3 admin-mono-font text-[10px] uppercase tracking-[0.12em] border border-[var(--sr-border-strong)] text-[var(--sr-text-secondary)] rounded-[3px] hover:text-[var(--sr-text-primary)] disabled:opacity-40"
          >
            Next
          </button>
        </div>
      )}
    </div>
  );
}
