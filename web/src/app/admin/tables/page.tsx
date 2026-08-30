"use client";

import { useEffect, useState } from "react";
import { RefreshCw, Lock } from "lucide-react";
import { useAdminNavRightSlot } from "@/components/AdminNavShell";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

interface TableInfo {
  name: string;
  rows: number;
  total_bytes: number;
  table_bytes: number;
  index_bytes: number;
  editable: boolean;
  pk: string;
}

function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} kB`;
  if (n < 1024 * 1024 * 1024) return `${(n / 1024 / 1024).toFixed(1)} MB`;
  return `${(n / 1024 / 1024 / 1024).toFixed(2)} GB`;
}

function fmtCount(n: number): string {
  return n.toLocaleString("en-US");
}

export default function TablesIndex() {
  const [token, setToken] = useState<string | null>(null);
  const [rows, setRows] = useState<TableInfo[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const stored = (typeof window !== "undefined" ? localStorage.getItem("admin_token") : null) || process.env.NEXT_PUBLIC_ADMIN_PASSWORD || "sailfast2026";
    if (stored) {
      if (typeof window !== "undefined") localStorage.setItem("admin_token", stored);
      setToken(stored);
    }
  }, []);

  const load = async () => {
    if (!token) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/admin/tables`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) {
        if (res.status === 401) {
          localStorage.removeItem("admin_token");
          setToken(null);
          return;
        }
        throw new Error(`${res.status}`);
      }
      const data = await res.json();
      setRows(data.tables);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Load failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (token) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token]);

  // Inject refresh button into the shared nav's right slot
  useAdminNavRightSlot(
    <button
      onClick={load}
      disabled={loading}
      className="text-[var(--sr-text-label)] hover:text-[var(--sr-text-primary)] disabled:opacity-30 transition-colors"
      title="Refresh"
    >
      <RefreshCw size={16} strokeWidth={1.5} className={loading ? "animate-spin" : ""} />
    </button>,
  );

  if (!token) {
    return (
      <div className="flex-1 flex items-center justify-center px-6 bg-[var(--sr-surface-page)]">
        <div className="text-center">
          <p className="text-[13px] text-[var(--sr-text-tertiary)] mb-4">
            Sign in via{" "}
            <a className="text-[var(--sr-link)] underline hover:text-[var(--sr-focus)]" href="/admin">
              /admin
            </a>{" "}
            first.
          </p>
        </div>
      </div>
    );
  }

  const totalRows = rows.reduce((sum, t) => sum + t.rows, 0);
  const totalSize = rows.reduce((sum, t) => sum + t.total_bytes, 0);

  return (
    <>
      <div className="px-6 py-4 border-b border-[var(--sr-link)]/12 flex items-baseline gap-3 bg-[var(--sr-surface-interactive)]">
        <h1 className="heading-display text-xl text-[var(--sr-text-primary)]">Tables</h1>
        <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
          {fmtCount(totalRows)} rows · {fmtBytes(totalSize)}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto bg-[var(--sr-surface-page)]">
        <div className="max-w-5xl mx-auto px-6 py-6">
          {error && (
            <div className="border border-[var(--sr-action-pressed)]/40 bg-[var(--sr-action-pressed)]/5 px-4 py-3 mb-4 rounded-[4px]">
              <p className="text-[13px] text-[var(--sr-action-pressed)]">{error}</p>
            </div>
          )}

          <div className="space-y-1">
            <div className="grid grid-cols-12 gap-3 px-3 py-2 admin-mono-font text-[10px] uppercase tracking-[0.14em] text-[var(--sr-text-label)] border-b border-[var(--sr-link)]/12">
              <div className="col-span-5">Table</div>
              <div className="col-span-2 text-right">Rows</div>
              <div className="col-span-2 text-right">Data</div>
              <div className="col-span-2 text-right">Indexes</div>
              <div className="col-span-1 text-right">Mode</div>
            </div>
            {rows.map((t) => (
              <a
                key={t.name}
                href={`/admin/tables/${encodeURIComponent(t.name)}`}
                className="group grid grid-cols-12 gap-3 px-3 py-2.5 border-b border-[var(--sr-link)]/5 hover:bg-[var(--sr-surface-interactive)] transition-colors rounded-[4px]"
              >
                <div className="col-span-5 min-w-0 flex items-center">
                  <span className="admin-mono-font text-[11px] text-[var(--sr-text-primary)] font-medium truncate">{t.name}</span>
                </div>
                <div className="col-span-2 text-right admin-mono-font text-[11px] text-[var(--sr-text-tertiary)] flex items-center justify-end">
                  {fmtCount(t.rows)}
                </div>
                <div className="col-span-2 text-right admin-mono-font text-[11px] text-[var(--sr-text-label)] flex items-center justify-end">
                  {fmtBytes(t.table_bytes)}
                </div>
                <div className="col-span-2 text-right admin-mono-font text-[11px] text-[var(--sr-text-label)]/70 flex items-center justify-end">
                  {fmtBytes(t.index_bytes)}
                </div>
                <div className="col-span-1 text-right flex items-center justify-end">
                  {t.editable ? (
                    <span className="admin-mono-font text-[9px] uppercase tracking-wider text-[var(--sr-status-success)]">
                      edit
                    </span>
                  ) : (
                    <Lock size={12} className="inline text-[var(--sr-status-warning)]/60" />
                  )}
                </div>
              </a>
            ))}
          </div>
        </div>
      </div>
    </>
  );
}
