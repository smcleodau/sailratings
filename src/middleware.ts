import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export function middleware(request: NextRequest) {
  // Block /justin admin page unless NEXT_PUBLIC_ENABLE_ADMIN is set
  if (request.nextUrl.pathname.startsWith("/justin")) {
    if (process.env.NEXT_PUBLIC_ENABLE_ADMIN !== "true") {
      return NextResponse.rewrite(new URL("/", request.url));
    }
  }
}

export const config = {
  matcher: ["/justin/:path*"],
};
