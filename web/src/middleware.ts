import { clerkMiddleware, createRouteMatcher } from "@clerk/nextjs/server";
import { NextResponse, type NextRequest } from "next/server";

const clerkPublishableKey = process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;
const isProtectedRoute = createRouteMatcher(['/admin(.*)']);

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

  if (isProtectedRoute(req)) {
    const authObject = await auth();
    if (!authObject.userId) {
      return NextResponse.redirect(new URL('/sign-in', req.url));
    }
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
