import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";
import { isProdHost } from "@/lib/seo";

export function middleware(request: NextRequest) {
  // Block /justin admin page unless NEXT_PUBLIC_ENABLE_ADMIN is set
  if (request.nextUrl.pathname.startsWith("/justin")) {
    if (process.env.NEXT_PUBLIC_ENABLE_ADMIN !== "true") {
      return NextResponse.rewrite(new URL("/", request.url));
    }
  }

  const response = NextResponse.next();

  // Belt-and-braces noindex for any non-production host (dev, previews, local).
  // Covers HTML responses and non-HTML assets (og-image, sitemap, etc.) that
  // a <meta name=robots> can't reach.
  if (!isProdHost(request.headers.get("host"))) {
    response.headers.set("X-Robots-Tag", "noindex, nofollow");
  }

  return response;
}

export const config = {
  // Run on every request except Next internals.
  matcher: ["/((?!_next/static|_next/image|_next/data).*)"],
};
