import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Sail Ratings — The unseen advantage in your rating",
  description:
    "We analyze 31,000 race results and every certificate ever published to find where your IRC and ORC rating points are hiding.",
  openGraph: {
    title: "Sail Ratings — IRC & ORC Rating Analysis",
    description: "The unseen advantage in your rating.",
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
      <body className="antialiased">{children}</body>
    </html>
  );
}
