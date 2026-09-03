"use client";

/**
 * /admin/billing — Customers zone: Stripe & pricing (PAY-01-10).
 *
 * Live catalogue by lookup_key (pro_monthly_gbp, pro_annual_gbp, …), promo
 * codes, account balance and the last 20 charges. Server caches Stripe for
 * 60 s. Money is rendered in Starboard green, never Signal red.
 */

import React, { useCallback, useEffect, useState } from "react";
import {
  CreditCardIcon,
  ExternalLinkIcon,
  RefreshIcon,
  TagIcon,
  WalletIcon,
} from "@/components/admin/AdminIcons";
import {
  BillingResponse,
  MONEY_CLASS,
  adminFetch,
  formatMoney,
} from "@/components/admin/customers";

function planLabel(lookupKey: string | null, productName: string | null): string {
  if (lookupKey) {
    return lookupKey
      .replace(/_/g, " ")
      .replace(/\b\w/g, (ch) => ch.toUpperCase());
  }
  return productName ?? "—";
}

function intervalLabel(recurring: { interval: string; interval_count: number } | null): string {
  if (!recurring) return "one-off";
  if (recurring.interval === "year") return "per year";
  if (recurring.interval === "month") return "per month";
  return `per ${recurring.interval}`;
}

export default function AdminBillingPage() {
  const [data, setData] = useState<BillingResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setData(await adminFetch<BillingResponse>("/admin/billing"));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const plans =
    data?.catalogue.filter((p) => p.recurring && p.active) ??
    [];
  const oneOffs =
    data?.catalogue.filter((p) => !p.recurring && p.active) ??
    [];

  return (
    <div>
      <div className="flex items-end justify-between gap-4 flex-wrap mb-6">
        <div>
          <h1 className="heading-display text-3xl text-[var(--sr-text-primary)]">
            Stripe &amp; pricing
          </h1>
          <p className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mt-1">
            Live catalogue, promo codes, balance and recent charges
            {data?.cached ? " (cached ≤60 s)" : ""}
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

      {error && (
        <div className="mb-4 px-4 py-3 border border-[var(--sr-signal-500)]/40 rounded-[6px] text-[13px] text-[var(--sr-signal-500)]">
          {error}
        </div>
      )}

      {data && !data.configured && (
        <div className="mb-4 px-4 py-3 border border-[var(--sr-buoy)]/40 rounded-[6px] text-[13px] text-[var(--sr-buoy)]">
          Stripe is not configured on this environment (no STRIPE_SECRET_KEY).
          Catalogue, balance and charges will be empty.
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 mb-6">
        {/* Plans */}
        <section className="lg:col-span-2">
          <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-3 flex items-center gap-2">
            <CreditCardIcon size={12} /> Plans (live catalogue)
          </h2>
          <div className="admin-table-container">
            <table className="w-full text-[13px]">
              <thead>
                <tr className="admin-table-header admin-mono-font text-[9px] uppercase tracking-[0.14em] text-left">
                  <th className="px-4 py-2.5">Plan</th>
                  <th className="px-4 py-2.5">Lookup key</th>
                  <th className="px-4 py-2.5 text-right">Price</th>
                  <th className="px-4 py-2.5">Cadence</th>
                  <th className="px-4 py-2.5 w-10" />
                </tr>
              </thead>
              <tbody>
                {plans.map((p) => (
                  <tr key={p.price_id} className="admin-table-row" data-testid={`plan-${p.lookup_key}`}>
                    <td className="px-4 py-3 text-[var(--sr-text-primary)] font-medium">
                      {planLabel(p.lookup_key, p.product_name)}
                    </td>
                    <td className="px-4 py-3 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                      {p.lookup_key ?? "—"}
                    </td>
                    <td className={`px-4 py-3 text-right admin-mono-font ${MONEY_CLASS}`}>
                      {formatMoney(p.unit_amount)}
                    </td>
                    <td className="px-4 py-3 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                      {intervalLabel(p.recurring)}
                    </td>
                    <td className="px-4 py-3">
                      <a
                        href={p.stripe_dashboard_url}
                        target="_blank"
                        rel="noreferrer"
                        title="Open in Stripe"
                        className="text-[var(--sr-link)] hover:text-[var(--sr-link-hover)]"
                      >
                        <ExternalLinkIcon size={12} />
                      </a>
                    </td>
                  </tr>
                ))}
                {oneOffs.map((p) => (
                  <tr key={p.price_id} className="admin-table-row" data-testid={`plan-${p.lookup_key}`}>
                    <td className="px-4 py-3 text-[var(--sr-text-primary)] font-medium">
                      {planLabel(p.lookup_key, p.product_name)}
                    </td>
                    <td className="px-4 py-3 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                      {p.lookup_key ?? "—"}
                    </td>
                    <td className={`px-4 py-3 text-right admin-mono-font ${MONEY_CLASS}`}>
                      {formatMoney(p.unit_amount)}
                    </td>
                    <td className="px-4 py-3 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                      one-off
                    </td>
                    <td className="px-4 py-3">
                      <a
                        href={p.stripe_dashboard_url}
                        target="_blank"
                        rel="noreferrer"
                        title="Open in Stripe"
                        className="text-[var(--sr-link)] hover:text-[var(--sr-link-hover)]"
                      >
                        <ExternalLinkIcon size={12} />
                      </a>
                    </td>
                  </tr>
                ))}
                {data && data.catalogue.length === 0 && (
                  <tr>
                    <td colSpan={5} className="px-4 py-8 text-center text-[13px] text-[var(--sr-text-tertiary)]">
                      No active prices in the Stripe catalogue.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        {/* Balance + promo codes */}
        <section className="space-y-6">
          <div>
            <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-3 flex items-center gap-2">
              <WalletIcon size={12} /> Balance
            </h2>
            <div className="admin-table-container px-4 py-4 space-y-2">
              <div className="flex items-center justify-between text-[13px]">
                <span className="text-[var(--sr-text-secondary)]">Available</span>
                <span className={`admin-mono-font text-[15px] ${MONEY_CLASS}`}>
                  {data?.balance.available.filter(Boolean).map(formatMoney).join(" · ") || "—"}
                </span>
              </div>
              <div className="flex items-center justify-between text-[13px]">
                <span className="text-[var(--sr-text-secondary)]">Pending</span>
                <span className="admin-mono-font text-[13px] text-[var(--sr-text-tertiary)]">
                  {data?.balance.pending.filter(Boolean).map(formatMoney).join(" · ") || "—"}
                </span>
              </div>
            </div>
          </div>

          <div>
            <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-3 flex items-center gap-2">
              <TagIcon size={12} /> Promo codes ({data?.promo_codes.length ?? 0})
            </h2>
            <div className="admin-table-container">
              {data && data.promo_codes.length === 0 ? (
                <p className="px-4 py-6 text-[12px] text-[var(--sr-text-tertiary)] text-center">
                  No active promo codes.
                </p>
              ) : (
                <ul className="divide-y divide-[var(--sr-border-subtle)]">
                  {data?.promo_codes.map((pc) => (
                    <li key={pc.code} className="px-4 py-2.5 flex items-center justify-between text-[12px]">
                      <span className="admin-mono-font text-[var(--sr-text-primary)]">{pc.code}</span>
                      <span className={`admin-mono-font ${MONEY_CLASS}`}>
                        {pc.percent_off != null
                          ? `${pc.percent_off}% off`
                          : pc.amount_off
                            ? `${formatMoney(pc.amount_off)} off`
                            : "—"}
                        {typeof pc.times_redeemed === "number" && (
                          <span className="text-[var(--sr-text-tertiary)]">
                            {" "}· {pc.times_redeemed}×
                          </span>
                        )}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
        </section>
      </div>

      {/* Last charges */}
      <section>
        <h2 className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-3">
          Last {data?.last_charges.length ?? 0} charges
        </h2>
        <div className="admin-table-container">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="admin-table-header admin-mono-font text-[9px] uppercase tracking-[0.14em] text-left">
                <th className="px-4 py-2.5">When</th>
                <th className="px-4 py-2.5">Customer</th>
                <th className="px-4 py-2.5">Description</th>
                <th className="px-4 py-2.5 text-right">Amount</th>
                <th className="px-4 py-2.5">Status</th>
                <th className="px-4 py-2.5 w-10" />
              </tr>
            </thead>
            <tbody>
              {data?.last_charges.map((ch) => (
                <tr key={ch.id} className="admin-table-row">
                  <td className="px-4 py-2.5 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                    {ch.created ? new Date(ch.created * 1000).toLocaleString() : "—"}
                  </td>
                  <td className="px-4 py-2.5 text-[var(--sr-text-secondary)]">
                    {ch.receipt_email ?? ch.customer_id ?? "—"}
                  </td>
                  <td className="px-4 py-2.5 text-[var(--sr-text-tertiary)] max-w-[260px] truncate">
                    {ch.description ?? "—"}
                  </td>
                  <td className={`px-4 py-2.5 text-right admin-mono-font ${MONEY_CLASS}`}>
                    {ch.refunded ? <s>{formatMoney(ch.amount)}</s> : formatMoney(ch.amount)}
                  </td>
                  <td className="px-4 py-2.5">
                    <span
                      className={`admin-mono-font text-[9px] uppercase tracking-[0.1em] px-1.5 py-0.5 border rounded-[3px] ${
                        ch.refunded
                          ? "border-[var(--sr-buoy)]/50 text-[var(--sr-buoy)]"
                          : ch.paid
                            ? "border-[var(--sr-starboard)]/50 text-[var(--sr-starboard)]"
                            : "border-[var(--sr-border-strong)] text-[var(--sr-text-tertiary)]"
                      }`}
                    >
                      {ch.refunded ? "refunded" : ch.status}
                    </span>
                  </td>
                  <td className="px-4 py-2.5">
                    <a
                      href={ch.stripe_dashboard_url}
                      target="_blank"
                      rel="noreferrer"
                      title="Open in Stripe"
                      className="text-[var(--sr-link)] hover:text-[var(--sr-link-hover)]"
                    >
                      <ExternalLinkIcon size={12} />
                    </a>
                  </td>
                </tr>
              ))}
              {data && data.last_charges.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-[13px] text-[var(--sr-text-tertiary)]">
                    No charges yet.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
