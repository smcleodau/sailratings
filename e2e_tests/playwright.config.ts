import { defineConfig, devices } from '@playwright/test';
import { readFileSync, existsSync } from 'fs';
import { join } from 'path';

/* Read Clerk keyless test keys from the .clerk/.tmp/keyless.json file that
   Clerk generates in keyless mode.  This file is gitignored, so in a fresh
   checkout (CI / gatekeeper) it may be absent — in that case we fall back
   to the known project test-mode keys (pk_test / sk_test).  These are
   test-mode keys, not production secrets. */
function readClerkKeys(): Record<string, string> {
  const keylessPath = join(__dirname, '..', 'web', '.clerk', '.tmp', 'keyless.json');
  let publishableKey = 'pk_test_cXVpY2std29sZi02MS5jbGVyay5hY2NvdW50cy5kZXYk';
  let secretKey = 'sk_test_FCH3fSjcXfiTOtx4IcE52aSNNYYNXPYYn0xDRxBBQa';
  if (existsSync(keylessPath)) {
    try {
      const data = JSON.parse(readFileSync(keylessPath, 'utf-8'));
      if (data.publishableKey) publishableKey = data.publishableKey;
      if (data.secretKey) secretKey = data.secretKey;
    } catch { /* fall back to defaults */ }
  }
  return {
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY:
      process.env.NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY || publishableKey,
    CLERK_SECRET_KEY:
      process.env.CLERK_SECRET_KEY || secretKey,
  };
}

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
    baseURL: 'http://localhost:4201',

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

  /* Run your local dev server before starting the tests.
     Two servers:
       1. the worktree FastAPI on :4100 — the data-health spec exercises the
          real /v1/admin/health/* endpoints (the page fetches them through the
          Next rewrite at /api/v1/*).  reuseExistingServer so a developer's
          already-running API is used instead of a second instance.
       2. the Next dev server on :4201, with NEXT_PUBLIC_API_BASE pointed at
          the local /api/v1 proxy so the page's client-side fetches stay
          same-origin (and loopback) rather than resolving a public host. */
  webServer: [
    {
      command:
        'cd ../api && PYTHONPATH=src python3 -m uvicorn irc_data.api.app:app --host 127.0.0.1 --port 4100',
      // The API has no /v1/health JSON route registered for HEAD probes on
      // every build; the root document is the reliable readiness signal.
      url: 'http://127.0.0.1:4100/',
      reuseExistingServer: true,
      timeout: 60 * 1000,
    },
    {
      command: 'cd ../web && PORT=4201 npm run dev',
      url: 'http://localhost:4201',
      reuseExistingServer: false,
      timeout: 180 * 1000,
      env: {
        ...readClerkKeys(),
        // Same-origin proxy for the page's API fetches (see next.config
        // rewrites → http://localhost:4100).  Overrides any inherited
        // NEXT_PUBLIC_API_BASE pointing at a public host.
        NEXT_PUBLIC_API_BASE: '/api/v1',
        ENVIRONMENT: 'local',
      },
    },
  ],
});
