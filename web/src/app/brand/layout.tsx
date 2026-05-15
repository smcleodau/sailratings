import type { Metadata } from "next";

/**
 * /brand/* — internal design previews. Keep them out of search engines
 * (also excluded in robots.ts as a belt-and-braces measure).
 */
export const metadata: Metadata = {
  title: "Brand preview",
  robots: {
    index: false,
    follow: false,
    googleBot: { index: false, follow: false },
  },
};

export default function BrandLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return children;
}
