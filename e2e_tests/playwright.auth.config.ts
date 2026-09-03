import { defineConfig, devices } from '@playwright/test';

/**
 * AUTH-01-02 — Clerk-configured E2E run.
 *
 * The default playwright.config.ts deliberately boots the dev server with
 * Clerk disabled (empty keys) so the shell/content specs are self-contained.
 * This config does the opposite: it passes the real Clerk keys through from
 * the runner's environment and sets E2E_CLERK=1, which un-skips the
 * Clerk-dependent block in tests/auth.spec.ts (Google button, forgot
 * password, /admin → /sign-in bounce, error states).
 *
 * Usage:
 *   export NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_test_...
 *   export CLERK_SECRET_KEY=sk_test_...
 *   npx playwright test --config=playwright.auth.config.ts
 *
 * Note: E2E is intentionally NOT set here — with Clerk configured, the
 * /admin route must enforce the sign-in redirect even in a test run.
 */
export default defineConfig({
  testDir: './tests',
  testMatch: 'auth.spec.ts',
  timeout: 60 * 1000,
  expect: {
    timeout: 5000,
  },
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  use: {
    baseURL: 'http://localhost:4202',
    trace: 'on-first-retry',
    video: 'on',
    screenshot: 'only-on-failure',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: 'cd ../web && PORT=4202 npm run dev',
    url: 'http://localhost:4202',
    reuseExistingServer: false,
    timeout: 180 * 1000,
    env: {
      E2E_CLERK: '1',
      NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY ?? '',
      CLERK_SECRET_KEY: process.env.CLERK_SECRET_KEY ?? '',
    },
  },
});
