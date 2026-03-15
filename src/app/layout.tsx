import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "SailingRatings — What is your IRC rating really costing you?",
  description:
    "We've analysed 6,000+ IRC boats, 31,000 race results, and thousands of certificates to find where your rating points are hiding. Search your boat and get your optimisation report.",
  openGraph: {
    title: "SailingRatings — IRC Rating Analysis",
    description:
      "Search your IRC-rated yacht. See where your rating points are hiding.",
    url: "https://sailingratings.com",
    siteName: "SailingRatings",
    type: "website",
  },
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">
        {/* Page border with corner brackets */}
        <div className="page-border" aria-hidden="true">
          <span className="corner-bl" />
          <span className="corner-br" />
        </div>

        {children}
      </body>
    </html>
  );
}
