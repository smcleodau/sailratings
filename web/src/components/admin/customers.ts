"use client";

/**
 * Shared helpers for the Customers zone pages (PAY-01-10):
 * /admin/users, /admin/orders, /admin/billing.
 *
 * Money rule (design system): money is rendered in Starboard green,
 * never Signal red — Signal is reserved for ratings/CTAs.
 */

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

export interface Money {
  amount_cents: number;
  currency: string;
}

export interface AdminUser {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
  plan: string;
  subscription_status: string;
  stripe_customer_id: string | null;
  boats_claimed: number;
  pending_claims: number;
  reports_bought: number;
  total_spend: Money | null;
  joined_at: string | null;
  last_seen_at: string | null;
  stripe_dashboard_url: string | null;
}

export interface AdminOrder {
  id: number;
  order_token: string;
  status: "abandoned" | "pending" | "paid" | "generated" | "error";
  stored_status: string;
  email: string | null;
  user_id: string | null;
  boat_id: number;
  boat_name: string | null;
  amount: Money | null;
  stripe_session_id: string | null;
  stripe_payment_intent: string | null;
  stripe_dashboard_url: string | null;
  search_query: string | null;
  report_url: string | null;
  created_at: string | null;
  paid_at: string | null;
  report_generated_at: string | null;
  email_sent_at: string | null;
}

export interface UsersResponse {
  users: AdminUser[];
  next_cursor: number | null;
  total: number;
}

export interface OrdersResponse {
  orders: AdminOrder[];
  next_cursor: number | null;
  total: number;
  status_counts: Record<string, number>;
}

export interface CataloguePrice {
  price_id: string;
  product_id: string;
  product_name: string | null;
  lookup_key: string | null;
  unit_amount: Money | null;
  recurring: { interval: string; interval_count: number } | null;
  active: boolean;
  stripe_dashboard_url: string;
}

export interface BillingResponse {
  configured: boolean;
  cached?: boolean;
  catalogue: CataloguePrice[];
  promo_codes: {
    code: string;
    active: boolean;
    percent_off: number | null;
    amount_off: Money | null;
    times_redeemed: number | null;
    expires_at: number | null;
  }[];
  balance: { available: (Money | null)[]; pending: (Money | null)[] };
  last_charges: {
    id: string;
    amount: Money | null;
    status: string;
    paid: boolean;
    refunded: boolean;
    description: string | null;
    receipt_email: string | null;
    customer_id: string | null;
    created: number | null;
    stripe_dashboard_url: string;
  }[];
}

export function getAdminToken(): string | null {
  if (typeof window === "undefined") return null;
  const t =
    localStorage.getItem("admin_token") ||
    process.env.NEXT_PUBLIC_ADMIN_PASSWORD ||
    null;
  if (t) localStorage.setItem("admin_token", t);
  return t;
}

export async function adminFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getAdminToken();
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      ...(init?.headers || {}),
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
  });
  if (res.status === 401) {
    localStorage.removeItem("admin_token");
    throw new Error("Session expired — reload to sign in again");
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => "");
    throw new Error(`HTTP ${res.status}${detail ? ` — ${detail.slice(0, 200)}` : ""}`);
  }
  return res.json();
}

/* ── Formatting ────────────────────────────────────────────────────────── */

const CURRENCY_SYMBOLS: Record<string, string> = {
  usd: "$",
  gbp: "£",
  eur: "€",
  aud: "A$",
};

/** Money in Starboard, never Signal — caller applies the class. */
export function formatMoney(m: Money | null | undefined): string {
  if (!m) return "—";
  const sym = CURRENCY_SYMBOLS[(m.currency || "usd").toLowerCase()] ?? `${m.currency.toUpperCase()} `;
  const value = m.amount_cents / 100;
  return `${sym}${value.toLocaleString(undefined, {
    minimumFractionDigits: value % 1 === 0 ? 0 : 2,
    maximumFractionDigits: 2,
  })}`;
}

export const MONEY_CLASS = "text-[var(--sr-starboard)]";

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).slice(0, 10);
  return d.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso);
  return d.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "never";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  if (s < 86400 * 30) return `${Math.floor(s / 86400)}d ago`;
  return formatDate(iso);
}

export const PLAN_BADGE: Record<string, string> = {
  pro: "border-[var(--sr-starboard)]/50 text-[var(--sr-starboard)]",
  skipper: "border-[var(--sr-marine-400)]/50 text-[var(--sr-marine-200)]",
  free: "border-[var(--sr-border-strong)] text-[var(--sr-text-tertiary)]",
};

export const ROLE_BADGE: Record<string, string> = {
  admin: "border-[var(--sr-buoy,#e8a33d)]/50 text-[var(--sr-buoy,#e8a33d)]",
  staff: "border-[var(--sr-marine-400)]/50 text-[var(--sr-marine-200)]",
  customer: "border-[var(--sr-border-strong)] text-[var(--sr-text-tertiary)]",
};

export const ORDER_STATUS_STYLE: Record<string, { label: string; cls: string }> = {
  generated: { label: "Delivered", cls: "border-[var(--sr-starboard)]/50 text-[var(--sr-starboard)]" },
  paid: { label: "Paid", cls: "border-[var(--sr-starboard)]/50 text-[var(--sr-starboard)]" },
  pending: { label: "Checkout open", cls: "border-[var(--sr-buoy,#e8a33d)]/50 text-[var(--sr-buoy,#e8a33d)]" },
  abandoned: { label: "Abandoned", cls: "border-[var(--sr-border-strong)] text-[var(--sr-text-tertiary)]" },
  error: { label: "Error", cls: "border-[var(--sr-signal-500)]/50 text-[var(--sr-signal-500)]" },
};
