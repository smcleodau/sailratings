import type { NextConfig } from "next";

// Single source of truth: ENVIRONMENT drives the API base + admin gate.
// Individual NEXT_PUBLIC_* values can still be overridden if explicitly set.
const ENV = (process.env.ENVIRONMENT || "local").toLowerCase();

const API_BASES: Record<string, string> = {
  local: "http://localhost:4100/v1",
  dev: "https://api.dev.sailratings.com/v1",
  production: "https://api.sailratings.com/v1",
};

const ENABLE_ADMIN_BY_ENV: Record<string, string> = {
  local: "true",
  dev: "true",
  production: "false",
};

if (!(ENV in API_BASES)) {
  throw new Error(`Unknown ENVIRONMENT=${ENV}; expected one of ${Object.keys(API_BASES).join(", ")}`);
}

const nextConfig: NextConfig = {
  output: "standalone",
  env: {
    NEXT_PUBLIC_API_BASE: process.env.NEXT_PUBLIC_API_BASE || API_BASES[ENV],
    NEXT_PUBLIC_ENABLE_ADMIN:
      process.env.NEXT_PUBLIC_ENABLE_ADMIN || ENABLE_ADMIN_BY_ENV[ENV],
  },
  async headers() {
    return [
      {
        source: "/((?!_next/static|_next/image|favicon|fonts|.*\\.(?:svg|png|jpg|jpeg|webp|gif|ico|woff2?|css|js)).*)",
        headers: [
          {
            key: "Cache-Control",
            value: "public, max-age=0, s-maxage=60, stale-while-revalidate=86400",
          },
        ],
      },
      {
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
