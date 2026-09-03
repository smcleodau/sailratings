/**
 * AD-01-13 — visual evidence capture for the /admin Today screen.
 *
 * Renders the Today screen against the 2 Sep 2026 snapshot fixture and
 * saves a full-page screenshot to e2e_tests/test-results/ so it can be
 * attached to the issue board as visual evidence.
 */
import { test, expect } from '@playwright/test';
import { mkdirSync } from 'fs';
import { join } from 'path';

test.use({ viewport: { width: 1440, height: 1000 } });

test('capture /admin Today screen (2 Sep 2026 snapshot)', async ({ page }) => {
  const { OVERVIEW_FIXTURE } = await import('./fixtures/overview.fixture');

  await page.route('**/admin/overview**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(OVERVIEW_FIXTURE),
    });
  });
  await page.addInitScript(() => {
    window.localStorage.setItem('admin_token', 'e2e-test-token');
  });

  await page.goto('/admin');
  await expect(page.getByTestId('stat-tiles')).toBeVisible();
  await expect(page.getByTestId('attention-list')).toBeVisible();
  await expect(page.getByTestId('sources-table')).toBeVisible();
  // let fonts settle
  await page.waitForTimeout(800);

  const outDir = join(__dirname, '..', 'test-results');
  mkdirSync(outDir, { recursive: true });
  await page.screenshot({
    path: join(outDir, 'admin-today-2026-09-02.png'),
    fullPage: true,
  });
});
