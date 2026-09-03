import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse, type NextRequest } from "next/server";

const clerkPublishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const isProtectedRoute = createRouteMatcher(['/admin(.*)']);

// E2E / local harness escape hatch: when NEXT_PUBLIC_ADMIN_E2E_BYPASS is set
// (never in deployed environments — only the Playwright webServer and local
// dev harnesses set it), /admin routes skip the Clerk gate. The page-level
// admin-token gate (Authorization: Bearer …) still applies.
const adminE2eBypass = !!process.env.NEXT_PUBLIC_ADMIN_E2E_BYPASS;

/**
 * Admin responses must never be cached.
 *
 * Next.js was emitting `cache-control: public, max-age=0, s-maxage=60,
 * stale-while-revalidate=86400` on /admin — including on the sign-in
 * redirect. `stale-while-revalidate=86400` lets a browser keep serving the
 * previously-rendered admin HTML for 24 hours, so signing out (or even
 * clearing cookies, which does not clear the HTTP cache) still showed the
 * admin UI from disk. `public` would also let a shared cache hold a
 * signed-in render.
 */
function noStore(res: NextResponse): NextResponse {
  res.headers.set("Cache-Control", "private, no-store, no-cache, must-revalidate");
  res.headers.set("Pragma", "no-cache");
  res.headers.set("Vary", "Cookie, Authorization");
  return res;
}

function adminHostRedirect(req: NextRequest) {
  const url = req.nextUrl;
  const hostname = req.headers.get('host');
  if (hostname === 'admin.sailratings.com' && url.pathname === '/') {
    return NextResponse.redirect(new URL('/admin', req.url));
  }
  return null;
}

// Full auth: used when Clerk is configured.
const clerkConfiguredMiddleware = clerkMiddleware(async (auth, req) => {
  const redirect = adminHostRedirect(req);
  if (redirect) return redirect;

  if (isProtectedRoute(req)) {
    if (!adminE2eBypass) {
      const authObject = await auth();
      if (!authObject.userId) {
        return noStore(NextResponse.redirect(new URL('/sign-in', req.url)));
      }
    }
    return noStore(NextResponse.next());
  }

  return NextResponse.next();
});

// Fallback: when Clerk is NOT configured (local dev / verification rigs),
// render everything — the admin pages self-gate with the shared admin
// password (admin_token in localStorage → Bearer on /v1/admin/*, enforced
// by the API). Auth re-engages automatically once
// NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY are provisioned.
function unconfiguredMiddleware(req: NextRequest) {
  const redirect = adminHostRedirect(req);
  if (redirect) return redirect;

  if (isProtectedRoute(req)) return noStore(NextResponse.next());

  return NextResponse.next();
}

export default clerkPublishableKey
  ? clerkConfiguredMiddleware
  : unconfiguredMiddleware;

export const config = {
  matcher: [
    // Skip Next.js internals and all static files, unless found in search params
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    // Always run for API routes
    '/(api|trpc)(.*)',
    '/__clerk/:path*',
  ],
};
