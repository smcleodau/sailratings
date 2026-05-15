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
    const stored = typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    if (stored) setToken(stored);
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
      className="text-white/40 hover:text-white/80 disabled:opacity-30"
      title="Refresh"
    >
      <RefreshCw size={16} strokeWidth={1.5} className={loading ? "animate-spin" : ""} />
    </button>,
  );

  if (!token) {
    return (
      <div className="flex-1 flex items-center justify-center px-6">
        <div className="text-center">
          <p className="body-text text-white/60 mb-4">
            Sign in via{" "}
            <a className="text-brass underline" href="/justin">
              /justin
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
      <div className="px-6 py-4 border-b border-white/10 flex items-baseline gap-3">
        <h1 className="heading-display text-xl text-white/90">Tables</h1>
        <span className="data-mono text-xs text-white/25">
          {fmtCount(totalRows)} rows · {fmtBytes(totalSize)}
        </span>
      </div>

      <div className="flex-1 overflow-y-auto bg-navy">
        <div className="max-w-5xl mx-auto px-6 py-6">
          {error && (
            <div className="border border-brass/40 bg-brass/10 px-4 py-3 mb-4">
              <p className="body-text text-sm text-brass">{error}</p>
            </div>
          )}

          <div className="space-y-1">
            <div className="grid grid-cols-12 gap-3 px-3 py-2 data-mono text-[10px] uppercase tracking-wider text-white/35 border-b border-white/10">
              <div className="col-span-5">Table</div>
              <div className="col-span-2 text-right">Rows</div>
              <div className="col-span-2 text-right">Data</div>
              <div className="col-span-2 text-right">Indexes</div>
              <div className="col-span-1 text-right">Mode</div>
            </div>
            {rows.map((t) => (
              <a
                key={t.name}
                href={`/justin/tables/${encodeURIComponent(t.name)}`}
                className="group grid grid-cols-12 gap-3 px-3 py-2.5 border-b border-white/5 hover:bg-white/5 transition-colors"
              >
                <div className="col-span-5 min-w-0">
                  <span className="data-mono text-sm text-white/90 truncate">{t.name}</span>
                </div>
                <div className="col-span-2 text-right data-mono text-sm text-white/70">
                  {fmtCount(t.rows)}
                </div>
                <div className="col-span-2 text-right data-mono text-sm text-white/50">
                  {fmtBytes(t.table_bytes)}
                </div>
                <div className="col-span-2 text-right data-mono text-sm text-white/35">
                  {fmtBytes(t.index_bytes)}
                </div>
                <div className="col-span-1 text-right">
                  {t.editable ? (
                    <span className="data-mono text-[10px] uppercase tracking-wider text-signal-light/70">
                      edit
                    </span>
                  ) : (
                    <Lock size={12} className="inline text-brass/60" />
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
