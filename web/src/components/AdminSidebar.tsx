"use client";

/**
 * AdminSidebar (AD-01-12) — the 232px section rail of the admin shell.
 *
 * Sections are the five product surfaces of the admin console:
 *   Today, Data quality, Operations, Customers, Agents.
 * Counts come from GET /v1/admin/overview (AD-01-13) and render 0 until
 * that endpoint exists. Existing routes are grouped under their section.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { AdminOverviewCounts } from "@/lib/adminApi";
import {
  AgentsIcon,
  CustomersIcon,
  DataQualityIcon,
  OperationsIcon,
  TodayIcon,
  type AdminIconProps,
} from "@/components/admin/AdminIcons";

export const ADMIN_SIDEBAR_WIDTH = 232;

interface SidebarSection {
  key: keyof AdminOverviewCounts;
  label: string;
  icon: (props: AdminIconProps) => React.ReactElement;
  items: { href: string; label: string }[];
}

const SECTIONS: SidebarSection[] = [
  {
    key: "today",
    label: "Today",
    icon: TodayIcon,
    items: [{ href: "/admin", label: "Today" }],
  },
  {
    key: "data_quality",
    label: "Data quality",
    icon: DataQualityIcon,
    items: [
      { href: "/admin/data-health", label: "Data health" },
      { href: "/admin/corrections", label: "Corrections" },
      { href: "/admin/identity", label: "Identity" },
      { href: "/admin/tables", label: "Tables" },
    ],
  },
  {
    key: "operations",
    label: "Operations",
    icon: OperationsIcon,
    items: [
      { href: "/admin/scrapers", label: "Scrapers" },
      { href: "/admin/discovery", label: "Discovery" },
      { href: "/admin/firecrawl", label: "Firecrawl" },
    ],
  },
  {
    key: "customers",
    label: "Customers",
    icon: CustomersIcon,
    items: [{ href: "/admin/stripe-events", label: "Payments" }],
  },
  {
    key: "agents",
    label: "Agents",
    icon: AgentsIcon,
    items: [
      { href: "/admin/swarm", label: "Swarm" },
      { href: "/admin/tables/admin_edits", label: "Audit" },
    ],
  },
];

function isItemActive(pathname: string, href: string): boolean {
  if (href === "/admin") {
    // "Today" is the exact root only — everything else belongs to a section.
    return pathname === "/admin";
  }
  if (href === "/admin/tables") {
    return (
      pathname.startsWith("/admin/tables") &&
      pathname !== "/admin/tables/admin_edits"
    );
  }
  return pathname === href || pathname.startsWith(`${href}/`);
}

function isSectionActive(pathname: string, section: SidebarSection): boolean {
  return section.items.some((item) => isItemActive(pathname, item.href));
}

interface AdminSidebarProps {
  counts: AdminOverviewCounts;
}

export function AdminSidebar({ counts }: AdminSidebarProps) {
  const pathname = usePathname();

  return (
    <aside
      data-testid="admin-sidebar"
      className="flex flex-col flex-shrink-0 min-h-screen sticky top-0 self-start bg-[var(--sr-dusk-card)] border-r border-[var(--sr-border-subtle)]"
      style={{ width: ADMIN_SIDEBAR_WIDTH }}
      aria-label="Admin sections"
    >
      {/* Brand */}
      <div className="flex items-center gap-2.5 px-5 h-16 border-b border-[var(--sr-border-subtle)]">
        {/* SailRatings mark — inline SVG, no image dependency */}
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          aria-hidden="true"
          className="text-[var(--sr-dusk)] flex-shrink-0"
        >
          <path
            d="M12 2v18M12 4l7.5 13H12M12 8l-6 9h6"
            stroke="currentColor"
            strokeWidth="1.6"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        <span className="sr-label text-[11px] tracking-[0.22em] uppercase text-[var(--sr-text-primary)] font-semibold">
          SailRatings
        </span>
        <span className="sr-label text-[8px] tracking-[0.18em] uppercase text-[var(--sr-dusk)] border border-[var(--sr-border-strong)] rounded-full px-1.5 py-[1px]">
          Admin
        </span>
      </div>

      {/* Sections */}
      <nav className="flex-1 overflow-y-auto py-4 px-3" aria-label="Admin navigation">
        <ul className="space-y-4">
          {SECTIONS.map((section) => {
            const SectionIcon = section.icon;
            const sectionActive = isSectionActive(pathname, section);
            const count = counts[section.key] ?? 0;
            return (
              <li key={section.key}>
                <div
                  className={`flex items-center justify-between px-2 mb-1 ${
                    sectionActive
                      ? "text-[var(--sr-dusk)]"
                      : "text-[var(--sr-text-label)]"
                  }`}
                >
                  <span className="flex items-center gap-2">
                    <SectionIcon size={14} strokeWidth={1.75} />
                    <span className="sr-label text-[9px] tracking-[0.2em] uppercase font-semibold">
                      {section.label}
                    </span>
                  </span>
                  <span
                    data-testid={`sidebar-count-${section.key}`}
                    className="admin-mono-font text-[10px] tabular-nums text-[var(--sr-text-tertiary)]"
                    aria-label={`${section.label} count`}
                  >
                    {count}
                  </span>
                </div>
                <ul className="space-y-0.5">
                  {section.items.map((item) => {
                    const active = isItemActive(pathname, item.href);
                    return (
                      <li key={item.href}>
                        <Link
                          href={item.href}
                          prefetch
                          aria-current={active ? "page" : undefined}
                          className={`flex items-center gap-2 rounded-md px-2.5 py-1.5 text-[13px] transition-colors ${
                            active
                              ? "bg-[var(--sr-dusk-interactive)] text-[var(--sr-text-primary)] shadow-[inset_2px_0_0_var(--sr-dusk)]"
                              : "text-[var(--sr-text-secondary)] hover:text-[var(--sr-text-primary)] hover:bg-[var(--sr-dusk-raised)]"
                          }`}
                        >
                          {item.label}
                        </Link>
                      </li>
                    );
                  })}
                </ul>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer */}
      <div className="px-5 py-4 border-t border-[var(--sr-border-subtle)]">
        <span className="admin-mono-font text-[9px] tracking-[0.14em] uppercase text-[var(--sr-text-tertiary)]">
          AD-01-12 · Dusk
        </span>
      </div>
    </aside>
  );
}
