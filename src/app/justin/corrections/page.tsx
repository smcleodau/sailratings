"use client";

import { useCallback, useEffect, useState } from "react";
import { Check, X, RefreshCw, ArrowLeft } from "lucide-react";

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

  if (!token) {
    return (
      <div className="min-h-screen bg-navy flex items-center justify-center px-6">
        <div className="w-full max-w-sm text-center">
          <p className="body-text text-white/60 mb-4">
            Sign in via <a className="text-brass underline" href="/justin">/justin</a> first.
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-navy flex flex-col">
      <header className="flex-shrink-0 border-b border-white/10 px-6 py-4 flex items-center justify-between">
        <div className="flex items-center gap-3">
          <a href="/justin" className="text-white/40 hover:text-white/80 transition-colors">
            <ArrowLeft size={18} strokeWidth={1.5} />
          </a>
          <h1 className="heading-display text-xl text-white/90">Corrections</h1>
          <span className="data-mono text-xs text-white/25 hidden sm:inline">moderation queue</span>
        </div>
        <div className="flex items-center gap-3">
          {(["pending", "applied", "rejected"] as Tab[]).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`body-text text-sm px-3 py-1.5 transition-colors ${
                tab === t ? "text-brass border-b-2 border-brass" : "text-white/40 hover:text-white/70"
              }`}
            >
              {t}
            </button>
          ))}
          <button
            onClick={() => load(tab)}
            disabled={loading}
            className="text-white/40 hover:text-white/80 transition-colors disabled:opacity-30"
            title="Refresh"
          >
            <RefreshCw size={16} strokeWidth={1.5} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto">
        <div className="max-w-6xl mx-auto px-6 py-6">
          {error && (
            <div className="border border-brass/40 bg-brass/10 px-4 py-3 mb-4">
              <p className="body-text text-sm text-brass">{error}</p>
            </div>
          )}

          {rows.length === 0 && !loading && (
            <p className="body-text text-sm text-white/40 text-center py-16">
              No {tab} corrections.
            </p>
          )}

          <div className="space-y-2">
            {rows.map((r) => (
              <div
                key={r.id}
                className="border border-white/10 bg-white/5 px-4 py-3 grid grid-cols-1 md:grid-cols-[1fr_auto] gap-3"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span className="body-text text-sm text-white/90 font-medium">
                      {r.boat_name ?? "—"}
                    </span>
                    <span className="data-mono text-xs text-white/30">
                      #{r.boat_id ?? "?"}
                    </span>
                    <span className="data-mono text-xs uppercase tracking-wider text-brass">
                      {r.field_name}
                    </span>
                    <span className="data-mono text-xs text-white/30">
                      {fmt(r.submitted_at)}
                    </span>
                  </div>
                  <div className="mt-2 grid grid-cols-1 sm:grid-cols-2 gap-x-6 gap-y-1">
                    <div>
                      <span className="data-mono text-xs text-white/30 uppercase tracking-wider">
                        Current
                      </span>
                      <p className="body-text text-sm text-white/60 break-words">
                        {r.current_value ?? <span className="italic">empty</span>}
                      </p>
                    </div>
                    <div>
                      <span className="data-mono text-xs text-signal-light/80 uppercase tracking-wider">
                        Proposed
                      </span>
                      <p className="body-text text-sm text-white/90 break-words">
                        {r.proposed_value}
                      </p>
                    </div>
                  </div>
                  {r.submitted_email && (
                    <p className="data-mono text-xs text-white/30 mt-2">
                      from {r.submitted_email}
                    </p>
                  )}
                  {r.review_notes && (
                    <p className="body-text text-xs text-white/40 mt-1 italic">
                      note: {r.review_notes}
                    </p>
                  )}
                </div>

                {tab === "pending" && (
                  <div className="flex items-start gap-2 md:flex-col md:items-stretch">
                    <button
                      onClick={() => act(r.id, "approve")}
                      disabled={acting.has(r.id)}
                      className="flex items-center justify-center gap-1.5 px-4 py-1.5 bg-signal-light/20 hover:bg-signal-light/30 text-signal-light text-sm body-text font-medium transition-colors disabled:opacity-40"
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
                      className="flex items-center justify-center gap-1.5 px-4 py-1.5 border border-white/20 text-white/60 hover:text-white hover:border-white/40 text-sm body-text transition-colors disabled:opacity-40"
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
