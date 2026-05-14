import type { MetadataRoute } from "next";

const SITE_URL = "https://sailratings.com";

/**
 * robots.txt — generated at build/request time by Next.
 *
 * - Allow everything crawlable.
 * - Disallow internal design previews under /brand/ and per-user reports under /report/.
 * - Point crawlers at the sitemap.
 */
export default function robots(): MetadataRoute.Robots {
  return {
    rules: [
      {
        userAgent: "*",
        allow: "/",
        disallow: ["/brand/", "/report/", "/justin/", "/api/"],
      },
    ],
    sitemap: `${SITE_URL}/sitemap.xml`,
    host: SITE_URL,
  };
}
