import { defineConfig, devices } from '@playwright/test';

/* Environment for the dev server Playwright boots (webServer.env).

   Clerk is always disabled for the E2E run: empty keys make the root layout
   skip <ClerkProvider> and the middleware take its unconfigured path, where
   E2E=1 (honoured only outside production) lets admin routes render their
   own internal bearer-token gate (AD-01-01) instead of redirecting to a
   hosted sign-in page. Auth itself is out of scope for these specs.

   This must be unconditional — previously the bypass only engaged when the
   runner remembered to export E2E=1, so a plain `npx playwright test`
   injected the fallback Clerk keys and every admin-shell spec failed on the
   sign-in redirect. Setting E2E=1 here (in the server env, not just the
   Playwright process) makes a bare `npm run test` self-contained. */
function testServerEnv(): Record<string, string> {
  return {
    E2E: '1',
    NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: '',
    CLERK_SECRET_KEY: '',
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

  /* Run your local dev server before starting the tests. We allow reusing an
     already-running dev server when E2E=1 is exported (handy for iterating on
     the AD-01-12 shell spec locally); otherwise Playwright always boots its
     own with the Clerk-disabled env above. */
  webServer: {
    command: 'cd ../web && PORT=4201 npm run dev',
    url: 'http://localhost:4201',
    reuseExistingServer: process.env.E2E === '1',
    timeout: 180 * 1000,
    env: testServerEnv(),
  },
});
