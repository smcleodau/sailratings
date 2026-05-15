import type { Metadata } from "next";

/**
 * /report/* — per-user report views accessed via a one-off token.
 * Must never be indexed (also excluded in robots.ts).
 */
export const metadata: Metadata = {
  title: "Your report",
  robots: {
    index: false,
    follow: false,
    nocache: true,
    googleBot: { index: false, follow: false, noimageindex: true },
  },
};

export default function ReportLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
