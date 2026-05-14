"use client";

import { use, useCallback, useEffect, useState } from "react";
import { ArrowLeft, RefreshCw, ChevronLeft, ChevronRight, Lock, Check, X } from "lucide-react";
import { AdminNav } from "@/components/AdminNav";

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
    const stored = typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    if (stored) setToken(stored);
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
      <div className="min-h-screen bg-navy flex items-center justify-center">
        <p className="body-text text-white/60">
          Sign in via <a className="text-brass underline" href="/justin">/justin</a> first.
        </p>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-navy flex flex-col">
      <AdminNav />

      <div className="px-6 py-3 border-b border-white/10 flex items-center justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <a
            href="/justin/tables"
            className="text-white/40 hover:text-white/80"
            title="Back to all tables"
          >
            <ArrowLeft size={16} strokeWidth={1.5} />
          </a>
          <h1 className="heading-display text-lg text-white/90 truncate">
            <span className="data-mono">{name}</span>
          </h1>
          {data && (
            <span className="data-mono text-xs text-white/25">
              {data.total.toLocaleString()} rows
            </span>
          )}
          {data && !data.editable && (
            <span className="inline-flex items-center gap-1.5 data-mono text-[10px] uppercase tracking-wider text-brass/80 bg-brass/10 px-2 py-0.5 rounded-sm">
              <Lock size={10} />
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
            placeholder="filter, e.g. country=AUS or boat_name~sun"
            className="bg-navy-light/60 border border-white/15 text-white/90 placeholder:text-white/30 px-3 py-1.5 text-xs data-mono w-72 focus:border-brass/60 focus:outline-none"
          />
          <button
            onClick={() => {
              setOffset(0);
              setFilterApplied(filter);
            }}
            className="data-mono text-xs uppercase tracking-wider text-white/60 hover:text-white border border-white/15 hover:border-white/30 px-3 py-1.5 transition-colors"
          >
            Filter
          </button>
          <button
            onClick={() => void load()}
            disabled={loading}
            className="text-white/40 hover:text-white/80 disabled:opacity-30"
            title="Refresh"
          >
            <RefreshCw size={16} strokeWidth={1.5} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-brass/15 border-b border-brass/30 px-6 py-2">
          <p className="data-mono text-xs text-brass">{error}</p>
        </div>
      )}

      <div className="flex-1 overflow-auto">
        {data && (
          <table className="w-full data-mono text-[11px]">
            <thead className="sticky top-0 bg-navy z-10 border-b border-white/10">
              <tr>
                {data.columns.map((c) => {
                  const isSort = data.order_by === c.name;
                  return (
                    <th
                      key={c.name}
                      onClick={() => headerSort(c.name)}
                      className={`text-left px-3 py-2 whitespace-nowrap cursor-pointer hover:bg-white/5 transition-colors ${
                        isSort ? "text-brass" : "text-white/50"
                      }`}
                      title={`${c.type}${c.nullable ? " · nullable" : ""}`}
                    >
                      <span className="uppercase tracking-wider">{c.name}</span>
                      <span className="ml-1 text-white/30">
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
                  <tr key={`${pk}-${ri}`} className="border-b border-white/5 hover:bg-white/[0.02]">
                    {data.columns.map((c) => {
                      const v = row[c.name];
                      const editable = data.editable && !FORBIDDEN.has(c.name);
                      const isEditing = editing?.pk === pk && editing?.col === c.name;
                      return (
                        <td
                          key={c.name}
                          onClick={() => editable && !isEditing && startEdit(pk, c.name, v)}
                          className={`px-3 py-1.5 text-white/85 align-top ${
                            editable ? "cursor-text hover:bg-brass/5" : "cursor-default text-white/55"
                          } ${isEditing ? "bg-brass/10" : ""}`}
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
                                className="bg-navy-light/80 border border-brass/50 text-white px-1.5 py-0.5 text-[11px] data-mono w-44 focus:outline-none focus:border-brass"
                              />
                              <button
                                onClick={() => void saveEdit()}
                                disabled={saving}
                                className="text-brass hover:text-cream disabled:opacity-30"
                                title="Save"
                              >
                                <Check size={11} />
                              </button>
                              <button
                                onClick={cancelEdit}
                                className="text-white/40 hover:text-white"
                                title="Cancel"
                              >
                                <X size={11} />
                              </button>
                            </span>
                          ) : v === null || v === undefined ? (
                            <span className="text-white/25 italic">NULL</span>
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
                  <td colSpan={data.columns.length} className="px-3 py-12 text-center text-white/40">
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
        <div className="flex-shrink-0 border-t border-white/10 px-6 py-3 flex items-center justify-between bg-navy">
          <span className="data-mono text-[11px] uppercase tracking-wider text-white/40">
            {data.offset + 1}–{Math.min(data.offset + data.rows.length, data.total)} of{" "}
            {data.total.toLocaleString()}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0}
              className="data-mono text-xs uppercase tracking-wider text-white/60 hover:text-white border border-white/15 hover:border-white/30 px-3 py-1.5 transition-colors disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1"
            >
              <ChevronLeft size={12} /> Prev
            </button>
            <button
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= data.total}
              className="data-mono text-xs uppercase tracking-wider text-white/60 hover:text-white border border-white/15 hover:border-white/30 px-3 py-1.5 transition-colors disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1"
            >
              Next <ChevronRight size={12} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
