import { ClerkProvider } from "@clerk/nextjs";
import type { Metadata } from "next";
import { headers } from "next/headers";
import "./globals.css";
import { PostHogProvider } from "@/components/PostHogProvider";
import { isProdHost } from "@/lib/seo";

const SITE_URL = "https://sailratings.com";

export async function generateMetadata(): Promise<Metadata> {
  const host = (await headers()).get("host");
  const allowIndex = isProdHost(host);

  return {
    metadataBase: new URL(SITE_URL),
    title: {
      default: "Sail Ratings — The unseen advantage in your rating",
      template: "%s | Sail Ratings",
    },
    description:
      "Sail Ratings analyzes 31,000+ race results and every IRC and ORC certificate ever published to find where your rating points are hiding.",
    applicationName: "Sail Ratings",
    keywords: [
      "IRC rating",
      "ORC rating",
      "sailing handicap",
      "yacht racing",
      "rating optimization",
      "IRC certificate",
      "ORC certificate",
      "race results analysis",
    ],
    authors: [{ name: "Sail Ratings" }],
    creator: "Sail Ratings",
    publisher: "Sail Ratings",
    alternates: {
      canonical: "/",
    },
    openGraph: {
      title: "Sail Ratings — IRC & ORC Rating Analysis",
      description:
        "The unseen advantage in your rating. We analyze 31,000+ race results and every certificate ever published.",
      url: SITE_URL,
      siteName: "Sail Ratings",
      type: "website",
      locale: "en_US",
      images: [
        {
          url: "/og-image.png",
          width: 1200,
          height: 630,
          alt: "Sail Ratings — The unseen advantage in your rating",
        },
      ],
    },
    twitter: {
      card: "summary_large_image",
      title: "Sail Ratings — IRC & ORC Rating Analysis",
      description: "The unseen advantage in your rating.",
      images: ["/og-image.png"],
    },
    icons: {
      icon: [
        { url: "/favicon.ico", sizes: "any" },
        { url: "/icon-192.png", type: "image/png", sizes: "192x192" },
        { url: "/icon-512.png", type: "image/png", sizes: "512x512" },
      ],
      apple: [{ url: "/apple-touch-icon.png", sizes: "180x180" }],
      shortcut: ["/favicon.ico"],
    },
    manifest: "/site.webmanifest",
    robots: allowIndex
      ? {
          index: true,
          follow: true,
          googleBot: {
            index: true,
            follow: true,
            "max-snippet": -1,
            "max-image-preview": "large",
            "max-video-preview": -1,
          },
        }
      : {
          index: false,
          follow: false,
          nocache: true,
          googleBot: { index: false, follow: false, noimageindex: true },
        },
    category: "sports",
  };
}

// JSON-LD structured data — Organization + WebSite + SoftwareApplication.
// Inlined into <head> via a server-rendered <script type="application/ld+json">.
const jsonLd = [
  {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: "Sail Ratings",
    url: SITE_URL,
    logo: `${SITE_URL}/icon-512.png`,
    sameAs: [],
    description:
      "IRC and ORC rating analysis powered by 31,000+ race results and every published certificate.",
  },
  {
    "@context": "https://schema.org",
    "@type": "WebSite",
    name: "Sail Ratings",
    url: SITE_URL,
    potentialAction: {
      "@type": "SearchAction",
      target: `${SITE_URL}/?q={search_term_string}`,
      "query-input": "required name=search_term_string",
    },
  },
  {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: "Sail Ratings",
    applicationCategory: "SportsApplication",
    operatingSystem: "Web",
    description:
      "Analyze your IRC or ORC certificate against 31,000+ race results to find hidden rating advantages.",
    url: SITE_URL,
    offers: {
      "@type": "Offer",
      priceCurrency: "USD",
      price: "0",
    },
  },
];

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <head>
        <script
          type="application/ld+json"
          // Safe: JSON.stringify of a static object literal — no user input.
          dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
        />
      </head>
      <body className="antialiased">
        <ClerkProvider>
          <PostHogProvider>{children}</PostHogProvider>
        </ClerkProvider>
      </body>
    </html>
  );
}