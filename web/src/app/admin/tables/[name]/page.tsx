"use client";

import { use, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft, RefreshCw, ChevronLeft, ChevronRight, Lock, Check, X } from "lucide-react";
import { useAdminNavRightSlot } from "@/components/AdminNavShell";

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

  useAdminNavRightSlot(
    <button
      onClick={() => void load()}
      disabled={loading}
      className="text-white/40 hover:text-white/80 disabled:opacity-30"
      title="Refresh"
    >
      <RefreshCw size={16} strokeWidth={1.5} className={loading ? "animate-spin" : ""} />
    </button>,
  );

  if (!token) {
    return (
      <div className="flex-1 flex items-center justify-center bg-[#F3F1EC]">
        <p className="text-[13px] text-[#52655F]">
          Sign in via <a className="text-[#0C5F5C] underline hover:text-[#3E9B95]" href="/admin">/admin</a> first.
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[#F3F1EC]">
      <div className="px-6 py-3 border-b border-[#0C5F5C]/12 flex items-center justify-between gap-4 flex-wrap bg-[#F6F4EE]">
        <div className="flex items-center gap-3 min-w-0">
          <Link
            href="/admin/tables"
            className="text-[#7E948F] hover:text-[#162423] transition-colors"
            title="Back to all tables"
          >
            <ArrowLeft size={16} strokeWidth={1.5} />
          </Link>
          <h1 className="heading-display text-lg text-[#162423] truncate">
            <span className="admin-mono-font text-[13px] tracking-normal">{name}</span>
          </h1>
          {data && (
            <span className="admin-mono-font text-[10px] text-[#7E948F]">
              {data.total.toLocaleString()} rows
            </span>
          )}
          {data && !data.editable && (
            <span className="inline-flex items-center gap-1.5 admin-mono-font text-[9px] uppercase tracking-wider text-[#8A6613] bg-[#E8B23A]/15 px-2 py-0.5 rounded-[2px]">
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
            placeholder="search… or col=val, col~text"
            className="bg-white border border-[#0C5F5C]/25 text-[#162423] placeholder:text-[#7E948F] px-3 py-1.5 text-[11px] admin-mono-font w-72 focus:border-[#0C5F5C] focus:ring-1 focus:ring-[#0C5F5C]/20 outline-none transition-all rounded-[4px] shadow-sm"
          />
          <button
            onClick={() => {
              setOffset(0);
              setFilterApplied(filter);
            }}
            className="admin-mono-font text-[10px] uppercase tracking-wider bg-[#0C5F5C] text-white hover:bg-[#3E9B95] px-3 py-1.5 rounded-[4px] shadow-sm transition-colors"
          >
            Filter
          </button>
        </div>
      </div>

      {error && (
        <div className="bg-[#C92B12]/5 border-b border-[#C92B12]/20 px-6 py-2">
          <p className="admin-mono-font text-[10px] text-[#C92B12]">{error}</p>
        </div>
      )}

      <div className="flex-1 overflow-auto bg-[#F3F1EC]">
        {data && (
          <table className="w-full admin-mono-font text-[11px]">
            <thead className="sticky top-0 bg-[#F6F4EE] z-10 border-b border-[#0C5F5C]/12 shadow-sm">
              <tr>
                {data.columns.map((c) => {
                  const isSort = data.order_by === c.name;
                  return (
                    <th
                      key={c.name}
                      onClick={() => headerSort(c.name)}
                      className={`text-left px-3 py-2 whitespace-nowrap cursor-pointer hover:bg-[#E5DFD1]/50 transition-colors ${
                        isSort ? "text-[#0C5F5C] font-semibold" : "text-[#7E948F] font-medium"
                      }`}
                      title={`${c.type}${c.nullable ? " · nullable" : ""}`}
                    >
                      <span className="uppercase tracking-[0.14em] text-[10px]">{c.name}</span>
                      <span className="ml-1 text-[#0C5F5C]/60">
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
                  <tr key={`${pk}-${ri}`} className="border-b border-[#0C5F5C]/5 hover:bg-[#F6F4EE]">
                    {data.columns.map((c) => {
                      const v = row[c.name];
                      const editable = data.editable && !FORBIDDEN.has(c.name);
                      const isEditing = editing?.pk === pk && editing?.col === c.name;
                      return (
                        <td
                          key={c.name}
                          onClick={() => editable && !isEditing && startEdit(pk, c.name, v)}
                          className={`px-3 py-1.5 align-top transition-colors ${
                            editable ? "cursor-text text-[#162423] hover:bg-[#0C5F5C]/5" : "cursor-default text-[#52655F]"
                          } ${isEditing ? "bg-[#E6F0EE]" : ""}`}
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
                                className="bg-white border border-[#0C5F5C] text-[#162423] px-1.5 py-0.5 text-[11px] admin-mono-font w-44 focus:outline-none rounded-[2px] shadow-sm"
                              />
                              <button
                                onClick={() => void saveEdit()}
                                disabled={saving}
                                className="text-[#0C5F5C] hover:text-[#3E9B95] disabled:opacity-30"
                                title="Save"
                              >
                                <Check size={11} />
                              </button>
                              <button
                                onClick={cancelEdit}
                                className="text-[#7E948F] hover:text-[#C92B12]"
                                title="Cancel"
                              >
                                <X size={11} />
                              </button>
                            </span>
                          ) : v === null || v === undefined ? (
                            <span className="text-[#7E948F] italic">NULL</span>
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
                  <td colSpan={data.columns.length} className="px-3 py-12 text-center text-[#7E948F] text-[13px]">
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
        <div className="flex-shrink-0 border-t border-[#0C5F5C]/12 px-6 py-3 flex items-center justify-between bg-[#F6F4EE]">
          <span className="admin-mono-font text-[10px] uppercase tracking-wider text-[#7E948F]">
            {data.offset + 1}–{Math.min(data.offset + data.rows.length, data.total)} of{" "}
            {data.total.toLocaleString()}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              disabled={offset === 0}
              className="admin-mono-font text-[10px] uppercase tracking-wider bg-white border border-[#0C5F5C]/20 text-[#0C5F5C] hover:bg-[#E6F0EE] px-3 py-1.5 transition-colors disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1 rounded-[4px] shadow-sm"
            >
              <ChevronLeft size={12} /> Prev
            </button>
            <button
              onClick={() => setOffset(offset + PAGE_SIZE)}
              disabled={offset + PAGE_SIZE >= data.total}
              className="admin-mono-font text-[10px] uppercase tracking-wider bg-white border border-[#0C5F5C]/20 text-[#0C5F5C] hover:bg-[#E6F0EE] px-3 py-1.5 transition-colors disabled:opacity-30 disabled:cursor-not-allowed inline-flex items-center gap-1 rounded-[4px] shadow-sm"
            >
              Next <ChevronRight size={12} />
            </button>
          </div>
        </div>
      )}
    </div>
  );
}
