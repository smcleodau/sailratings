"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, X, RefreshCw } from "lucide-react";
import { useAdminNavRightSlot } from "@/components/AdminNavShell";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "/api/v1";

interface Correction {
  id: number;
  boat_id: number | null;
  boat_name: string | null;
  field_name: string;
  current_value: string | null;
  proposed_value: string;
  submitted_email: string | null;
  submitted_at: string;
  status: string;
  reviewed_at?: string | null;
  review_notes?: string | null;
}

type Tab = "pending" | "applied" | "rejected";

export default function CorrectionsPage() {
  const [token, setToken] = useState<string | null>(null);
  const [rows, setRows] = useState<Correction[]>([]);
  const [loading, setLoading] = useState(false);
  const [tab, setTab] = useState<Tab>("pending");
  const [error, setError] = useState<string | null>(null);
  const [acting, setActing] = useState<Set<number>>(new Set());

  useEffect(() => {
    const stored = typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
    if (stored) setToken(stored);
  }, []);

  const load = useCallback(
    async (which: Tab) => {
      if (!token) return;
      setLoading(true);
      setError(null);
      try {
        const res = await fetch(`${API_BASE}/admin/corrections?status=${which}&limit=200`, {
          headers: { Authorization: `Bearer ${token}` },
        });
        if (!res.ok) {
          if (res.status === 401) {
            localStorage.removeItem("admin_token");
            setToken(null);
            return;
          }
          throw new Error(`Load failed: ${res.status}`);
        }
        const data: Correction[] = await res.json();
        setRows(data);
      } catch (e) {
        setError(e instanceof Error ? e.message : "Failed");
      } finally {
        setLoading(false);
      }
    },
    [token],
  );

  useEffect(() => {
    if (token) void load(tab);
  }, [token, tab, load]);

  const act = async (id: number, action: "approve" | "reject", notes?: string) => {
    if (!token) return;
    setActing((prev) => new Set(prev).add(id));
    try {
      const res = await fetch(`${API_BASE}/admin/corrections/${id}/${action}`, {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ review_notes: notes ?? null }),
      });
      if (!res.ok) throw new Error(`${action} failed: ${res.status}`);
      setRows((prev) => prev.filter((r) => r.id !== id));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Action failed");
    } finally {
      setActing((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  };

  const fmt = (iso: string) => {
    const d = new Date(iso);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return `${Math.floor(diff / 60)}m`;
    if (diff < 86400) return `${Math.floor(diff / 3600)}h`;
    return d.toLocaleDateString();
  };

  useAdminNavRightSlot(
    <button
      onClick={() => load(tab)}
      disabled={loading}
      className="text-[#7E948F] hover:text-[#162423] transition-colors disabled:opacity-30"
      title="Refresh"
    >
      <RefreshCw size={16} strokeWidth={1.5} className={loading ? "animate-spin" : ""} />
    </button>,
  );

  if (!token) {
    return (
      <div className="flex-1 flex items-center justify-center px-6 bg-[#F3F1EC]">
        <div className="w-full max-w-sm text-center">
          <p className="text-[13px] text-[#52655F] mb-4">
            Sign in via <a className="text-[#0C5F5C] underline hover:text-[#3E9B95]" href="/admin">/admin</a> first.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[#F3F1EC]">
      <div className="px-6 py-3 border-b border-[#0C5F5C]/12 flex items-center justify-between gap-3 bg-[#F6F4EE]">
        <div className="flex items-baseline gap-3">
          <h1 className="heading-display text-lg text-[#162423]">Corrections</h1>
          <span className="admin-mono-font text-[10px] text-[#7E948F]">moderation queue</span>
        </div>
        <div className="flex items-center gap-1">
          {(["pending", "applied", "rejected"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`admin-mono-font text-[10px] uppercase tracking-[0.16em] px-3 py-1.5 transition-colors ${
                tab === t ? "text-[#0C5F5C] border-b-2 border-[#0C5F5C]" : "text-[#7E948F] hover:text-[#162423]"
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-6xl mx-auto px-6 py-6">
          {error && (
            <div className="border border-[#C92B12]/40 bg-[#C92B12]/5 px-4 py-3 mb-4 rounded-[4px]">
              <p className="text-[13px] text-[#C92B12]">{error}</p>
            </div>
          )}

          {rows.length === 0 && !loading && (
            <p className="text-[13px] text-[#7E948F] text-center py-16">
              No {tab} corrections.
            </p>
          )}

          <div className="space-y-3">
            {rows.map((r) => (
              <div
                key={r.id}
                className="border border-[#0C5F5C]/12 bg-white px-4 py-3 grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3 rounded-[4px] shadow-sm"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="text-[13px] text-[#162423] font-medium">
                      {r.boat_name ?? "—"}
                    </span>
                    <span className="admin-mono-font text-[10px] text-[#7E948F]">
                      #{r.boat_id ?? "?"}
                    </span>
                    <span className="admin-mono-font text-[10px] uppercase tracking-[0.14em] text-[#0C5F5C]">
                      {r.field_name}
                    </span>
                    <span className="admin-mono-font text-[10px] text-[#7E948F]">
                      {fmt(r.submitted_at)}
                    </span>
                  </div>
                  <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
                    <div>
                      <span className="admin-mono-font text-[9px] text-[#7E948F] uppercase tracking-wider">
                        Current
                      </span>
                      <p className="text-[13px] text-[#52655F] break-words">
                        {r.current_value ?? <span className="italic text-[#7E948F]">empty</span>}
                      </p>
                    </div>
                    <div>
                      <span className="admin-mono-font text-[9px] text-[#2E7D54] uppercase tracking-wider">
                        Proposed
                      </span>
                      <p className="text-[13px] text-[#162423] break-words">
                        {r.proposed_value}
                      </p>
                    </div>
                  </div>
                  {r.submitted_email && (
                    <p className="admin-mono-font text-[10px] text-[#7E948F] mt-2">
                      from {r.submitted_email}
                    </p>
                  )}
                  {r.review_notes && (
                    <p className="text-[11px] text-[#52655F] mt-1 italic">
                      note: {r.review_notes}
                    </p>
                  )}
                </div>

                {tab === "pending" && (
                  <div className="flex items-start gap-2 md:flex-col md:items-stretch">
                    <button
                      onClick={() => act(r.id, "approve")}
                      disabled={acting.has(r.id)}
                      className="flex items-center justify-center gap-1.5 px-4 py-1.5 bg-[#0C5F5C] text-white text-[11px] font-medium hover:bg-[#3E9B95] transition-colors disabled:opacity-40 rounded-[4px] shadow-sm"
                    >
                      <Check size={14} strokeWidth={2} />
                      Approve
                    </button>
                    <button
                      onClick={() => {
                        const reason = window.prompt("Reject reason (optional)") ?? undefined;
                        act(r.id, "reject", reason);
                      }}
                      disabled={acting.has(r.id)}
                      className="flex items-center justify-center gap-1.5 px-4 py-1.5 border border-[#C92B12]/40 text-[#C92B12] hover:bg-[#C92B12]/10 text-[11px] font-medium transition-colors disabled:opacity-40 rounded-[4px]"
                    >
                      <X size={14} strokeWidth={2} />
                      Reject
                    </button>
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
