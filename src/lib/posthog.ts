"use client";

import posthog from "posthog-js";

let initialized = false;

export function initPostHog(): void {
  if (initialized || typeof window === "undefined") return;

  const key = process.env.NEXT_PUBLIC_POSTHOG_KEY;
  const host =
    process.env.NEXT_PUBLIC_POSTHOG_HOST ?? "https://eu.i.posthog.com";
  // When api_host is a path-based reverse proxy (e.g. /ingest on the same
  // domain), ui_host points the toolbar and replays at the real PostHog UI.
  const uiHost =
    process.env.NEXT_PUBLIC_POSTHOG_UI_HOST ?? "https://eu.posthog.com";

  if (!key) return;

  posthog.init(key, {
    api_host: host,
    ui_host: uiHost,
    capture_pageview: "history_change",
    capture_pageleave: true,
    person_profiles: "identified_only",
    defaults: "2025-05-24",
  });

  initialized = true;
}

export { posthog };

export function track(
  event: string,
  properties?: Record<string, unknown>,
): void {
  if (typeof window === "undefined") return;
  if (!initialized) return;
  posthog.capture(event, properties);
}
