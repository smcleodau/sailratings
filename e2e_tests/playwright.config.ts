import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'fs';
import { join } from 'path';

/* PAY-01-10 E2E rig.
 *
 * Two dev servers are started automatically before the tests run:
 *
 *   1. Admin Customers API (http://127.0.0.1:4101) — a self-contained
 *      FastAPI process serving the admin_customers router against a freshly
 *      seeded SQLite fixture (5 users / 6 claims / 47 orders incl. 37
 *      abandoned) with the Stripe SDK patched to return the live catalogue
 *      (pro_monthly_gbp £29, pro_annual_gbp £290/yr, LAUNCH20 promo).
 *      See fixtures/admin_customers_api.py.  No external DB / Stripe / Clerk
 *      needed, so `npm run test` works from a clean checkout.
 *
 *   2. The Next.js frontend (http://localhost:4201) pointed at that API via
 *      NEXT_PUBLIC_API_BASE.
 *
 * IMPORTANT: no Clerk keys are passed to the web server here.  When
 * NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is unset the middleware (web/src/
 * middleware.ts) falls through and lets the admin pages self-gate with the
 * shared admin password (admin_token in localStorage → Bearer on
 * /v1/admin/*).  Injecting the keyless test keys here previously flipped the
 * middleware into Clerk-enforced mode and every /admin test redirected to
 * the "Secure Admin Gateway" sign-in page — which is why this suite failed.
 */

const API_PORT = process.env.PW_API_PORT || '4101';
const WEB_PORT = process.env.PW_WEB_PORT || '4201';
const API_URL = `http://127.0.0.1:${API_PORT}`;
const WEB_URL = process.env.PW_BASE_URL || `http://localhost:${WEB_PORT}`;

/* Resolve the API interpreter: PW_API_PYTHON wins, then the worktree's own
   api/.venv, then the sibling checkout's venv, then PATH python3. */
function resolveApiPython(): string {
  if (process.env.PW_API_PYTHON) return process.env.PW_API_PYTHON;
  const candidates = [
    join(__dirname, '..', 'api', '.venv', 'bin', 'python'),
    join(__dirname, '..', '..', 'api', '.venv', 'bin', 'python'),
  ];
  for (const c of candidates) {
    if (existsSync(c)) return c;
  }
  return 'python3';
}
const API_PY = resolveApiPython();

export default defineConfig({
  testDir: './tests',
  /* Maximum time one test can run for. */
  timeout: 30 * 1000,
  expect: {
    timeout: 5000
  },
  /* Run tests in files in parallel */
  fullyParallel: true,
  /* Fail the build on CI if you accidentally left test.only in the source code. */
  forbidOnly: !!process.env.CI,
  /* Retry on CI only */
  retries: process.env.CI ? 2 : 0,
  /* Opt out of parallel tests on CI. */
  workers: process.env.CI ? 1 : undefined,
  /* Reporter to use. See https://playwright.dev/docs/test-reporters */
  reporter: 'html',
  /* Shared settings for all the projects below. See https://playwright.dev/docs/api/class-testoptions */
  use: {
    /* Base URL to use in actions like `await page.goto('/')`. */
    baseURL: WEB_URL,

    /* Collect trace when retrying the failed test. See https://playwright.dev/docs/trace-viewer */
    trace: 'on-first-retry',
    video: 'on',
    screenshot: 'only-on-failure',
  },

  /* Configure projects for major browsers */
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  /* Start the seeded admin API, then the frontend, before the tests. */
  webServer: [
    {
      command: `${API_PY} fixtures/admin_customers_api.py`,
      url: `${API_URL}/v1/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60 * 1000,
      env: { PW_API_PORT: API_PORT },
    },
    {
      command: `cd ../web && PORT=${WEB_PORT} npm run dev`,
      url: WEB_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 180 * 1000,
      env: {
        NEXT_PUBLIC_API_BASE: `${API_URL}/v1`,
        ENVIRONMENT: 'local',
      },
    },
  ],
});
