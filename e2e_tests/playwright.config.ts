import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'fs';
import { join } from 'path';

/* Combined admin E2E rig (PAY-01-10 Customers + AD-01-06 Scrapers).
 *
 * Two dev servers are started automatically before the tests run:
 *
 *   1. Combined Admin API (http://127.0.0.1:4101) — a self-contained FastAPI
 *      process serving BOTH the admin_customers router (5 users / 6 claims /
 *      47 orders incl. 37 abandoned, fake live Stripe catalogue) AND the
 *      admin router's scrapers endpoints (the AD-01-06 ledger fixture), each
 *      backed by its own seeded SQLite database. See
 *      fixtures/admin_combined_api.py.  No external DB / Stripe / Clerk
 *      needed, so `npm run test` works from a clean checkout.
 *
 *      Why combined? The default config's testDir includes
 *      admin-scrapers.spec.ts, and the frontend is pointed at a single
 *      NEXT_PUBLIC_API_BASE. Serving only the customers API here left the
 *      scrapers page's /v1/admin/scrapers calls 404ing (the exact
 *      Gatekeeper failure), because the scrapers-only fixture lived on a
 *      different port under a different config.
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
  /* admin-dupes.spec.ts runs under playwright.dupes.config.ts — it needs the
     AD-01-14 dupes fixture API on :4102, not the PAY-01-10 customers API
     this config boots on :4101. */
  testIgnore: 'admin-dupes.spec.ts',
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

  /* Start the seeded combined admin API, then the frontend, before the
   * tests.
   *
   * The readiness probe hits the real scrapers endpoint (not /v1/health).
   * Rationale: this box also runs a live dev API and the scrapers fixture on
   * adjacent ports, and both expose /v1/health — a stray server squatting on
   * this port would otherwise satisfy a /v1/health probe and get "reused"
   * while serving the wrong data (every /admin/scrapers assertion then reads
   * the live ledger, not the fixture). Probing the scrapers route itself
   * guarantees the combined fixture is the process actually serving. */
  webServer: [
    {
      command: `${API_PY} fixtures/admin_combined_api.py`,
      url: `${API_URL}/v1/admin/scrapers/ping`,
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
        // Force Clerk OFF. The header comment above assumes "pass no keys =
        // Clerk unconfigured", but `npm run dev` auto-loads web/.env.local,
        // which carries real pk_test_/sk_test_ keys — that flipped the
        // middleware into enforced mode and bounced every /admin spec to
        // /sign-in. Setting these explicitly wins over .env.local and
        // restores the intended keyless run. playwright.auth.config.ts
        // passes the real keys through to test the configured path.
        NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: '',
        CLERK_SECRET_KEY: '',
      },
    },
  ],
});
