/**
 * AD-01-13 — Playwright smoke of the /admin Today screen.
 *
 * The e2e rig boots only the Next.js dev server, so the
 * GET /v1/admin/overview call is intercepted and answered with the
 * 2 Sep 2026 snapshot fixture (the same acceptance numbers the API
 * contract test asserts: orc_api stale_days=38, dupes.pending_clusters=
 * 174, today.new=0, one attention item per stale nightly source).
 */
import { test, expect, type Page } from '@playwright/test';
import { OVERVIEW_FIXTURE } from './fixtures/overview.fixture';

async function stubOverview(page: Page) {
  // The Today screen calls NEXT_PUBLIC_API_BASE (default /api/v1, proxied
  // by next.config rewrites). Intercept both shapes defensively.
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
}

test.describe('/admin Today screen (AD-01-13)', () => {
  test('renders the four stat tiles, attention list, sources table and sparkline', async ({
    page,
  }) => {
    await stubOverview(page);
    await page.goto('/admin');

    // Header
    await expect(
      page.getByRole('heading', { name: 'Today', level: 1 })
    ).toBeVisible();

    // Four stat tiles
    const tiles = page.getByTestId('stat-tiles');
    await expect(tiles).toBeVisible();
    await expect(page.getByTestId('tile-attention')).toContainText('4');
    await expect(page.getByTestId('tile-new-today')).toContainText('0');
    await expect(page.getByTestId('tile-dupes')).toContainText('174');
    await expect(page.getByTestId('tile-corrections')).toContainText('7');

    // Attention list — one item per stale nightly source
    const attention = page.getByTestId('attention-list');
    await expect(attention).toBeVisible();
    await expect(attention).toContainText('ORC API is stale');
    await expect(attention).toContainText('TopYacht is stale');
    await expect(attention).toContainText('174 dupe clusters awaiting review');

    // Sources table with stale pills
    const sources = page.getByTestId('sources-table');
    await expect(sources).toBeVisible();
    await expect(page.getByTestId('source-row-orc_api')).toContainText('38d');
    await expect(page.getByTestId('stale-pill-orc_api')).toContainText(
      'stale 38d'
    );
    await expect(page.getByTestId('stale-pill-topyacht')).toContainText(
      'stale 2d'
    );

    // Runs-per-day sparkline with zero-run bands
    await expect(page.getByTestId('runs-per-day-sparkline')).toBeVisible();

    // Boats count + completeness meters
    await expect(page.getByTestId('boats-tile')).toContainText('9,421');
    const meters = page.getByTestId('completeness-meters');
    await expect(meters).toBeVisible();
    await expect(meters).toContainText('90.0%');
    await expect(meters).toContainText('25.0%');
  });

  test('401 from the API drops the token and shows the login gate', async ({
    page,
  }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('admin_token', 'expired-token');
    });
    await page.route('**/admin/overview**', (route) => {
      route.fulfill({
        status: 401,
        contentType: 'application/json',
        body: JSON.stringify({ detail: 'Unauthorized' }),
      });
    });
    await page.goto('/admin');
    await expect(page.getByPlaceholder('Admin password')).toBeVisible();
  });
});
