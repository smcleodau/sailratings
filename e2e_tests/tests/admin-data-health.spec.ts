import { test, expect, type Page } from '@playwright/test';

/**
 * AD-01-15 — Data health per SPEC-22 §3.4: /health/tables +
 * /health/completeness read from nightly admin_metrics.
 *
 * The e2e rig boots only the Next.js dev server (default
 * playwright.config.ts, same as admin-today.spec.ts) — the admin page's
 * three GET calls (dashboard, tables, completeness) are intercepted and
 * answered with fixture data in-browser, so this spec needs no dedicated
 * fixture API process.
 *
 * Fixtures below mirror the real API shapes:
 *   GET /admin/data-health/dashboard     — DP-05-04 dashboard (unchanged)
 *   GET /admin/data-health/completeness  — AD-01-15 (new): 10 boats.* meters
 *     + 1 events.venue_null_pct, one of the 10 ("builder") deliberately
 *     under the 40% Buoy threshold.
 *   GET /admin/data-health/tables        — AD-01-15 (new): a non-empty
 *     table and an empty one (rows === 0), empty listed first.
 */

const DASHBOARD_FIXTURE = {
  schema_version: 'v1',
  as_of: '2026-09-08T05:30:00Z',
  window_days: 7,
  overview: {
    sources_tracked: 3,
    sources_stale: 0,
    sources_quarantined: 0,
    open_source_incidents: 0,
    open_data_incidents: 0,
    unacknowledged_data_incidents: 0,
    blocking_reconciliations_in_window: 0,
    lineage_gap_runs_in_window: 0,
    gate_quarantine_open: 0,
    identity_awaiting_review: 0,
    identity_quarantined: 0,
    slo_breaches: 0,
  },
  sources: [],
  identity_uncertainty: {
    available: true,
    awaiting_review_batches: 0,
    quarantined_batches: 0,
    consumer_impact: 'none',
  },
  lineage_gaps: { available: true, runs: [] },
  slo_breaches: [],
  active_quarantines: [],
  incidents: [],
  availability: {},
};

const COMPLETENESS_FIXTURE = {
  count: 11,
  metrics: [
    { metric: 'completeness.boats.design', value: 90.0, computed_at: '2026-09-08T05:30:00Z' },
    { metric: 'completeness.boats.design_canonical', value: 88.5, computed_at: '2026-09-08T05:30:00Z' },
    { metric: 'completeness.boats.country', value: 95.2, computed_at: '2026-09-08T05:30:00Z' },
    { metric: 'completeness.boats.year_built', value: 70.1, computed_at: '2026-09-08T05:30:00Z' },
    // Deliberately under the 40% Buoy threshold.
    { metric: 'completeness.boats.builder', value: 27.9, computed_at: '2026-09-08T05:30:00Z' },
    { metric: 'completeness.boats.designer', value: 31.6, computed_at: '2026-09-08T05:30:00Z' },
    { metric: 'completeness.boats.loa', value: 60.0, computed_at: '2026-09-08T05:30:00Z' },
    { metric: 'completeness.boats.lwl', value: 58.0, computed_at: '2026-09-08T05:30:00Z' },
    { metric: 'completeness.boats.beam_max', value: 58.5, computed_at: '2026-09-08T05:30:00Z' },
    { metric: 'completeness.boats.displacement_kg', value: 30.6, computed_at: '2026-09-08T05:30:00Z' },
    { metric: 'completeness.events.venue_null_pct', value: 12.5, computed_at: '2026-09-08T05:30:00Z' },
  ],
};

const TABLES_FIXTURE = {
  count: 2,
  tables: [
    { name: 'events', rows: 0, bytes: 16384, empty: true, computed_at: '2026-09-08T05:30:00Z' },
    { name: 'boats', rows: 9421, bytes: 4194304, empty: false, computed_at: '2026-09-08T05:30:00Z' },
  ],
};

async function stubDataHealth(page: Page) {
  await page.route('**/admin/data-health/dashboard**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(DASHBOARD_FIXTURE),
    });
  });
  await page.route('**/admin/data-health/completeness**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(COMPLETENESS_FIXTURE),
    });
  });
  await page.route('**/admin/data-health/tables**', (route) => {
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(TABLES_FIXTURE),
    });
  });
  await page.addInitScript(() => {
    window.localStorage.setItem('admin_token', 'e2e-test-token');
  });
}

test.describe('Admin Data Health (AD-01-15)', () => {
  test('renders in shell', async ({ page }) => {
    await stubDataHealth(page);
    await page.goto('/admin/data-health', { waitUntil: 'domcontentloaded' });

    await expect(page.getByTestId('admin-shell')).toBeVisible();
    await expect(page.getByTestId('admin-sidebar')).toBeVisible();
    await expect(
      page.getByRole('heading', { name: 'Data health', level: 1 }),
    ).toBeVisible();
    await expect(page.getByTestId('completeness-section')).toBeVisible();
    await expect(page.getByTestId('tables-census-section')).toBeVisible();
  });

  test('completeness meters count 10', async ({ page }) => {
    await stubDataHealth(page);
    await page.goto('/admin/data-health', { waitUntil: 'domcontentloaded' });

    await expect(page.getByTestId('completeness-section')).toBeVisible();
    const meters = page.getByTestId('completeness-meter');
    await expect(meters).toHaveCount(10);

    // The 11th metric (events venue null-rate) renders separately, not as
    // one of the 10 boats.* meters.
    await expect(page.getByTestId('completeness-venue-null')).toContainText(
      '12.5%',
    );
  });

  test('Buoy class on a fixture metric under 40%', async ({ page }) => {
    await stubDataHealth(page);
    await page.goto('/admin/data-health', { waitUntil: 'domcontentloaded' });

    await expect(page.getByTestId('completeness-section')).toBeVisible();
    // completeness.boats.builder = 27.9% is seeded under the 40% threshold
    // and must render with the SailRatings "Buoy" warning colour class.
    const builderMeter = page
      .getByTestId('completeness-meter')
      .filter({ hasText: 'builder' });
    await expect(builderMeter).toBeVisible();
    const buoyNode = builderMeter.locator('[class*="sr-buoy"]').first();
    await expect(buoyNode).toBeVisible();
  });

  test('empty tables listed first', async ({ page }) => {
    await stubDataHealth(page);
    await page.goto('/admin/data-health', { waitUntil: 'domcontentloaded' });

    await expect(page.getByTestId('tables-census-section')).toBeVisible();
    const rows = page.getByTestId('table-census-row');
    await expect(rows).toHaveCount(2);

    // The events table (rows === 0) is flagged empty and sorted first.
    const first = rows.first();
    await expect(first).toHaveAttribute('data-table-empty', 'true');
    await expect(first).toContainText('events');
    await expect(first).toContainText('empty');

    const second = rows.nth(1);
    await expect(second).toHaveAttribute('data-table-empty', 'false');
    await expect(second).toContainText('boats');
  });
});
