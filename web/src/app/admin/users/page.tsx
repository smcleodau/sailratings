"use client";

/**
 * /admin/users — Customers zone: Users & plans (PAY-01-10).
 *
 * Every customer, their plan, their boats and their money. Backed by
 * GET /v1/admin/users (v_admin_users) with q / plan / role / claims filters
 * and cursor pagination. Per-user actions: role change, claim verify/reject,
 * Open in Stripe.
 */

import React, { useCallback, useEffect, useState } from "react";
import {
  Anchor,
  Check,
  ChevronDown,
  ChevronRight,
  ExternalLink,
  RefreshCw,
  Search,
  Ship,
  X,
} from "lucide-react";
import {
  AdminOrder,
  AdminUser,
  MONEY_CLASS,
  ORDER_STATUS_STYLE,
  PLAN_BADGE,
  ROLE_BADGE,
  UsersResponse,
  adminFetch,
  formatDate,
  formatMoney,
  timeAgo,
} from "@/components/admin/customers";

/* ── Detail types ──────────────────────────────────────────────────────── */

interface UserBoat {
  claim_id: number;
  boat_id: number;
  boat_name: string | null;
  sail_number: string | null;
  design: string | null;
  country: string | null;
  status: string;
  evidence: string | null;
  claimed_at: string | null;
  verified_at: string | null;
}

interface UserDetail {
  user: AdminUser;
  boats: UserBoat[];
  orders: AdminOrder[];
  claims: {
    id: number;
    user_id: string;
    boat_id: number;
    boat_name: string | null;
    sail_number: string | null;
    status: string;
    evidence: string | null;
    verified_by: string | null;
    verified_at: string | null;
    created_at: string | null;
  }[];
}

const ROLES = ["customer", "staff", "admin"] as const;

/* ── Page ──────────────────────────────────────────────────────────────── */

export default function AdminUsersPage() {
  const [q, setQ] = useState("");
  const [plan, setPlan] = useState("");
  const [role, setRole] = useState("");
  const [claimsOnly, setClaimsOnly] = useState(false);
  const [data, setData] = useState<UsersResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [detail, setDetail] = useState<UserDetail | null>(null);
  const [cursorStack, setCursorStack] = useState<number[]>([0]);
  const [cursor, setCursor] = useState(0);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams();
      if (q.trim()) params.set("q", q.trim());
      if (plan) params.set("plan", plan);
      if (role) params.set("role", role);
      if (claimsOnly) params.set("claims", "pending");
      params.set("cursor", String(cursor));
      setData(await adminFetch<UsersResponse>(`/admin/users?${params}`));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [q, plan, role, claimsOnly, cursor]);

  useEffect(() => {
    load();
  }, [load]);

  const toggle = useCallback(
    async (id: string) => {
      if (expanded === id) {
        setExpanded(null);
        setDetail(null);
        return;
      }
      setExpanded(id);
      setDetail(null);
      try {
        setDetail(await adminFetch<UserDetail>(`/admin/users/${id}`));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [expanded],
  );

  const setUserRole = useCallback(
    async (id: string, newRole: string) => {
      try {
        await adminFetch(`/admin/users/${id}/role`, {
          method: "POST",
          body: JSON.stringify({ role: newRole }),
        });
        await load();
        if (expanded === id) setDetail(await adminFetch(`/admin/users/${id}`));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [load, expanded],
  );

  const decideClaim = useCallback(
    async (claimId: number, decision: "verify" | "reject", userId: string) => {
      try {
        await adminFetch(`/admin/claims/${claimId}/${decision}`, {
          method: "POST",
          body: JSON.stringify({ reviewer: "admin" }),
        });
        await load();
        setDetail(await adminFetch(`/admin/users/${userId}`));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      }
    },
    [load],
  );

  const resetPage = useCallback(() => {
    setCursor(0);
    setCursorStack([0]);
  }, []);

  return (
    <div>
      {/* Header */}
      <div className="flex items-end justify-between gap-4 flex-wrap mb-6">
        <div>
          <h1 className="heading-display text-3xl text-[var(--sr-text-primary)]">
            Users &amp; plans
          </h1>
          <p className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mt-1">
            Every customer, their plan, their boats and their money
            {data ? ` — ${data.total} users` : ""}
          </p>
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="inline-flex items-center gap-1.5 admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)] transition-colors disabled:opacity-40"
        >
          <RefreshCw size={12} className={loading ? "animate-spin" : ""} />
          Refresh
        </button>
      </div>

      {/* Filter bar */}
      <div className="flex items-center gap-2 flex-wrap mb-4">
        <form
          className="relative"
          onSubmit={(e) => {
            e.preventDefault();
            resetPage();
            load();
          }}
        >
          <Search
            size={13}
            className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--sr-text-tertiary)]"
          />
          <input
            value={q}
            onChange={(e) => {
              setQ(e.target.value);
              resetPage();
            }}
            placeholder="Search email, name or boat…"
            className="h-9 w-64 pl-8 pr-3 bg-[var(--sr-surface-card)] border border-[var(--sr-border-strong)] rounded-[3px] text-[13px] text-[var(--sr-text-primary)] placeholder:text-[var(--sr-text-tertiary)] focus:outline-none focus:border-[var(--sr-marine-400)]"
          />
        </form>

        <select
          value={plan}
          onChange={(e) => {
            setPlan(e.target.value);
            resetPage();
          }}
          className="h-9 px-2 bg-[var(--sr-surface-card)] border border-[var(--sr-border-strong)] rounded-[3px] admin-mono-font text-[10px] uppercase tracking-[0.12em] text-[var(--sr-text-secondary)]"
        >
          <option value="">All plans</option>
          <option value="pro">Pro</option>
          <option value="skipper">Skipper</option>
          <option value="free">Free</option>
        </select>

        <select
          value={role}
          onChange={(e) => {
            setRole(e.target.value);
            resetPage();
          }}
          className="h-9 px-2 bg-[var(--sr-surface-card)] border border-[var(--sr-border-strong)] rounded-[3px] admin-mono-font text-[10px] uppercase tracking-[0.12em] text-[var(--sr-text-secondary)]"
        >
          <option value="">All roles</option>
          {ROLES.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>

        <button
          onClick={() => {
            setClaimsOnly((v) => !v);
            resetPage();
          }}
          className={`h-9 px-3 admin-mono-font text-[10px] uppercase tracking-[0.12em] border rounded-[3px] transition-colors ${
            claimsOnly
              ? "border-[var(--sr-buoy)]/60 text-[var(--sr-buoy)] bg-[var(--sr-buoy)]/10"
              : "border-[var(--sr-border-strong)] text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)]"
          }`}
        >
          <Anchor size={11} className="inline mr-1 -mt-0.5" />
          Claims pending
        </button>
      </div>

      {error && (
        <div className="mb-4 px-4 py-3 border border-[var(--sr-signal-500)]/40 rounded-[6px] text-[13px] text-[var(--sr-signal-500)]">
          {error}
        </div>
      )}

      {/* Users table */}
      <div className="admin-table-container">
        <table className="w-full text-[13px]">
          <thead>
            <tr className="admin-table-header admin-mono-font text-[9px] uppercase tracking-[0.14em] text-left">
              <th className="px-4 py-2.5 w-6" />
              <th className="px-4 py-2.5">Customer</th>
              <th className="px-4 py-2.5">Plan</th>
              <th className="px-4 py-2.5">Role</th>
              <th className="px-4 py-2.5 text-right">Boats</th>
              <th className="px-4 py-2.5 text-right">Reports</th>
              <th className="px-4 py-2.5 text-right">Spend</th>
              <th className="px-4 py-2.5">Joined</th>
              <th className="px-4 py-2.5">Last seen</th>
              <th className="px-4 py-2.5 w-10" />
            </tr>
          </thead>
          <tbody>
            {data?.users.map((u) => (
              <React.Fragment key={u.id}>
                <tr
                  className="admin-table-row"
                  onClick={() => toggle(u.id)}
                  data-testid={`user-row-${u.email}`}
                >
                  <td className="px-4 py-3 text-[var(--sr-text-tertiary)]">
                    {expanded === u.id ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </td>
                  <td className="px-4 py-3">
                    <div className="text-[var(--sr-text-primary)] font-medium leading-tight">
                      {u.full_name || u.email}
                    </div>
                    <div className="text-[11px] text-[var(--sr-text-tertiary)]">
                      {u.full_name ? u.email : ""}
                      {u.pending_claims > 0 && (
                        <span className="ml-2 admin-mono-font text-[9px] uppercase tracking-[0.1em] text-[var(--sr-buoy)]">
                          {u.pending_claims} claim{u.pending_claims === 1 ? "" : "s"} pending
                        </span>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`admin-mono-font text-[9px] uppercase tracking-[0.12em] px-1.5 py-0.5 border rounded-[3px] ${
                        PLAN_BADGE[u.plan] ?? PLAN_BADGE.free
                      }`}
                    >
                      {u.plan}
                    </span>
                  </td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    <select
                      value={u.role}
                      onChange={(e) => setUserRole(u.id, e.target.value)}
                      className={`bg-transparent border rounded-[3px] admin-mono-font text-[9px] uppercase tracking-[0.12em] px-1.5 py-0.5 ${
                        ROLE_BADGE[u.role] ?? ROLE_BADGE.customer
                      }`}
                    >
                      {ROLES.map((r) => (
                        <option key={r} value={r} className="bg-[var(--sr-surface-card)]">
                          {r}
                        </option>
                      ))}
                    </select>
                  </td>
                  <td className="px-4 py-3 text-right admin-mono-font text-[var(--sr-text-secondary)]">
                    {u.boats_claimed}
                  </td>
                  <td className="px-4 py-3 text-right admin-mono-font text-[var(--sr-text-secondary)]">
                    {u.reports_bought}
                  </td>
                  <td className={`px-4 py-3 text-right admin-mono-font ${MONEY_CLASS}`}>
                    {formatMoney(u.total_spend)}
                  </td>
                  <td className="px-4 py-3 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                    {formatDate(u.joined_at)}
                  </td>
                  <td className="px-4 py-3 admin-mono-font text-[11px] text-[var(--sr-text-secondary)]">
                    {timeAgo(u.last_seen_at)}
                  </td>
                  <td className="px-4 py-3" onClick={(e) => e.stopPropagation()}>
                    {u.stripe_dashboard_url && (
                      <a
                        href={u.stripe_dashboard_url}
                        target="_blank"
                        rel="noreferrer"
                        title="Open in Stripe"
                        className="inline-flex items-center gap-1 admin-mono-font text-[9px] uppercase tracking-[0.1em] text-[var(--sr-link)] hover:text-[var(--sr-link-hover)]"
                      >
                        <ExternalLink size={11} />
                        Stripe
                      </a>
                    )}
                  </td>
                </tr>

                {expanded === u.id && (
                  <tr className="border-b border-[var(--sr-border-subtle)]">
                    <td colSpan={10} className="px-8 py-4 bg-[var(--sr-surface-deep)]/40">
                      {!detail ? (
                        <div className="admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
                          Loading…
                        </div>
                      ) : (
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                          {/* Boats */}
                          <div>
                            <h3 className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-2 flex items-center gap-1.5">
                              <Ship size={11} /> Boats ({detail.boats.length})
                            </h3>
                            {detail.boats.length === 0 ? (
                              <p className="text-[12px] text-[var(--sr-text-tertiary)]">
                                No boats claimed yet.
                              </p>
                            ) : (
                              <ul className="space-y-2">
                                {detail.boats.map((b) => (
                                  <li
                                    key={b.claim_id}
                                    className="flex items-center justify-between gap-3 text-[12px]"
                                  >
                                    <div>
                                      <a
                                        href={`/boat/${b.boat_id}`}
                                        className="text-[var(--sr-link)] hover:text-[var(--sr-link-hover)]"
                                        onClick={(e) => e.stopPropagation()}
                                      >
                                        {b.boat_name ?? `#${b.boat_id}`}
                                      </a>
                                      <span className="text-[var(--sr-text-tertiary)]">
                                        {" "}
                                        {b.sail_number} · {b.design ?? "—"}
                                      </span>
                                    </div>
                                    {b.status === "pending" ? (
                                      <span className="flex items-center gap-1">
                                        <button
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            decideClaim(b.claim_id, "verify", u.id);
                                          }}
                                          title="Verify claim"
                                          className="p-1 border border-[var(--sr-starboard)]/50 text-[var(--sr-starboard)] rounded-[3px] hover:bg-[var(--sr-starboard)]/10"
                                        >
                                          <Check size={11} />
                                        </button>
                                        <button
                                          onClick={(e) => {
                                            e.stopPropagation();
                                            decideClaim(b.claim_id, "reject", u.id);
                                          }}
                                          title="Reject claim"
                                          className="p-1 border border-[var(--sr-signal-500)]/50 text-[var(--sr-signal-500)] rounded-[3px] hover:bg-[var(--sr-signal-500)]/10"
                                        >
                                          <X size={11} />
                                        </button>
                                      </span>
                                    ) : (
                                      <span
                                        className={`admin-mono-font text-[9px] uppercase tracking-[0.1em] ${
                                          b.status === "verified"
                                            ? "text-[var(--sr-starboard)]"
                                            : "text-[var(--sr-text-tertiary)]"
                                        }`}
                                      >
                                        {b.status}
                                      </span>
                                    )}
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>

                          {/* Orders */}
                          <div>
                            <h3 className="admin-mono-font text-[9px] uppercase tracking-[0.16em] text-[var(--sr-text-label)] mb-2">
                              Orders ({detail.orders.length})
                            </h3>
                            {detail.orders.length === 0 ? (
                              <p className="text-[12px] text-[var(--sr-text-tertiary)]">
                                No orders yet.
                              </p>
                            ) : (
                              <ul className="space-y-1.5">
                                {detail.orders.map((o) => (
                                  <li
                                    key={o.id}
                                    className="flex items-center justify-between gap-3 text-[12px]"
                                  >
                                    <span className="text-[var(--sr-text-secondary)] truncate">
                                      {o.boat_name ?? `boat #${o.boat_id}`}
                                      <span className="text-[var(--sr-text-tertiary)]">
                                        {" "}
                                        · {formatDate(o.created_at)}
                                      </span>
                                    </span>
                                    <span className="flex items-center gap-2 flex-shrink-0">
                                      <span className={`admin-mono-font ${MONEY_CLASS}`}>
                                        {formatMoney(o.amount)}
                                      </span>
                                      <span
                                        className={`admin-mono-font text-[9px] uppercase tracking-[0.1em] px-1.5 py-0.5 border rounded-[3px] ${
                                          ORDER_STATUS_STYLE[o.status]?.cls ?? ""
                                        }`}
                                      >
                                        {ORDER_STATUS_STYLE[o.status]?.label ?? o.status}
                                      </span>
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            )}
                          </div>
                        </div>
                      )}
                    </td>
                  </tr>
                )}
              </React.Fragment>
            ))}
            {data && data.users.length === 0 && (
              <tr>
                <td colSpan={10} className="px-4 py-10 text-center text-[13px] text-[var(--sr-text-tertiary)]">
                  No users match these filters.
                </td>
              </tr>
            )}
            {!data && !error && (
              <tr>
                <td colSpan={10} className="px-4 py-10 text-center admin-mono-font text-[10px] uppercase tracking-[0.16em] text-[var(--sr-text-label)]">
                  Loading users…
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Pager */}
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
