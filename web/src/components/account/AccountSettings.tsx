"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuth, useUser } from "@clerk/nextjs";
import {
  AccountSettings as AccountSettingsData,
  deleteAccount,
  exportAccountData,
  getAccountSettings,
  updateAccountProfile,
  updateNotificationPrefs,
} from "@/lib/api";

type Status = { kind: "idle" | "saving" | "saved" | "error"; message?: string };

const NOTIFICATION_OPTIONS: {
  key: keyof Pick<
    AccountSettingsData,
    | "notify_product_updates"
    | "notify_rating_changes"
    | "notify_event_reminders"
    | "notify_marketing"
  >;
  label: string;
  description: string;
}[] = [
  {
    key: "notify_rating_changes",
    label: "Rating changes",
    description: "When a boat you follow has a new IRC/ORC rating snapshot.",
  },
  {
    key: "notify_event_reminders",
    label: "Event reminders",
    description: "Upcoming regattas and closing entry deadlines.",
  },
  {
    key: "notify_product_updates",
    label: "Product updates",
    description: "New analysis features and report improvements.",
  },
  {
    key: "notify_marketing",
    label: "News & offers",
    description: "Occasional news, guides and promotions. Off by default.",
  },
];

function downloadJson(filename: string, data: unknown) {
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

const CLERK_ENABLED = !!process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY;

/**
 * Clerk is optional (see layout.tsx): when no publishable key is
 * configured, ClerkProvider isn't mounted, so calling useAuth/useUser
 * here would crash. Gate the whole Clerk-backed component behind
 * CLERK_ENABLED at the top level, same pattern as AdminSignOutButton.
 */
export default function AccountSettings() {
  if (!CLERK_ENABLED) {
    return (
      <div className="sr-card text-center" data-testid="account-sign-in-prompt">
        <p className="text-sm text-[var(--sr-text-secondary)]">
          Account settings are not available in this environment.
        </p>
      </div>
    );
  }
  return <ClerkAccountSettings />;
}

/**
 * AUTH-01-03 — the interactive account settings surface.
 *
 * Sections: Profile, Boats, Notifications, Billing, Data & privacy
 * (export + deletion). Loads settings from the API with the Clerk session
 * token; every mutation re-reads the canonical settings response so the UI
 * never drifts from server state.
 */
function ClerkAccountSettings() {
  const { isLoaded, isSignedIn, getToken } = useAuth();
  const { user } = useUser();
  const router = useRouter();

  const [settings, setSettings] = useState<AccountSettingsData | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);

  const [profile, setProfile] = useState({
    full_name: "",
    display_name: "",
    home_club: "",
    country: "",
  });
  const [profileStatus, setProfileStatus] = useState<Status>({ kind: "idle" });
  const [notifStatus, setNotifStatus] = useState<Status>({ kind: "idle" });
  const [exportStatus, setExportStatus] = useState<Status>({ kind: "idle" });

  const [confirmText, setConfirmText] = useState("");
  const [deleteReason, setDeleteReason] = useState("");
  const [deleteStatus, setDeleteStatus] = useState<Status>({ kind: "idle" });

  const load = useCallback(async () => {
    const token = await getToken().catch(() => null);
    if (!token) {
      setLoadError("Could not get a session token. Try signing in again.");
      return;
    }
    try {
      const data = await getAccountSettings(token);
      setSettings(data);
      setProfile({
        full_name: data.full_name ?? "",
        display_name: data.display_name ?? "",
        home_club: data.home_club ?? "",
        country: data.country ?? "",
      });
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "Failed to load settings");
    }
  }, [getToken]);

  useEffect(() => {
    if (isLoaded && isSignedIn) void load();
  }, [isLoaded, isSignedIn, load]);

  const profileDirty = useMemo(() => {
    if (!settings) return false;
    return (
      profile.full_name !== (settings.full_name ?? "") ||
      profile.display_name !== (settings.display_name ?? "") ||
      profile.home_club !== (settings.home_club ?? "") ||
      profile.country !== (settings.country ?? "")
    );
  }, [profile, settings]);

  async function saveProfile() {
    setProfileStatus({ kind: "saving" });
    const token = await getToken();
    try {
      const updated = await updateAccountProfile(token!, {
        full_name: profile.full_name,
        display_name: profile.display_name,
        home_club: profile.home_club,
        country: profile.country,
      });
      setSettings(updated);
      setProfileStatus({ kind: "saved", message: "Profile saved." });
    } catch (e) {
      setProfileStatus({
        kind: "error",
        message: e instanceof Error ? e.message : "Save failed",
      });
    }
  }

  async function toggleNotification(
    key: (typeof NOTIFICATION_OPTIONS)[number]["key"],
    value: boolean,
  ) {
    if (!settings) return;
    // Optimistic toggle; reverted via re-read on error.
    setSettings({ ...settings, [key]: value });
    setNotifStatus({ kind: "saving" });
    const token = await getToken();
    try {
      const updated = await updateNotificationPrefs(token!, { [key]: value });
      setSettings(updated);
      setNotifStatus({ kind: "saved", message: "Preferences updated." });
    } catch (e) {
      setNotifStatus({
        kind: "error",
        message: e instanceof Error ? e.message : "Update failed",
      });
      void load();
    }
  }

  async function runExport() {
    setExportStatus({ kind: "saving", message: "Preparing your export…" });
    const token = await getToken();
    try {
      const doc = await exportAccountData(token!);
      const stamp = new Date().toISOString().slice(0, 10);
      downloadJson(`sailratings-export-${stamp}.json`, doc);
      setExportStatus({ kind: "saved", message: "Export downloaded." });
    } catch (e) {
      setExportStatus({
        kind: "error",
        message: e instanceof Error ? e.message : "Export failed",
      });
    }
  }

  async function runDelete() {
    if (confirmText !== "DELETE") return;
    setDeleteStatus({ kind: "saving", message: "Deleting your data…" });
    const token = await getToken();
    try {
      await deleteAccount(token!, deleteReason || undefined);
    } catch (e) {
      setDeleteStatus({
        kind: "error",
        message: e instanceof Error ? e.message : "Deletion failed",
      });
      return;
    }
    // Our data is gone — now remove the Clerk-side account and leave.
    try {
      await user?.delete();
    } catch {
      // Clerk deletion is best-effort from the client; the API-side
      // personal data is already destroyed and the tombstone blocks any
      // further use of a lingering session (410 Gone).
    }
    setDeleteStatus({ kind: "saved", message: "Your account was deleted." });
    router.push("/");
    router.refresh();
  }

  if (!isLoaded) {
    return <p className="text-sm text-[var(--sr-text-secondary)]">Loading…</p>;
  }

  if (!isSignedIn) {
    return (
      <div className="sr-card text-center" data-testid="account-sign-in-prompt">
        <p className="text-sm text-[var(--sr-text-secondary)] mb-4">
          Sign in to manage your account settings.
        </p>
        <Link href="/sign-in" className="sr-button sr-button--primary">
          Sign in
        </Link>
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="sr-card" role="alert">
        <p className="text-sm text-[var(--sr-status-warning)]">{loadError}</p>
      </div>
    );
  }

  if (!settings) {
    return (
      <p className="text-sm text-[var(--sr-text-secondary)]">
        Loading your settings…
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-10">
      {/* ── Profile ─────────────────────────────────────────────── */}
      <section className="sr-panel" data-testid="settings-profile">
        <h2 className="font-display text-lg font-bold mb-1">Profile</h2>
        <p className="text-xs text-[var(--sr-text-secondary)] mb-6">
          How you appear on Sail Ratings. Your email (
          <span className="font-medium">{settings.email ?? "—"}</span>) is
          managed by your sign-in provider.
        </p>
        <div className="grid gap-5 sm:grid-cols-2">
          <label className="sr-field">
            <span className="sr-field__label">Full name</span>
            <input
              className="sr-input"
              value={profile.full_name}
              onChange={(e) =>
                setProfile({ ...profile, full_name: e.target.value })
              }
              placeholder="Sam Sailor"
              maxLength={200}
            />
          </label>
          <label className="sr-field">
            <span className="sr-field__label">Display name</span>
            <input
              className="sr-input"
              value={profile.display_name}
              onChange={(e) =>
                setProfile({ ...profile, display_name: e.target.value })
              }
              placeholder="Shown publicly"
              maxLength={200}
            />
          </label>
          <label className="sr-field">
            <span className="sr-field__label">Home club</span>
            <input
              className="sr-input"
              value={profile.home_club}
              onChange={(e) =>
                setProfile({ ...profile, home_club: e.target.value })
              }
              placeholder="Royal Solent YC"
              maxLength={200}
            />
          </label>
          <label className="sr-field">
            <span className="sr-field__label">Country</span>
            <input
              className="sr-input"
              value={profile.country}
              onChange={(e) =>
                setProfile({ ...profile, country: e.target.value })
              }
              placeholder="GBR"
              maxLength={100}
            />
          </label>
        </div>
        <div className="mt-6 flex items-center gap-4">
          <button
            className="sr-button sr-button--primary"
            onClick={saveProfile}
            disabled={!profileDirty || profileStatus.kind === "saving"}
            data-testid="profile-save"
          >
            {profileStatus.kind === "saving" ? "Saving…" : "Save profile"}
          </button>
          {profileStatus.kind === "saved" && (
            <span className="text-xs text-[var(--sr-status-success,#2f9e44)]">
              {profileStatus.message}
            </span>
          )}
          {profileStatus.kind === "error" && (
            <span className="text-xs text-[var(--sr-status-warning)]" role="alert">
              {profileStatus.message}
            </span>
          )}
        </div>
      </section>

      {/* ── Boats ───────────────────────────────────────────────── */}
      <section className="sr-panel" data-testid="settings-boats">
        <h2 className="font-display text-lg font-bold mb-1">Your boats</h2>
        <p className="text-xs text-[var(--sr-text-secondary)] mb-6">
          Boats you have claimed appear here once verified. Claim a boat from
          her ratings page to unlock owner-only analysis.
        </p>
        <BoatsSummary />
      </section>

      {/* ── Notifications ───────────────────────────────────────── */}
      <section className="sr-panel" data-testid="settings-notifications">
        <h2 className="font-display text-lg font-bold mb-1">Notifications</h2>
        <p className="text-xs text-[var(--sr-text-secondary)] mb-6">
          Transactional email (receipts, report delivery) is always sent —
          these preferences control everything else. All off by default.
        </p>
        <ul className="flex flex-col divide-y divide-[var(--sr-border-subtle)]">
          {NOTIFICATION_OPTIONS.map((opt) => (
            <li key={opt.key} className="flex items-start justify-between gap-6 py-4">
              <div>
                <p className="text-sm font-medium">{opt.label}</p>
                <p className="text-xs text-[var(--sr-text-secondary)]">
                  {opt.description}
                </p>
              </div>
              <button
                type="button"
                role="switch"
                aria-checked={settings[opt.key]}
                aria-label={opt.label}
                data-testid={`notif-${opt.key}`}
                onClick={() => toggleNotification(opt.key, !settings[opt.key])}
                className={`relative mt-0.5 h-6 w-11 shrink-0 rounded-full transition-colors ${
                  settings[opt.key]
                    ? "bg-[var(--sr-action)]"
                    : "bg-[var(--sr-border-strong)]"
                }`}
              >
                <span
                  className={`absolute top-0.5 h-5 w-5 rounded-full bg-[var(--sr-surface-card)] shadow transition-transform ${
                    settings[opt.key] ? "translate-x-[22px]" : "translate-x-0.5"
                  }`}
                />
              </button>
            </li>
          ))}
        </ul>
        {notifStatus.kind === "saved" && (
          <p className="mt-3 text-xs text-[var(--sr-status-success,#2f9e44)]">
            {notifStatus.message}
          </p>
        )}
        {notifStatus.kind === "error" && (
          <p className="mt-3 text-xs text-[var(--sr-status-warning)]" role="alert">
            {notifStatus.message}
          </p>
        )}
      </section>

      {/* ── Billing ─────────────────────────────────────────────── */}
      <section className="sr-panel" data-testid="settings-billing">
        <h2 className="font-display text-lg font-bold mb-1">Billing</h2>
        <p className="text-xs text-[var(--sr-text-secondary)] mb-6">
          Manage your subscription, payment method and invoices on our
          payments partner, Stripe.
        </p>
        <a
          href="https://billing.stripe.com/p/login"
          target="_blank"
          rel="noopener noreferrer"
          className="sr-button sr-button--secondary"
          data-testid="billing-portal-link"
        >
          Manage billing
        </a>
      </section>

      {/* ── Data & privacy ──────────────────────────────────────── */}
      <section className="sr-panel" data-testid="settings-data">
        <h2 className="font-display text-lg font-bold mb-1">Your data</h2>
        <p className="text-xs text-[var(--sr-text-secondary)] mb-6">
          Download everything we hold on you — profile, settings, boats,
          orders and subscriptions — as a single JSON document.
        </p>
        <div className="flex items-center gap-4">
          <button
            className="sr-button sr-button--secondary"
            onClick={runExport}
            disabled={exportStatus.kind === "saving"}
            data-testid="export-download"
          >
            {exportStatus.kind === "saving" ? "Preparing…" : "Download my data"}
          </button>
          {exportStatus.kind === "saved" && (
            <span className="text-xs text-[var(--sr-status-success,#2f9e44)]">
              {exportStatus.message}
            </span>
          )}
          {exportStatus.kind === "error" && (
            <span className="text-xs text-[var(--sr-status-warning)]" role="alert">
              {exportStatus.message}
            </span>
          )}
        </div>
      </section>

      {/* ── Danger zone ─────────────────────────────────────────── */}
      <section
        className="sr-panel border-[var(--sr-status-warning)]"
        data-testid="settings-danger"
      >
        <h2 className="font-display text-lg font-bold mb-1 text-[var(--sr-status-warning)]">
          Delete account
        </h2>
        <p className="text-xs text-[var(--sr-text-secondary)] mb-4 max-w-xl">
          Permanently deletes your profile, settings, notification
          preferences, boat claims and subscription records, and anonymises
          your identity. Transaction records are kept only in anonymised
          form, as required for financial-record retention. This cannot be
          undone.
        </p>
        <label className="sr-field mb-4 max-w-md">
          <span className="sr-field__label">
            Reason (optional — helps us improve)
          </span>
          <textarea
            className="sr-textarea"
            value={deleteReason}
            onChange={(e) => setDeleteReason(e.target.value)}
            maxLength={2000}
          />
        </label>
        <label className="sr-field mb-5 max-w-md">
          <span className="sr-field__label">
            Type <strong>DELETE</strong> to confirm
          </span>
          <input
            className="sr-input"
            value={confirmText}
            onChange={(e) => setConfirmText(e.target.value)}
            placeholder="DELETE"
            data-testid="delete-confirm-input"
          />
        </label>
        <div className="flex items-center gap-4">
          <button
            className="sr-button bg-[var(--sr-status-warning)] text-white"
            onClick={runDelete}
            disabled={confirmText !== "DELETE" || deleteStatus.kind === "saving"}
            data-testid="delete-account-button"
          >
            {deleteStatus.kind === "saving"
              ? "Deleting…"
              : "Permanently delete account"}
          </button>
          {deleteStatus.kind === "error" && (
            <span className="text-xs text-[var(--sr-status-warning)]" role="alert">
              {deleteStatus.message}
            </span>
          )}
        </div>
      </section>
    </div>
  );
}

/**
 * Boats claimed by the member — read from the export document's ``boats``
 * section so the settings page needs no extra endpoint.
 */
function BoatsSummary() {
  const { getToken } = useAuth();
  const [boats, setBoats] = useState<
    { boat_id: number; boat_name: string | null; sail_number: string | null; status: string }[] | null
  >(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const token = await getToken().catch(() => null);
      if (!token) return;
      try {
        const doc = await exportAccountData(token);
        if (cancelled) return;
        setBoats(
          (doc.boats as typeof boats) ?? [],
        );
      } catch {
        if (!cancelled) setBoats([]);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [getToken]);

  if (boats === null) {
    return (
      <p className="text-sm text-[var(--sr-text-secondary)]">Loading boats…</p>
    );
  }
  if (boats.length === 0) {
    return (
      <p className="text-sm text-[var(--sr-text-secondary)]" data-testid="boats-empty">
        No boats claimed yet. Find your boat on the{" "}
        <Link href="/ratings" className="underline text-[var(--sr-link)]">
          ratings register
        </Link>{" "}
        and claim her from her page.
      </p>
    );
  }
  return (
    <ul className="flex flex-col gap-2" data-testid="boats-list">
      {boats.map((b) => (
        <li
          key={b.boat_id}
          className="flex items-center justify-between rounded-[var(--sr-radius-control)] border border-[var(--sr-border-subtle)] bg-[var(--sr-surface-card)] px-4 py-3"
        >
          <Link
            href={`/ratings/${b.boat_id}`}
            className="text-sm font-medium text-[var(--sr-link)] hover:underline"
          >
            {b.boat_name ?? "Unnamed"} · {b.sail_number ?? "—"}
          </Link>
          <span className="text-[11px] uppercase tracking-wider text-[var(--sr-text-label)]">
            {b.status}
          </span>
        </li>
      ))}
    </ul>
  );
}
