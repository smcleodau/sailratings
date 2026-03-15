import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sail Ratings — What is your IRC rating really costing you?",
  description:
    "6,197 boats. 31,000 race results. We know where your points are hiding. Search your boat and get your optimisation report.",
  openGraph: {
    title: "Sail Ratings — IRC Rating Analysis",
    description:
      "Search your IRC-rated yacht. See where your rating points are hiding.",
    url: "https://sailratings.com",
    siteName: "Sail Ratings",
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
