"use client";

/**
 * AdminNavShell (AD-01-12) — the one shell every admin page mounts into.
 *
 * Layout: a 232px sidebar (Today / Data quality / Operations / Customers /
 * Agents) beside a content column headed by the topbar (global search with
 * `/` focus, environment badge, health pills). The whole shell sits on the
 * Dusk ground defined by `.admin-theme` in globals.css (SPEC-22 §1).
 *
 * Sidebar counts come from GET /v1/admin/overview (AD-01-13). Until that
 * endpoint exists they render 0, per the acceptance criteria.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import {
  EMPTY_ADMIN_OVERVIEW,
  fetchAdminOverview,
  fetchServiceHealth,
  type AdminHealthPill,
  type AdminOverview,
} from "@/lib/adminApi";
import { AdminSidebar } from "@/components/AdminSidebar";
import { AdminTopbar } from "@/components/AdminTopbar";

/* ── Right-slot portal ───────────────────────────────────────────────── */

type SlotSetter = (node: React.ReactNode) => void;
const AdminNavRightSlotContext = createContext<SlotSetter>(() => {});

/** Inject a React node into the topbar's right area from any admin page. */
export function useAdminNavRightSlot(node: React.ReactNode) {
  const setSlot = useContext(AdminNavRightSlotContext);
  useEffect(() => {
    setSlot(node);
    return () => setSlot(null);
  }); // no deps — keeps slot current on every page render
}

function resolveEnvironment(): string {
  const fromEnv = (process.env.NEXT_PUBLIC_ENVIRONMENT ?? "").toLowerCase();
  if (fromEnv) return fromEnv;
  if (typeof window === "undefined") return "local";
  const host = window.location.hostname;
  if (host === "localhost" || host === "127.0.0.1") return "local";
  if (host.includes("-dev") || host.includes("dev.")) return "dev";
  return "production";
}

function healthPillsFromOverview(overview: AdminOverview): AdminHealthPill[] {
  if (overview.health.length > 0) return overview.health;
  return [];
}

export function AdminNavShell({ children }: { children: React.ReactNode }) {
  const [overview, setOverview] = useState<AdminOverview>(EMPTY_ADMIN_OVERVIEW);
  const [health, setHealth] = useState<AdminHealthPill[]>([]);
  const [rightSlot, setRightSlot] = useState<React.ReactNode>(null);
  const setSlot = useCallback<SlotSetter>((node) => setRightSlot(node), []);
  // Lazy initializer keeps this a pure render — no setState-in-effect.
  const [environment] = useState(resolveEnvironment);

  // Sidebar counts + health pills from AD-01-13; 0s until it ships.
  useEffect(() => {
    const controller = new AbortController();
    fetchAdminOverview(controller.signal)
      .then((data) => {
        setOverview(data);
        setHealth(healthPillsFromOverview(data));
      })
      .catch(() => {
        // Auth failures etc. — counts stay at 0, pills fall through to /health.
      });
    return () => controller.abort();
  }, []);

  // Baseline service health pills from the public /health endpoint.
  useEffect(() => {
    const controller = new AbortController();
    fetchServiceHealth(controller.signal)
      .then((body) => {
        const status = (body.status ?? "").toLowerCase();
        const ok = status === "ok" || status === "healthy" || status === "";
        setHealth((current) => {
          // Overview pills (AD-01-13) take precedence once they exist.
          if (current.some((p) => p.label !== "api" && p.label !== "db")) {
            return current;
          }
          return [
            { label: "api", status: "ok", detail: "API reachable" },
            {
              label: "db",
              status: ok ? "ok" : "warn",
              detail: `status: ${body.status ?? "unknown"}`,
            },
          ];
        });
      })
      .catch(() => {
        setHealth((current) =>
          current.length > 0
            ? current
            : [{ label: "api", status: "down", detail: "unreachable" }],
        );
      });
    return () => controller.abort();
  }, []);

  return (
    <AdminNavRightSlotContext.Provider value={setSlot}>
      <div className="admin-theme" data-testid="admin-shell">
        <div className="admin-box flex min-h-screen bg-[var(--sr-dusk-ground)] text-[var(--sr-text-primary)]">
          <AdminSidebar counts={overview.counts} />
          <div className="flex-1 flex flex-col min-w-0">
            <AdminTopbar environment={environment} health={health} rightSlot={rightSlot} />
            <main className="admin-container flex-1">{children}</main>
          </div>
        </div>
      </div>
    </AdminNavRightSlotContext.Provider>
  );
}
