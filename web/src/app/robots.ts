import type { MetadataRoute } from "next";
import { headers } from "next/headers";
import { isProdHost } from "@/lib/seo";

const SITE_URL = "https://sailratings.com";

export default async function robots(): Promise<MetadataRoute.Robots> {
  const host = (await headers()).get("host");

  // Non-production hosts (dev.sailratings.com, localhost, preview deploys) must
  // never be indexed — anything that finds dev hits a Disallow: / wall.
  if (!isProdHost(host)) {
    return {
      rules: [{ userAgent: "*", disallow: "/" }],
    };
  }

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
