import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse, type NextRequest } from "next/server";

const clerkPublishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const isProtectedRoute = createRouteMatcher(['/admin(.*)']);

// E2E / local harness escape hatch: when NEXT_PUBLIC_ADMIN_E2E_BYPASS is set
// (never in deployed environments — only the Playwright webServer and local
// dev harnesses set it), /admin routes skip the Clerk gate. The page-level
// admin-token gate (Authorization: Bearer …) still applies.
const adminE2eBypass = !!process.env.NEXT_PUBLIC_ADMIN_E2E_BYPASS;

function adminHostRedirect(req: NextRequest) {
  const url = req.nextUrl;
  const hostname = req.headers.get('host');
  if (hostname === 'admin.sailratings.com' && url.pathname === '/') {
    return NextResponse.redirect(new URL('/admin/swarm', req.url));
  }
  return null;
}

// Full auth: used when Clerk is configured.
const clerkConfiguredMiddleware = clerkMiddleware(async (auth, req) => {
  const redirect = adminHostRedirect(req);
  if (redirect) return redirect;

  if (isProtectedRoute(req) && !adminE2eBypass) {
    const authObject = await auth();
    if (!authObject.userId) {
      return NextResponse.redirect(new URL('/sign-in', req.url));
    }
  }

  return NextResponse.next();
});

// Fallback: when Clerk is NOT configured, render public pages normally and
// refuse access to protected admin routes (503) instead of crashing on the
// missing publishable key. Auth re-engages automatically once
// NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY and CLERK_SECRET_KEY are provisioned.
function unconfiguredMiddleware(req: NextRequest) {
  const redirect = adminHostRedirect(req);
  if (redirect) return redirect;

  if (isProtectedRoute(req) && !adminE2eBypass) {
    return new NextResponse('Authentication is not configured on this environment.', {
      status: 503,
    });
  }

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
