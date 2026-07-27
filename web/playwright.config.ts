import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E configuration for the SailRatings web frontend.
 *
 * `npx playwright test` discovers specs under `./tests` matching the
 * `*.spec.ts` pattern. A single webServer spins up `next dev` so the suite
 * is self-contained and does not depend on a separately-running dev server.
 */
export default defineConfig({
  testDir: "./tests",
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? "line" : "list",
  use: {
    baseURL: "http://localhost:4300",
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // Dedicated port (4300) so the test server never collides with a
    // long-running dev server on 4200. reuseExistingServer lets a developer
    // keep `next dev -p 4300` running between runs; in a fresh CI/gatekeeper
    // worktree Playwright starts its own server.
    command: "npm run dev -- -p 4300",
    url: "http://localhost:4300",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
