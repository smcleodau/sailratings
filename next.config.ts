import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async headers() {
    return [
      {
        // HTML pages — short CDN cache with stale-while-revalidate so users
        // see new deploys within a minute, without losing CDN benefit. Browser
        // always revalidates (max-age=0). Excludes Next.js static + image
        // assets and common file extensions.
        source: "/((?!_next/static|_next/image|favicon|fonts|.*\\.(?:svg|png|jpg|jpeg|webp|gif|ico|woff2?|css|js)).*)",
        headers: [
          {
            key: "Cache-Control",
            value: "public, max-age=0, s-maxage=60, stale-while-revalidate=86400",
          },
        ],
      },
      {
        // Next.js hashed static assets — content-hashed, immutable.
        source: "/_next/static/:path*",
        headers: [
          {
            key: "Cache-Control",
            value: "public, max-age=31536000, immutable",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
