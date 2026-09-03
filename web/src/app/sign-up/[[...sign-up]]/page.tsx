import { SignUp } from "@clerk/nextjs";
import type { Metadata } from "next";
import Link from "next/link";
import AuthFrame from "@/components/AuthFrame";
import { clerkAppearance } from "@/lib/clerkAppearance";

export const metadata: Metadata = {
  title: "Create account",
  description: "Create your SailRatings account.",
  robots: { index: false, follow: false },
};

/**
 * AUTH-01-02 — Create-account page.
 *
 * Clerk's <SignUp /> mounted inside the SailRatings AuthFrame. Email +
 * password and Google OAuth are enabled in the Clerk dashboard; email
 * verification, password rules and inline error states are rendered by
 * Clerk's component (styled via clerkAppearance).
 *
 * forceRedirectUrl="/" sends a new account straight back to the funnel after
 * sign-up, and has precedence over any stale redirect search params.
 *
 * When Clerk is not configured on this environment (no publishable key) the
 * page renders an explicit notice instead of crashing on the missing key —
 * same contract as the root layout and middleware.
 */
export default function SignUpPage() {
  const clerkConfigured = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

  return (
    <AuthFrame strapline="Create your account">
      {clerkConfigured ? (
        <div data-testid="sign-up-form" className="w-full">
          <SignUp
            appearance={clerkAppearance}
            routing="path"
            path="/sign-up"
            signInUrl="/sign-in"
            forceRedirectUrl="/"
          />
        </div>
      ) : (
        <p data-testid="auth-not-configured" className="text-center text-sm leading-relaxed text-[var(--sr-text-secondary)]">
          Account creation is not available on this environment.{" "}
          <Link href="/" className="text-[var(--sr-link)] underline underline-offset-2 hover:text-[var(--sr-link-hover)]">
            Return to SailRatings
          </Link>
          .
        </p>
      )}
    </AuthFrame>
  );
}
