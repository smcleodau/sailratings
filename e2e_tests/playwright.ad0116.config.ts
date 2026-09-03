import { defineConfig, devices } from '@playwright/test';

/**
 * AD-01-16 config — the admin-shell migration smoke rig.
 *
 * Unlike playwright.config.ts (which boots the seeded admin_customers API),
 * this config expects two already-running servers:
 *
 *   · web :4299 — the worktree `next dev` with Clerk keys unset, pointed at…
 *   · api :4199 — the worktree uvicorn against the dev Postgres (5433)
 *
 * Both are launched by hand before the run so the Sources pause toggle and
 * the data-chat propose/confirm flow hit the real backend.
 */

export default defineConfig({
  testDir: './tests',
  timeout: 60 * 1000,
  expect: { timeout: 10000 },
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.PW_BASE_URL || 'http://localhost:4299',
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
