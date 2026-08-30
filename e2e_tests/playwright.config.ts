import { defineConfig, devices } from '@playwright/test';

// The web dev server runs on port 4200 on this machine (port 3000 is
// occupied by another application).  Allow override via TEST_WEB_PORT for
// CI or alternative environments.
const WEB_PORT = process.env.TEST_WEB_PORT || '4200';
const WEB_URL = `http://localhost:${WEB_PORT}`;

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
  reporter: [['html'], ['list']],
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

  /* Run your local dev server before starting the tests.
   *
   * reuseExistingServer is true — the dev server is expected to be running
   * on this machine at port 4200.  If it isn't running, Playwright starts it.
   */
  webServer: {
    command: `cd ../web && npm run dev -- -p ${WEB_PORT}`,
    url: WEB_URL,
    reuseExistingServer: true,
    timeout: 120 * 1000,
  },
});
