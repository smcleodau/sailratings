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
    href: "/admin",
    label: "Chat",
    match: (p: string) =>
      p === "/admin" ||
      (p.startsWith("/admin") &&
        !p.startsWith("/admin/tables") &&
        !p.startsWith("/admin/corrections") &&
        !p.startsWith("/admin/scrapers") &&
        !p.startsWith("/admin/discovery") &&
        !p.startsWith("/admin/firecrawl")),
  },
  {
    href: "/admin/scrapers",
    label: "Scrapers",
    match: (p: string) => p.startsWith("/admin/scrapers"),
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
    <header className="admin-nav px-10 py-3 flex items-center justify-between gap-6 flex-wrap">
      {/* Brand */}
      <div className="flex items-center gap-2 flex-shrink-0">
        <img src="/mark-marine-on-light.svg" alt="" className="w-[22px] h-[22px]" />
        <Link href="/admin" prefetch className="admin-header-font font-extrabold text-[15px] tracking-[0.02em] text-[#162423]">
          SAIL<span className="text-[#FF4119]">RATINGS</span>
        </Link>
        <span className="admin-mono-font text-[9px] tracking-[0.18em] uppercase text-[#0C5F5C] border border-[#0C5F5C]/35 rounded-full px-2 py-[2px] ml-1">
          Admin
        </span>
      </div>

      {/* Section tabs */}
      <nav className="flex items-center gap-0 flex-1" aria-label="Admin sections">
        {SECTIONS.map((s) => {
          const active = s.match(pathname);
          return (
            <Link
              key={s.href}
              href={s.href}
              prefetch
              className={`admin-mono-font text-[10px] tracking-[0.14em] uppercase px-3 py-2 ${
                active ? "admin-link-active" : "admin-link"
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
          className="admin-mono-font text-[10px] tracking-[0.14em] uppercase admin-link flex items-center gap-2"
          aria-label="Sign out"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
