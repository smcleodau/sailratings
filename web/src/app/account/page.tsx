import type { Metadata } from "next";
import MainNav from "@/components/MainNav";
import EditorialFooter from "@/components/EditorialFooter";
import AccountSettings from "@/components/account/AccountSettings";

export const metadata: Metadata = {
  title: "Account settings",
  description:
    "Manage your Sail Ratings profile, boats, notification preferences, billing, data export and account deletion.",
  alternates: { canonical: "/account" },
  robots: { index: false, follow: false },
};

/**
 * AUTH-01-03 — Account settings.
 *
 * Members control their own data: profile, boats, notification
 * preferences, billing (Stripe portal), a GDPR-style data export and
 * account deletion with the privacy-policy retention cascade.
 *
 * The page shell is server-rendered (nav + footer); the interactive
 * settings surface is a client component that talks to the API with the
 * Clerk session token.
 */
export default function AccountPage() {
  return (
    <main className="min-h-screen bg-[var(--sr-surface-page)] text-[var(--sr-text-primary)]">
      <MainNav theme="on-cream" />
      <div className="mx-auto w-full max-w-3xl px-6 py-12 sm:py-16">
        <header className="mb-10">
          <p className="sr-label mb-2">Your account</p>
          <h1 className="font-display text-3xl sm:text-4xl font-bold tracking-tight">
            Account settings
          </h1>
          <p className="mt-3 text-sm text-[var(--sr-text-secondary)] max-w-xl">
            Your data belongs to you. Manage your profile and boats, choose
            what we email you about, download everything we hold on you, or
            delete your account entirely.
          </p>
        </header>
        <AccountSettings />
      </div>
      <EditorialFooter />
    </main>
  );
}
