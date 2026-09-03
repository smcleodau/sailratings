"use client";

import { usePathname, useRouter } from "next/navigation";
import Link from "next/link";
import Image from "next/image";

interface AdminNavProps {
  /** Optional element rendered after the tabs (e.g. conversation badge on chat). */
  rightSlot?: React.ReactNode;
}

const SECTIONS = [
  {
    href: "/admin",
    label: "Today",
    match: (p: string) => p === "/admin",
  },
  {
    href: "/admin/chat",
    label: "Chat",
    match: (p: string) =>
      p === "/admin/chat" ||
      (p.startsWith("/admin") &&
        p !== "/admin" &&
        !p.startsWith("/admin/tables") &&
        !p.startsWith("/admin/corrections") &&
        !p.startsWith("/admin/scrapers") &&
        !p.startsWith("/admin/discovery") &&
        !p.startsWith("/admin/firecrawl") &&
        !p.startsWith("/admin/identity") &&
        !p.startsWith("/admin/data-health") &&
        !p.startsWith("/admin/users") &&
        !p.startsWith("/admin/orders") &&
        !p.startsWith("/admin/billing")),
  },
  {
    href: "/admin/users",
    label: "Users & plans",
    match: (p: string) => p.startsWith("/admin/users"),
  },
  {
    href: "/admin/orders",
    label: "Reports & orders",
    match: (p: string) => p.startsWith("/admin/orders"),
  },
  {
    href: "/admin/billing",
    label: "Stripe & pricing",
    match: (p: string) => p.startsWith("/admin/billing"),
  },
  {
    href: "/admin/identity",
    label: "Identity",
    match: (p: string) => p.startsWith("/admin/identity"),
  },
  {
    href: "/admin/scrapers",
    label: "Scrapers",
    match: (p: string) => p.startsWith("/admin/scrapers"),
  },
  {
    href: "/admin/data-health",
    label: "Data health",
    match: (p: string) => p.startsWith("/admin/data-health"),
  },
  {
    href: "/sources-policy",
    label: "Sources",
    match: (p: string) => p.startsWith("/sources-policy"),
  },
  {
    href: "/admin/discovery",
    label: "Discovery",
    match: (p: string) => p.startsWith("/admin/discovery"),
  },
  {
    href: "/admin/firecrawl",
    label: "Firecrawl",
    match: (p: string) => p.startsWith("/admin/firecrawl"),
  },
  {
    href: "/admin/tables",
    label: "Tables",
    match: (p: string) =>
      p.startsWith("/admin/tables") && p !== "/admin/tables/admin_edits",
  },
  {
    href: "/admin/corrections",
    label: "Corrections",
    match: (p: string) => p.startsWith("/admin/corrections"),
  },
  {
    href: "/admin/tables/admin_edits",
    label: "Audit",
    match: (p: string) => p === "/admin/tables/admin_edits",
  },
];

export function AdminNav({ rightSlot }: AdminNavProps) {
  const pathname = usePathname();
  const router = useRouter();

  const handleSignOut = () => {
    if (typeof window === "undefined") return;
    localStorage.removeItem("admin_token");
    router.push("/admin");
    router.refresh();
  };

  return (
    <header className="sr-app-header px-8 py-3 flex items-center justify-between gap-6 flex-wrap bg-[var(--sr-surface-deep)] border-b border-[var(--sr-border-subtle)]">
      {/* Brand Header using Official Outlined Wordmark SVG */}
      <div className="flex items-center gap-2.5 flex-shrink-0">
        <Link href="/admin" prefetch className="flex items-center gap-2">
          <Image
            src="/brand/wordmark-outlined.svg"
            alt="SailRatings"
            width={140}
            height={21}
            priority
            className="h-5 w-auto"
          />
        </Link>
        <span className="sr-label text-[9px] tracking-[0.18em] uppercase text-[var(--sr-marine-200)] border border-[var(--sr-marine-600)]/40 rounded-full px-2 py-[2px] ml-1">
          Admin
        </span>
      </div>

      {/* Section tabs */}
      <nav className="flex items-center gap-1 flex-1" aria-label="Admin sections">
        {SECTIONS.map((s) => {
          const active = s.match(pathname);
          return (
            <Link
              key={s.href}
              href={s.href}
              prefetch
              className={`sr-label text-[10px] tracking-[0.14em] uppercase px-3 py-2 transition-colors ${
                active
                  ? "text-[var(--sr-paper)] border-b-2 border-[var(--sr-action)]"
                  : "text-[var(--sr-text-secondary)] hover:text-[var(--sr-paper)]"
              }`}
            >
              {s.label}
            </Link>
          );
        })}
      </nav>

      {/* Right controls */}
      <div className="flex items-center gap-4 flex-shrink-0">
        {rightSlot}
        <button
          onClick={handleSignOut}
          className="sr-label text-[10px] tracking-[0.14em] uppercase text-[var(--sr-text-secondary)] hover:text-[var(--sr-paper)] transition-colors flex items-center gap-2"
          aria-label="Sign out"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
