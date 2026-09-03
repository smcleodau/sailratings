"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import {
  ArrowLeftIcon,
  ChevronLeftIcon,
  ChevronRightIcon,
  LockIcon,
  CheckIcon,
  XIcon,
} from "@/components/admin/AdminIcons";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

interface ColInfo {
  name: string;
  type: string;
  nullable: boolean;
  has_default: boolean;
  max_length: number | null;
}

interface RowsResponse {
  table: string;
  editable: boolean;
  pk: string;
  columns: ColInfo[];
  rows: Record<string, unknown>[];
  total: number;
  offset: number;
  limit: number;
  order_by: string;
  order_dir: "asc" | "desc";
  q: string | null;
}

const PAGE_SIZE = 50;
const FORBIDDEN = new Set(["id", "created_at", "updated_at"]);

function cellDisplay(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "object") return JSON.stringify(v).slice(0, 120);
  return String(v);
}

export default function TableEditor({ params }: { params: Promise<{ name: string }> }) {
  const { name } = use(params);
  const [token, setToken] = useState<string | null>(null);
  const [data, setData] = useState<RowsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [offset, setOffset] = useState(0);
  const [orderBy, setOrderBy] = useState<string | null>(null);
  const [orderDir, setOrderDir] = useState<"asc" | "desc">("desc");
  const [filter, setFilter] = useState("");
  const [filterApplied, setFilterApplied] = useState("");
  const [editing, setEditing] = useState<{ pk: string; col: string } | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const stored = (typeof window !== "undefined" ? localStorage.getItem("admin_token") : null) || process.env.NEXT_PUBLIC_ADMIN_PASSWORD || "sailfast2026";
    if (stored) {
      if (typeof window !== "undefined") localStorage.setItem("admin_token", stored);
      setToken(stored);
    }
  }, []);

  const load = useCallback(async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        offset: String(offset),
        limit: String(PAGE_SIZE),
        order_dir: orderDir,
      });
      if (orderBy) params.set("order_by", orderBy);
      if (filterApplied) params.set("q", filterApplied);
      const res = await fetch(`${API_BASE}/admin/tables/${encodeURIComponent(name)}?${params}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        if (res.status === 401) {
          localStorage.removeItem("admin_token");
          setToken(null);
          return;
        }
        const detail = await res.text();
        throw new Error(`${res.status}: ${detail.slice(0, 200)}`);
      }
      const d = (await res.json()) as RowsResponse;
      setData(d);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Load failed");
    } finally {
      setLoading(false);
    }
  }, [token, name, offset, orderBy, orderDir, filterApplied]);

  useEffect(() => {
    if (token) void load();
  }, [token, load]);

  const headerSort = (col: string) => {
    if (orderBy === col) {
      setOrderDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setOrderBy(col);
      setOrderDir("asc");
    }
    setOffset(0);
  };

  const startEdit = (pk: string, col: string, current: unknown) => {
    setEditing({ pk, col });
    setDraft(current === null || current === undefined ? "" : String(current));
  };

  const cancelEdit = () => {
    setEditing(null);
    setDraft("");
  };

  const saveEdit = async () => {
    if (!editing || !token || !data) return;
    setSaving(true);
    try {
      // Allow setting null explicitly via the literal "null" sentinel
      const valueToSend: string | null = draft === "" || draft === "null" ? null : draft;
      const res = await fetch(
        `${API_BASE}/admin/tables/${encodeURIComponent(name)}/${encodeURIComponent(editing.pk)}`,
        {
          method: "PATCH",
          headers: {
            Authorization: `Bearer ${token}`,
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ column: editing.col, value: valueToSend }),
        },
      );
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(`${res.status}: ${detail.slice(0, 200)}`);
      }
      // Optimistic-update the row
      const pkCol = data.pk;
      setData({
        ...data,
        rows: data.rows.map((r) =>
          String(r[pkCol]) === editing.pk ? { ...r, [editing.col]: valueToSend } : r,
        ),
      });
      cancelEdit();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setSaving(false);
    }
  };


  if (!token) {
    return (
      <div className="flex-1 flex items-center justify-center">
        <p className="text-[13px] text-[var(--sr-text-tertiary)]">
          Sign in via <a className="text-[var(--sr-link)] underline hover:text-[var(--sr-link-hover)]" href="/admin">/admin</a> first.
        </p>
      </div>
    );
  }

  return (
    <div data-testid="table-editor-page" className="flex flex-col h-full">
      <div className="px-6 py-3 border-b border-[var(--sr-border-subtle)] flex items-center justify-between gap-4 flex-wrap bg-[var(--sr-surface-card)]">
        <div className="flex items-center gap-3 min-w-0">
          <Link
            href="/admin/tables"
            className="text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)] transition-colors"
            title="Back to all tables"
          >
            <ArrowLeftIcon size={16} strokeWidth={1.5} />
          </Link>
          <h1 className="heading-display text-lg text-[var(--sr-text-primary)] truncate">
            <span className="admin-mono-font text-[13px] tracking-normal">{name}</span>
          </h1>
          {data && (
            <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
              {data.total.toLocaleString()} rows
            </span>
          )}
          {data && !data.editable && (
            <span className="admin-pill !border-[var(--sr-status-warning)]/40 !text-[var(--sr-status-warning)]">
              <LockIcon size={10} />
              read-only
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <input
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                setOffset(0);
                setFilterApplied(filter);
              }
              if (e.key === "Escape") {
                setFilter("");
                setFilterApplied("");
              }
            }}
            placeholder="search… or col=val, col~text"
            className="bg-[var(--sr-surface-deep)] border border-[var(--sr-border-strong)] text-[var(--sr-text-primary)] placeholder:text-[var(--sr-text-tertiary)] px-3 py-1.5 text-[11px] admin-mono-font w-72 focus:border-[var(--sr-dusk)] focus:ring-1 focus:ring-[var(--sr-dusk)]/40 outline-none transition-all rounded-md"
          />
          <button
            onClick={() => {
              setOffset(0);
              setFilterApplied(filter);
            }}
            className="admin-mono-font text-[10px] uppercase tracking-wider bg-[var(--sr-dusk)] text-white hover:bg-[var(--sr-link)] px-3 py-1.5 rounded-md transition-colors"
          >
            Filter
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-[var(--sr-status-danger)]/10 border-b border-[var(--sr-status-danger)]/30 px-6 py-2">
          <p className="admin-mono-font text-[10px] text-[var(--sr-status-danger)]">{error}</p>
        </div>
      )}

      <div className="flex-1 overflow-auto">
        {data && (
          <table className="w-full admin-mono-font text-[11px]">
            <thead className="sticky top-0 bg-[var(--sr-surface-card)] z-10 border-b border-[var(--sr-border-subtle)]">
              <tr>
                {data.columns.map((c) => {
                  const isSort = data.order_by === c.name;
                  return (
                    <th
                      key={c.name}
                      onClick={() => headerSort(c.name)}
                      className={`text-left px-3 py-2 whitespace-nowrap cursor-pointer hover:bg-[var(--sr-surface-interactive)] transition-colors ${
                        isSort ? "text-[var(--sr-link)] font-semibold" : "text-[var(--sr-text-label)] font-medium"
                      }`}
                      title={`${c.type}${c.nullable ? " · nullable" : ""}`}
                    >
                      <span className="uppercase tracking-[0.14em] text-[10px]">{c.name}</span>
                      <span className="ml-1 text-[var(--sr-link)]/60">
                        {isSort ? (data.order_dir === "asc" ? "↑" : "↓") : ""}
                      </span>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {data.rows.map((row, ri) => {
                const pk = String(row[data.pk]);
                return (
                  <tr key={`${pk}-${ri}`} className="border-b border-[var(--sr-border-subtle)] hover:bg-[var(--sr-surface-interactive)]">
                    {data.columns.map((c) => {
                      const v = row[c.name];
                      const editable = data.editable && !FORBIDDEN.has(c.name);
                      const isEditing = editing?.pk === pk && editing?.col === c.name;
                      return (
                        <td
                          key={c.name}
                          onClick={() => editable && !isEditing && startEdit(pk, c.name, v)}
                          className={`px-3 py-1.5 align-top transition-colors ${
                            editable ? "cursor-text text-[var(--sr-text-primary)] hover:bg-[var(--sr-surface-interactive)]" : "cursor-default text-[var(--sr-text-tertiary)]"
                          } ${isEditing ? "bg-[var(--sr-surface-card)]" : ""}`}
                          style={{ maxWidth: 320 }}
                        >
                          {isEditing ? (
                            <span className="inline-flex items-center gap-1.5">
                              <input
                                autoFocus
                                value={draft}
                                onChange={(e) => setDraft(e.target.value)}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") void saveEdit();
                                  if (e.key === "Escape") cancelEdit();
                                }}
                                className="bg-[var(--sr-surface-deep)] border border-[var(--sr-dusk)] text-[var(--sr-text-primary)] px-1.5 py-0.5 text-[11px] admin-mono-font w-44 focus:outline-none rounded-[2px]"
                              />
                              <button
                                onClick={() => void saveEdit()}
                                disabled={saving}
                                className="text-[var(--sr-link)] hover:text-[var(--sr-link-hover)] disabled:opacity-30"
                                title="Save"
                              >
                                <CheckIcon size={11} />
                              </button>
                              <button
                                onClick={cancelEdit}
                                className="text-[var(--sr-text-label)] hover:text-[var(--sr-status-danger)]"
                                title="Cancel"
                              >
                                <XIcon size={11} />
                              </button>
                            </span>
                          ) : v === null || v === undefined ? (
                            <span className="text-[var(--sr-text-label)] italic">NULL</span>
                          ) : (
                            <span className="truncate block" title={cellDisplay(v)}>
                              {cellDisplay(v)}
                            </span>
                          )}
                        </td>
                      );
                    })}
                  </tr>
                );
              })}
              {!data.rows.length && !loading && (
                <tr>
                  <td colSpan={data.columns.length} className="px-3 py-12 text-center text-[var(--sr-text-label)] text-[13px]">
                    no rows
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        )}
      </div>

      {/* Pagination */}
      {data && data.total > PAGE_SIZE && (
        <div className="flex-shrink-0 border-t border-[var(--sr-border-subtle)] px-6 py-3 flex items-center justify-between bg-[var(--sr-surface-card)]">
          <span className="admin-mono-font text-[10px] uppercase tracking-wider text-[var(--sr-text-label)]">
            {data.offset + 1}–{Math.min(data.offset + data.rows.length, data.total)} of{" "}
            {data.total.toLocaleString()}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0}
              className="admin-mono-font text-[10px] uppercase tracking-wider bg-transparent border border-[var(--sr-border-strong)] text-[var(--sr-text-secondary)] hover:text-[var(--sr-text-primary)] hover:border-[var(--sr-dusk)] px-3 py-1.5 transition-colors disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1 rounded-md"
            >
              <ChevronLeftIcon size={12} /> Prev
            </button>
            <button
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= data.total}
              className="admin-mono-font text-[10px] uppercase tracking-wider bg-transparent border border-[var(--sr-border-strong)] text-[var(--sr-text-secondary)] hover:text-[var(--sr-text-primary)] hover:border-[var(--sr-dusk)] px-3 py-1.5 transition-colors disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1 rounded-md"
            >
              Next <ChevronRightIcon size={12} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
