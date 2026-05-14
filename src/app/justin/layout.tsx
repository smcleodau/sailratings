import type { Metadata } from "next";

/**
 * /justin — internal page. Keep out of search engines.
 */
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
  return children;
}
