import type { MetadataRoute } from "next";

const SITE_URL = "https://sailratings.com";

/**
 * Sitemap — generated at build/request time by Next.
 *
 * Add new public-facing routes to PUBLIC_ROUTES below. Anything under
 * /brand/ or /report/ stays out (those are noindex'd at the page level
 * and disallowed in robots.ts).
 */
type Route = {
  path: string;
  changeFrequency?: MetadataRoute.Sitemap[number]["changeFrequency"];
  priority?: number;
};

const PUBLIC_ROUTES: Route[] = [
  { path: "/",        changeFrequency: "weekly",  priority: 1.0 },
  { path: "/ratings", changeFrequency: "monthly", priority: 0.8 },
  { path: "/fleet",   changeFrequency: "monthly", priority: 0.8 },
  { path: "/results", changeFrequency: "weekly",  priority: 0.8 },
];

export default function sitemap(): MetadataRoute.Sitemap {
  const lastModified = new Date();
  return PUBLIC_ROUTES.map(({ path, changeFrequency, priority }) => ({
    url: `${SITE_URL}${path}`,
    lastModified,
    changeFrequency,
    priority,
  }));
}
