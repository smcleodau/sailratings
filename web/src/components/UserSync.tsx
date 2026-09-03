"use client";

import { useEffect, useRef } from "react";
import { useAuth } from "@clerk/nextjs";
import { syncCurrentUser } from "@/lib/api";

/**
 * Mirrors the signed-in Clerk user into our `users` table.
 *
 * Renders nothing. Mounted once in the root layout so that signing in is
 * enough to create the local row — previously only a checkout attempt did
 * that, which left the admin Customers zone empty and `last_seen_at` unset.
 */
export default function UserSync() {
  const { isLoaded, isSignedIn, getToken, userId } = useAuth();
  const syncedFor = useRef<string | null>(null);

  useEffect(() => {
    if (!isLoaded || !isSignedIn || !userId) return;
    if (syncedFor.current === userId) return; // once per signed-in user
    syncedFor.current = userId;

    let cancelled = false;
    (async () => {
      const token = await getToken().catch(() => null);
      if (!token || cancelled) return;
      // Best-effort: a failure here must never block rendering.
      await syncCurrentUser(token).catch(() => null);
    })();
    return () => {
      cancelled = true;
    };
  }, [isLoaded, isSignedIn, userId, getToken]);

  return null;
}
