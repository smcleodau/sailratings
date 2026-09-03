"use client";

import { useEffect, useState } from "react";
import {
  LockIcon,
} from "@/components/admin/AdminIcons";

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

  if (!token) {
    return (
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="text-center">
          <p className="text-[13px] text-[var(--sr-text-tertiary)] mb-4">
            Sign in via{" "}
            <a className="text-[var(--sr-link)] underline hover:text-[var(--sr-link-hover)]" href="/admin">
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
    <div data-testid="tables-page" className="flex-1 overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-8">
        {/* Header */}
        <div className="flex items-baseline gap-3 mb-6">
          <h1 className="heading-display text-2xl text-[var(--sr-text-primary)]">Tables</h1>
          <span className="admin-mono-font text-[10px] text-[var(--sr-text-label)]">
            {fmtCount(totalRows)} rows · {fmtBytes(totalSize)}
          </span>
        </div>

        {error && (
          <div className="border border-[var(--sr-status-danger)]/40 bg-[var(--sr-status-danger)]/10 px-4 py-3 mb-4 rounded-md">
            <p className="text-[13px] text-[var(--sr-status-danger)]">{error}</p>
          </div>
        )}

        {/* Hairline table */}
        <div className="admin-table-container">
          <div className="admin-table-header grid grid-cols-12 gap-3 admin-mono-font text-[9px] uppercase tracking-[0.16em]">
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
              data-testid={`table-row-${t.name}`}
              className="group grid grid-cols-12 gap-3 px-4 py-2.5 border-b border-[var(--sr-border-subtle)] last:border-b-0 hover:bg-[var(--sr-surface-interactive)] transition-colors"
            >
              <div className="col-span-5 min-w-0 flex items-center">
                <span className="admin-mono-font text-[11px] text-[var(--sr-text-primary)] font-medium truncate">{t.name}</span>
              </div>
              <div className="col-span-2 text-right admin-mono-font text-[11px] tabular-nums text-[var(--sr-text-secondary)] flex items-center justify-end">
                {fmtCount(t.rows)}
              </div>
              <div className="col-span-2 text-right admin-mono-font text-[11px] tabular-nums text-[var(--sr-text-label)] flex items-center justify-end">
                {fmtBytes(t.table_bytes)}
              </div>
              <div className="col-span-2 text-right admin-mono-font text-[11px] tabular-nums text-[var(--sr-text-label)]/70 flex items-center justify-end">
                {fmtBytes(t.index_bytes)}
              </div>
              <div className="col-span-1 text-right flex items-center justify-end">
                {t.editable ? (
                  <span className="admin-pill !border-[var(--sr-status-success)]/40 !text-[var(--sr-status-success)]">
                    edit
                  </span>
                ) : (
                  <span className="admin-pill" title="Read-only">
                    <LockIcon size={9} strokeWidth={2} />
                    ro
                  </span>
                )}
              </div>
            </a>
          ))}
          {rows.length === 0 && !loading && (
            <p className="px-4 py-10 text-center admin-mono-font text-[11px] text-[var(--sr-text-label)]">
              No tables visible to the admin role.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
