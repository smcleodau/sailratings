"use client";

/**
 * Admin topbar "Sign out".
 *
 * This used to only `localStorage.removeItem("admin_token")` and re-navigate,
 * which left the Clerk session completely untouched — so signing out of the
 * admin and then reloading /admin walked straight back in, because the Clerk
 * middleware still saw an active session. Nothing else in the app called
 * Clerk's signOut either.
 *
 * Signing out now ends both: the page-level admin token and the Clerk session.
 *
 * The publishable key is inlined at build time, so the branch below is a
 * build constant — each variant is its own component, so hook order is stable.
 */

import { useClerk } from "@clerk/nextjs";
import { useRouter } from "next/navigation";
import { SignOutIcon } from "@/components/admin/AdminIcons";

const CLERK_ENABLED = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

const BUTTON_CLASS =
  "sr-label text-[10px] tracking-[0.14em] uppercase text-[var(--sr-text-secondary)] hover:text-[var(--sr-text-primary)] transition-colors flex items-center gap-1.5";

function clearAdminToken() {
  if (typeof window === "undefined") return;
  localStorage.removeItem("admin_token");
}

function Button({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick} className={BUTTON_CLASS} aria-label="Sign out">
      <SignOutIcon size={13} />
      Sign out
    </button>
  );
}

/** Clerk configured: end the Clerk session as well as the admin token. */
function ClerkSignOut() {
  const { signOut } = useClerk();
  return (
    <Button
      onClick={() => {
        clearAdminToken();
        void signOut({ redirectUrl: "/sign-in" });
      }}
    />
  );
}

/** Clerk unconfigured (local rigs): the admin token is the only session. */
function LocalSignOut() {
  const router = useRouter();
  return (
    <Button
      onClick={() => {
        clearAdminToken();
        router.push("/admin");
        router.refresh();
      }}
    />
  );
}

export function AdminSignOutButton() {
  return CLERK_ENABLED ? <ClerkSignOut /> : <LocalSignOut />;
}
