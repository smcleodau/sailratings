"use client";

/**
 * AdminTopbar (AD-01-12) — the 64px bar across the top of the admin shell:
 *
 *   · global search input — pressing `/` anywhere focuses it
 *   · environment badge (local / dev / production)
 *   · health pills fed by GET /v1/health and the AD-01-13 overview
 */

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import type { AdminHealthPill, AdminHealthStatus } from "@/lib/adminApi";
import { SearchIcon } from "@/components/admin/AdminIcons";
import { AdminSignOutButton } from "@/components/admin/AdminSignOutButton";

interface AdminTopbarProps {
  environment: string;
  health: AdminHealthPill[];
  rightSlot?: React.ReactNode;
}

const PILL_STYLES: Record<AdminHealthStatus, string> = {
  ok: "border-[var(--sr-status-success)]/40 text-[var(--sr-status-success)]",
  warn: "border-[var(--sr-status-warning)]/40 text-[var(--sr-status-warning)]",
  down: "border-[var(--sr-status-danger)]/45 text-[var(--sr-status-danger)]",
  unknown: "border-[var(--sr-border-strong)] text-[var(--sr-text-tertiary)]",
};

const PILL_DOT: Record<AdminHealthStatus, string> = {
  ok: "bg-[var(--sr-status-success)]",
  warn: "bg-[var(--sr-status-warning)]",
  down: "bg-[var(--sr-status-danger)]",
  unknown: "bg-[var(--sr-text-tertiary)]",
};

const ENV_BADGE_STYLES: Record<string, string> = {
  production: "border-[var(--sr-status-danger)]/50 text-[var(--sr-status-danger)]",
  dev: "border-[var(--sr-status-warning)]/50 text-[var(--sr-status-warning)]",
  local: "border-[var(--sr-dusk)]/50 text-[var(--sr-dusk)]",
};

export function AdminTopbar({ environment, health, rightSlot }: AdminTopbarProps) {
  const router = useRouter();
  const searchRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");

  // `/` focuses the global search (unless the user is already typing in a
  // field). Escape blurs.
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      const typing =
        target &&
        (target.tagName === "INPUT" ||
          target.tagName === "TEXTAREA" ||
          target.tagName === "SELECT" ||
          target.isContentEditable);

      if (event.key === "/" && !typing) {
        event.preventDefault();
        searchRef.current?.focus();
      } else if (event.key === "Escape" && document.activeElement === searchRef.current) {
        searchRef.current?.blur();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, []);

  const handleSearchSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    const q = query.trim();
    if (q) {
      router.push(`/admin/tables?q=${encodeURIComponent(q)}`);
    }
  };

  const envKey = environment.toLowerCase();
  const envStyle =
    ENV_BADGE_STYLES[envKey] ?? "border-[var(--sr-border-strong)] text-[var(--sr-text-secondary)]";

  return (
    <header
      data-testid="admin-topbar"
      className="sticky top-0 z-20 flex items-center gap-4 h-16 px-6 flex-shrink-0 bg-[var(--sr-dusk-ground)]/90 backdrop-blur border-b border-[var(--sr-border-subtle)]"
    >
      {/* Global search */}
      <form
        onSubmit={handleSearchSubmit}
        role="search"
        className="flex-1 max-w-md"
      >
        <label className="relative flex items-center">
          <SearchIcon
            size={14}
            className="absolute left-3 text-[var(--sr-text-tertiary)] pointer-events-none"
          />
          <input
            ref={searchRef}
            data-testid="admin-global-search"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search admin…"
            aria-label="Global admin search"
            className="w-full h-9 rounded-md bg-[var(--sr-dusk-card)] border border-[var(--sr-border-subtle)] pl-9 pr-12 text-[13px] text-[var(--sr-text-primary)] placeholder:text-[var(--sr-text-tertiary)] focus:outline-none focus:border-[var(--sr-dusk)] focus:ring-1 focus:ring-[var(--sr-dusk)]/40 transition-colors"
          />
          <kbd className="absolute right-2.5 admin-mono-font text-[10px] text-[var(--sr-text-tertiary)] border border-[var(--sr-border-subtle)] rounded px-1.5 py-0.5 pointer-events-none">
            /
          </kbd>
        </label>
      </form>

      <div className="flex-1" />

      {/* Page-injected right slot (e.g. conversation ID badge) */}
      {rightSlot && <div className="flex items-center">{rightSlot}</div>}

      {/* Health pills */}
      <div className="flex items-center gap-2" data-testid="admin-health-pills" aria-label="Service health">
        {health.length === 0 ? (
          <span className="flex items-center gap-1.5 rounded-full border px-2.5 py-1 admin-mono-font text-[10px] uppercase tracking-[0.1em] border-[var(--sr-border-strong)] text-[var(--sr-text-tertiary)]">
            <span className="w-1.5 h-1.5 rounded-full bg-[var(--sr-text-tertiary)]" />
            health unknown
          </span>
        ) : (
          health.map((pill) => (
            <span
              key={pill.label}
              title={pill.detail ?? pill.label}
              className={`flex items-center gap-1.5 rounded-full border px-2.5 py-1 admin-mono-font text-[10px] uppercase tracking-[0.1em] ${PILL_STYLES[pill.status]}`}
            >
              <span className={`w-1.5 h-1.5 rounded-full ${PILL_DOT[pill.status]}`} />
              {pill.label}
            </span>
          ))
        )}
      </div>

      {/* Environment badge */}
      <span
        data-testid="admin-env-badge"
        className={`rounded-full border px-2.5 py-1 admin-mono-font text-[10px] uppercase tracking-[0.14em] ${envStyle}`}
      >
        {environment}
      </span>

      {/* Sign out — ends the Clerk session, not just the admin token. */}
      <AdminSignOutButton />
    </header>
  );
}
