import { test, expect, type Page } from '@playwright/test';
import {
  BUDGET_LOW_FIXTURE,
  BUDGET_OK_FIXTURE,
  BY_DOMAIN_FIXTURE,
  DIFFS_FIXTURE,
  RECENT_FIXTURE,
  SUMMARY_FIXTURE,
} from '../fixtures/admin-firecrawl.fixture';

/**
 * AD-01-22 — /admin/firecrawl: credit budget + content-diffs panels, and the
 * page's first e2e spec.
 *
 * Like admin-data-health.spec.ts, the page's five GETs are intercepted and
 * answered in-browser with fixture data, so only the default rig's Next.js
 * dev server is exercised (no dedicated API fixture process).
 */

interface StubOptions {
  budget?: unknown;
  recent?: unknown;
}

async function stubFirecrawl(page: Page, opts: StubOptions = {}) {
  const json = (body: unknown) => ({
    status: 200,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
  await page.route('**/admin/firecrawl/summary**', (r) => r.fulfill(json(SUMMARY_FIXTURE)));
  await page.route('**/admin/firecrawl/recent**', (r) => r.fulfill(json(opts.recent ?? RECENT_FIXTURE)));
  await page.route('**/admin/firecrawl/by-domain**', (r) => r.fulfill(json(BY_DOMAIN_FIXTURE)));
  await page.route('**/admin/firecrawl/budget**', (r) => r.fulfill(json(opts.budget ?? BUDGET_OK_FIXTURE)));
  await page.route('**/admin/firecrawl/diffs**', (r) => r.fulfill(json(DIFFS_FIXTURE)));
  await page.route('**/admin/overview**', (r) => r.fulfill({ status: 404, body: '{}' }));
  await page.addInitScript(() => {
    window.localStorage.setItem('admin_token', 'e2e-test-token');
  });
}

test.describe('Admin Firecrawl (AD-01-22)', () => {
  test('renders in shell', async ({ page }) => {
    await stubFirecrawl(page);
    await page.goto('/admin/firecrawl', { waitUntil: 'domcontentloaded' });

    await expect(page.getByTestId('admin-shell')).toBeVisible();
    await expect(page.getByTestId('admin-sidebar')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Firecrawl', level: 1 })).toBeVisible();
    await expect(page.getByTestId('credit-panel')).toBeVisible();
    await expect(page.getByTestId('diffs-panel')).toBeVisible();
  });

  test('credit panel shows balance and cap from fixture', async ({ page }) => {
    await stubFirecrawl(page);
    await page.goto('/admin/firecrawl', { waitUntil: 'domcontentloaded' });

    await expect(page.getByTestId('credit-panel')).toBeVisible();
    await expect(page.getByTestId('credit-balance')).toHaveText('2,400');
    await expect(page.getByTestId('credit-daily-cap')).toHaveText('200');
    await expect(page.getByTestId('credit-spent-today')).toHaveText('12');
    await expect(page.getByTestId('credit-projected-burn')).toHaveText('300');
  });

  test('headroom colour class flips below 7 days', async ({ page }) => {
    // 2,400 cr at 10 cr/day = 240 d → healthy.
    await stubFirecrawl(page);
    await page.goto('/admin/firecrawl', { waitUntil: 'domcontentloaded' });
    const headroom = page.getByTestId('credit-headroom');
    await expect(headroom).toBeVisible();
    await expect(headroom).toHaveClass(/headroom-ok/);
    await expect(headroom).not.toHaveClass(/headroom-low/);
    await expect(page.getByTestId('credit-panel')).toHaveAttribute('data-headroom-tone', 'ok');

    // 100 cr at 20 cr/day = 5 d → Buoy.
    const page2 = await page.context().newPage();
    await stubFirecrawl(page2, { budget: BUDGET_LOW_FIXTURE });
    await page2.goto('/admin/firecrawl', { waitUntil: 'domcontentloaded' });
    const low = page2.getByTestId('credit-headroom');
    await expect(low).toBeVisible();
    await expect(low).toHaveClass(/headroom-low/);
    await expect(low).toHaveClass(/sr-buoy/);
    await expect(low).not.toHaveClass(/headroom-ok/);
    await expect(low).toHaveText('5.0 d');
    await expect(page2.getByTestId('credit-panel')).toHaveAttribute('data-headroom-tone', 'low');
  });

  test('diffs panel lists rows', async ({ page }) => {
    await stubFirecrawl(page);
    await page.goto('/admin/firecrawl', { waitUntil: 'domcontentloaded' });

    const panel = page.getByTestId('diffs-panel');
    await expect(panel).toBeVisible();
    await expect(panel.locator('[data-testid^="diff-row-"]')).toHaveCount(3);
    await expect(page.getByTestId('diff-row-101')).toContainText('cowesweek.co.uk');
    await expect(page.getByTestId('diff-row-101')).toContainText('Cowes Week 2026');
    await expect(page.getByTestId('diff-rate-101')).toHaveText('98%');
    await expect(page.getByTestId('diff-rate-102')).toHaveText('75%');
  });

  test('domain filter narrows recent calls', async ({ page }) => {
    await stubFirecrawl(page);
    await page.goto('/admin/firecrawl', { waitUntil: 'domcontentloaded' });

    const rows = page.getByTestId('recent-row');
    await expect(rows).toHaveCount(4);

    await page.getByTestId('domain-filter').selectOption('sailsys.com.au');
    await expect(rows).toHaveCount(2);
    for (const row of await rows.all()) {
      await expect(row).toHaveAttribute('data-domain', 'sailsys.com.au');
    }
  });

  test('empty state when /recent returns []', async ({ page }) => {
    await stubFirecrawl(page, { recent: { calls: [] } });
    await page.goto('/admin/firecrawl', { waitUntil: 'domcontentloaded' });

    await expect(page.getByTestId('recent-empty')).toBeVisible();
    await expect(page.getByTestId('recent-empty')).toContainText('No calls match');
    await expect(page.getByTestId('recent-row')).toHaveCount(0);
  });
});
