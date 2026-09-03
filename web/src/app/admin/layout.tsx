/**
 * /admin layout — the one shell every admin page mounts into (AD-01-12).
 *
 * AdminNavShell renders the 232px sidebar (Today, Data quality, Operations,
 * Customers, Agents) and the topbar (global search, env badge, health
 * pills) once at this layout level so React keeps the chrome mounted across
 * client-side route changes between /admin, /admin/tables,
 * /admin/corrections, /admin/data-health, /admin/scrapers, /admin/discovery,
 * /admin/firecrawl, /admin/identity, /admin/stripe-events, /admin/swarm,
 * /admin/tables/[name] and /admin/tables/admin_edits.
 *
 * The metadata { robots: noindex } stays — admin pages should never be
 * indexed.
 */

import type { Metadata } from "next";
import { AdminNavShell } from "@/components/AdminNavShell";
import "./admin.css";

export const metadata: Metadata = {
  title: "Internal",
  robots: {
    index: false,
    follow: false,
    googleBot: { index: false, follow: false },
  },
};

export default function AdminLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <AdminNavShell>{children}</AdminNavShell>;
}
