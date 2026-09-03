import { defineConfig, devices } from '@playwright/test';
import { existsSync } from 'fs';
import { join } from 'path';

/**
 * AD-01-14 — Duplicate-boats queue E2E run.
 *
 * Boots a self-contained stack for the dupes decision flow:
 *
 *   1. Dupes admin API (http://127.0.0.1:4102) — the admin_dupes router
 *      against a freshly seeded SQLite fixture (FIFTH AVENUE|AUS 551+146
 *      race results, FOX BAT|GBR ×3, GREY GULL|NZL ×2).  See
 *      fixtures/admin_dupes_api.py.  No external DB / Clerk needed.
 *
 *   2. The Next.js frontend (http://localhost:4203) pointed at that API
 *      via NEXT_PUBLIC_API_BASE, with Clerk disabled so the admin pages
 *      self-gate on the shared admin password.
 *
 * Usage:
 *   npx playwright test --config=playwright.dupes.config.ts
 */

const API_PORT = process.env.PW_API_PORT || '4102';
const WEB_PORT = process.env.PW_WEB_PORT || '4203';
const API_URL = `http://127.0.0.1:${API_PORT}`;
const WEB_URL = `http://localhost:${WEB_PORT}`;

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
  testMatch: 'admin-dupes.spec.ts',
  timeout: 60 * 1000,
  expect: {
    timeout: 8000,
  },
  fullyParallel: false, // one stack, one queue — decisions mutate it
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
  webServer: [
    {
      command: `${API_PY} fixtures/admin_dupes_api.py`,
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
        // Clerk OFF — admin pages self-gate on the shared admin password.
        NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY: '',
        CLERK_SECRET_KEY: '',
      },
    },
  ],
});
