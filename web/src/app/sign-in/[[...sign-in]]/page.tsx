import { SignIn } from "@clerk/nextjs";
import type { Metadata } from "next";
import Link from "next/link";
import AuthFrame from "@/components/AuthFrame";
import { clerkAppearance } from "@/lib/clerkAppearance";

export const metadata: Metadata = {
  title: "Sign in",
  description: "Sign in to your SailRatings account.",
  robots: { index: false, follow: false },
};

/**
 * AUTH-01-02 — Sign-in page.
 *
 * Clerk's <SignIn /> mounted inside the SailRatings AuthFrame. Email +
 * password and Google OAuth are enabled in the Clerk dashboard; the "Forgot
 * password?" link and inline error states are rendered by Clerk's component
 * (styled via clerkAppearance).
 *
 * forceRedirectUrl="/" sends the user back to the page they came from — the
 * funnel — after a successful sign-in, and has precedence over any stale
 * redirect search params.
 *
 * When Clerk is not configured on this environment (no publishable key) the
 * page renders an explicit notice instead of crashing on the missing key —
 * same contract as the root layout and middleware.
 */
export default function SignInPage() {
  const clerkConfigured = Boolean(process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY);

  return (
    <AuthFrame strapline="Sign in to your account">
      {clerkConfigured ? (
        <div data-testid="sign-in-form" className="w-full">
          <SignIn
            appearance={clerkAppearance}
            routing="path"
            path="/sign-in"
            signUpUrl="/sign-up"
            forceRedirectUrl="/"
          />
        </div>
      ) : (
        <p data-testid="auth-not-configured" className="text-center text-sm leading-relaxed text-[var(--sr-text-secondary)]">
          Sign-in is not available on this environment.{" "}
          <Link href="/" className="text-[var(--sr-link)] underline underline-offset-2 hover:text-[var(--sr-link-hover)]">
            Return to SailRatings
          </Link>
          .
        </p>
      )}
    </AuthFrame>
  );
}
