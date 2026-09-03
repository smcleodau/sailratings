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
    baseURL: process.env.PW_BASE_URL || 'http://localhost:4201',

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

  /* Run your local dev server before starting the tests */
  webServer: {
    command: 'cd ../web && PORT=4201 npm run dev',
    url: process.env.PW_BASE_URL || 'http://localhost:4201',
    reuseExistingServer: !process.env.CI,
    timeout: 180 * 1000,
    env: readClerkKeys(),
  },
});
