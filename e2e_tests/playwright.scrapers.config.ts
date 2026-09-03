import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'fs';
import { join } from 'path';

/* AD-01-06 — Scrapers health page E2E rig.
 *
 * Two servers are started automatically before the tests run:
 *
 *   1. Admin Scrapers API (http://127.0.0.1:4102) — a self-contained
 *      FastAPI process serving the real admin router's scrapers endpoints
 *      against a freshly-seeded SQLite ledger fixture (sailsys healthy,
 *      topyacht cron-breached with an active watchdog alert, orc_api never
 *      run, ghost uncatalogued). See fixtures/admin_scrapers_api.py and
 *      fixtures/admin_scrapers_seed.py. No external DB / Temporal / Clerk
 *      needed, so `npx playwright test --config=playwright.scrapers.config.ts`
 *      works from a clean checkout.
 *
 *   2. The Next.js frontend (http://localhost:4203) pointed at that API via
 *      NEXT_PUBLIC_API_BASE.
 *
 * No Clerk keys are passed to the web server: when
 * NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY is unset the middleware falls through
 * and lets the admin pages self-gate with the shared admin password
 * (admin_token in localStorage → Bearer on /v1/admin/*).
 */

const API_PORT = process.env.PW_SCRAPERS_API_PORT || '4102';
const WEB_PORT = process.env.PW_SCRAPERS_WEB_PORT || '4203';
const API_URL = `http://127.0.0.1:${API_PORT}`;
const WEB_URL = process.env.PW_SCRAPERS_BASE_URL || `http://localhost:${WEB_PORT}`;

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
  testMatch: 'admin-scrapers.spec.ts',
  timeout: 30 * 1000,
  expect: {
    timeout: 5000,
  },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: 'html',
  use: {
    baseURL: WEB_URL,
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

  /* Start the seeded scrapers API, then the frontend, before the tests. */
  webServer: [
    {
      command: `${API_PY} fixtures/admin_scrapers_api.py`,
      url: `${API_URL}/v1/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60 * 1000,
      env: { PW_SCRAPERS_API_PORT: API_PORT },
    },
    {
      command: `cd ../web && PORT=${WEB_PORT} npm run dev`,
      url: WEB_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 180 * 1000,
      env: {
        NEXT_PUBLIC_API_BASE: `${API_URL}/v1`,
        ENVIRONMENT: 'local',
        // Force Clerk OFF — see playwright.config.ts header.
        NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: '',
        CLERK_SECRET_KEY: '',
      },
    },
  ],
});
