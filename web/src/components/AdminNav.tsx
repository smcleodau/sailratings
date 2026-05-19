"use client";

import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import { LogOut } from "lucide-react";

interface AdminNavProps {
  /** Optional element rendered after the tabs (e.g. conversation badge on chat). */
  rightSlot?: React.ReactNode;
}

const SECTIONS = [
  {
    href: "/justin",
    label: "Chat",
    match: (p: string) =>
      p === "/justin" ||
      (p.startsWith("/justin") &&
        !p.startsWith("/justin/tables") &&
        !p.startsWith("/justin/corrections") &&
        !p.startsWith("/justin/scrapers") &&
        !p.startsWith("/justin/discovery")),
  },
  {
    href: "/justin/scrapers",
    label: "Scrapers",
    match: (p: string) => p.startsWith("/justin/scrapers"),
  },
  {
    href: "/justin/discovery",
    label: "Discovery",
    match: (p: string) => p.startsWith("/justin/discovery"),
  },
  {
    href: "/justin/tables",
    label: "Tables",
    match: (p: string) =>
      p.startsWith("/justin/tables") && p !== "/justin/tables/admin_edits",
  },
  {
    href: "/justin/corrections",
    label: "Corrections",
    match: (p: string) => p.startsWith("/justin/corrections"),
  },
  {
    href: "/justin/tables/admin_edits",
    label: "Audit",
    match: (p: string) => p === "/justin/tables/admin_edits",
  },
];

export function AdminNav({ rightSlot }: AdminNavProps) {
  const pathname = usePathname();
  const router = useRouter();

  const handleSignOut = () => {
    if (typeof window === "undefined") return;
    localStorage.removeItem("admin_token");
    router.push("/justin");
    router.refresh();
  };

  return (
    <header className="flex-shrink-0 border-b border-white/10 bg-navy px-6 py-3 flex items-center justify-between gap-6 flex-wrap">
      {/* Brand */}
      <Link
        href="/justin"
        prefetch
        className="flex items-baseline gap-2 group flex-shrink-0"
      >
        <span className="brand-wordmark text-white text-sm">Sail Ratings</span>
        <span className="data-mono text-[10px] uppercase tracking-[0.18em] text-white/30 group-hover:text-white/60 transition-colors">
          Admin
        </span>
      </Link>

      {/* Section tabs */}
      <nav className="flex items-center gap-0 -mb-3 flex-1" aria-label="Admin sections">
        {SECTIONS.map((s) => {
          const active = s.match(pathname);
          return (
            <Link
              key={s.href}
              href={s.href}
              prefetch
              className={`relative data-mono text-[11px] uppercase tracking-[0.16em] px-4 py-3 transition-colors ${
                active ? "text-brass" : "text-white/40 hover:text-white/70"
              }`}
            >
              {s.label}
              {active && (
                <span className="absolute left-3 right-3 bottom-0 h-px bg-brass" aria-hidden />
              )}
            </Link>
          );
        })}
      </nav>

      {/* Right slot + sign out */}
      <div className="flex items-center gap-4 flex-shrink-0">
        {rightSlot}
        <button
          onClick={handleSignOut}
          className="inline-flex items-center gap-1.5 data-mono text-[10px] uppercase tracking-[0.16em] text-white/30 hover:text-white/70 transition-colors"
          title="Sign out"
        >
          <LogOut size={12} strokeWidth={1.75} />
          Sign Out
        </button>
      </div>
    </header>
  );
}
