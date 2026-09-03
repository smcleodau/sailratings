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
 *
 * IMPORTANT: this rig runs ONLY the AD-01-16 spec. The other files in
 * ./tests (admin-customers, admin-shell, …) belong to playwright.config.ts /
 * playwright.auth.config.ts and are bound to their own seeded fixtures
 * (e.g. the :4101 admin_customers API with alice.waters@example.com and 47
 * orders). Pointed at this rig's real dev API they can never pass, so the
 * testDir is narrowed to the migration spec itself — otherwise a plain
 * `npx playwright test --config playwright.ad0116.config.ts` fails 2/12.
 */

export default defineConfig({
  testDir: './tests',
  testMatch: 'admin-migration.spec.ts',
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
