/**
 * /justin layout — shared chrome for every admin page.
 *
 * The AdminNav is rendered once at this layout level so React keeps the
 * nav DOM mounted across client-side route changes between /justin,
 * /justin/tables, /justin/corrections, /justin/tables/[name] and
 * /justin/tables/admin_edits.
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

export default function JustinLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return <AdminNavShell>{children}</AdminNavShell>;
}
