import { test, expect, type Page } from '@playwright/test';

/**
 * AD-01-06 — Admin: Scrapers health page (design 2a).
 *
 * Verifies every acceptance criterion against the seeded ledger fixture
 * served by fixtures/admin_scrapers_api.py (see playwright.scrapers.config.ts):
 *
 *   - /admin/scrapers renders every source with last run, last new data and
 *     7-day runs/fails/rows;
 *   - run pills (fresh/stale/never/n/a) per signal;
 *   - expandable recent-runs table per row (started/duration/status/found/
 *     new/error);
 *   - auto-refresh every 60 s (observed via the ?refresh_ms= test hook);
 *   - "Cron health" banner driven by the OPS-01-04 watchdog alert stream;
 *   - the admin design (inverse Paper / Dusk palette) — the page sits on the
 *     Dusk ground, not a light background.
 *
 * Fixture numbers (fixtures/admin_scrapers_seed.py):
 *   sailsys   3 runs / 1 fail / 8 new rows in 7d, run+fresh, data fresh
 *   topyacht  last success 5 d ago → run stale, data stale, ACTIVE watchdog
 *             run alert → Cron health banner
 *   orc_api   never ran → run: never, data: n/a
 *   cowesweek optional annual → state optional
 *   ghost     uncatalogued → surfaced with "(uncatalogued)"
 */

const SUPERVISED_SOURCES = [
  'sailsys',
  'orc_api',
  'irc_tcc',
  'topyacht',
  'sailracehq',
  'isora',
  'rhkyc',
  'cowesweek',
  'sydneyhobart',
  'rorc',
] as const;

async function openScrapers(page: Page, query = '') {
  await page.goto(`/admin/scrapers${query}`, { waitUntil: 'domcontentloaded' });
  // Wait for the first summary fetch to land.
  await expect(page.getByTestId('source-row-sailsys')).toBeVisible();
}

test.describe('AD-01-06 scrapers health page', () => {
  test.beforeEach(async ({ page }) => {
    // Seed the admin token so the page skips its password gate (AD-01-01);
    // the fixture API accepts it.
    await page.addInitScript(() => {
      window.localStorage.setItem('admin_token', 'sailfast2026');
    });
  });

  test('renders every source with last run, last new data, 7-day runs/fails/rows', async ({
    page,
  }) => {
    await openScrapers(page);

    // Every supervised source renders a row…
    for (const slug of SUPERVISED_SOURCES) {
      await expect(page.getByTestId(`source-row-${slug}`)).toBeVisible();
    }
    // …plus the uncatalogued ledger source.
    await expect(page.getByTestId('source-row-ghost')).toBeVisible();
    await expect(page.getByTestId('source-row-ghost')).toContainText(
      '(uncatalogued)',
    );

    // sailsys row: last run "30m ago", 7-day 3 / 1 / 8, both pills fresh.
    const sailsys = page.getByTestId('source-row-sailsys');
    await expect(sailsys).toContainText('SailSys (AU clubs)');
    await expect(sailsys).toContainText('sailsys · every 30 min');
    await expect(sailsys).toContainText('30m');
    await expect(sailsys).toContainText('latest race');
    await expect(sailsys.getByTestId('pill-run')).toHaveAttribute(
      'data-state',
      'fresh',
    );
    await expect(sailsys.getByTestId('pill-data')).toHaveAttribute(
      'data-state',
      'fresh',
    );
    // 7-day runs / fails / rows — the acceptance triple, in one cell.
    await expect(sailsys).toContainText('3 / 1 / 8');

    // topyacht row: 5-day-old success → run stale AND data stale.
    const topyacht = page.getByTestId('source-row-topyacht');
    await expect(topyacht.getByTestId('pill-run')).toHaveAttribute(
      'data-state',
      'stale',
    );
    await expect(topyacht.getByTestId('pill-data')).toHaveAttribute(
      'data-state',
      'stale',
    );
    await expect(topyacht).toContainText('5.0d');

    // orc_api: never ran → "never" + run pill never, data n/a.
    const orc = page.getByTestId('source-row-orc_api');
    await expect(orc).toContainText('never');
    await expect(orc.getByTestId('pill-run')).toHaveAttribute(
      'data-state',
      'never',
    );
    await expect(orc.getByTestId('pill-data')).toHaveAttribute(
      'data-state',
      'n/a',
    );

    // The 7-day column header names all three counters.
    await expect(page.getByTestId('scrapers-table')).toContainText(
      '7-day runs / fails / rows',
    );
  });

  test('expandable recent-runs table per row', async ({ page }) => {
    await openScrapers(page);

    // Drawer hidden until the row is clicked.
    await expect(
      page.getByTestId('recent-runs-sailsys'),
    ).not.toBeVisible();

    await page.getByTestId('source-row-header-sailsys').click();

    const drawer = page.getByTestId('recent-runs-sailsys');
    await expect(drawer).toBeVisible();
    await expect(drawer).toContainText('Recent runs');
    for (const col of ['STARTED', 'DURATION', 'STATUS', 'FOUND', 'NEW', 'ERROR']) {
      await expect(drawer).toContainText(col);
    }
    // Three fixture runs; the failed one shows its error and the completed
    // ones their durations / counts.
    await expect(drawer).toContainText('42.5s');
    await expect(drawer).toContainText('completed');
    await expect(drawer).toContainText('failed');
    await expect(drawer).toContainText('HTTP 503 from club site');

    // Clicking again collapses the drawer.
    await page.getByTestId('source-row-header-sailsys').click();
    await expect(
      page.getByTestId('recent-runs-sailsys'),
    ).not.toBeVisible();
  });

  test('auto-refreshes every 60 s', async ({ page }) => {
    // ?refresh_ms=600 keeps the production default of 60 000 ms but lets the
    // test observe three poll cycles in ~1.3 s instead of two minutes.
    let hits = 0;
    page.on('request', (req) => {
      if (req.url().includes('/admin/scrapers') && !req.url().includes('/runs')) {
        hits += 1;
      }
    });
    await openScrapers(page, '?refresh_ms=600');
    const initial = hits;
    await page.waitForTimeout(1300);
    expect(hits).toBeGreaterThanOrEqual(initial + 2);
  });

  test('"Cron health" banner comes from the watchdog alert stream', async ({
    page,
  }) => {
    await openScrapers(page);

    // The fixture's active watchdog alert is a run-signal alert for
    // topyacht — the banner must name it.
    const banner = page.getByTestId('cron-health-banner');
    await expect(banner).toBeVisible();
    await expect(banner).toContainText('Cron health: 1 source is not running');
    await expect(banner).toContainText('TopYacht (AU/regattas)');
    await expect(banner).toContainText('Watchdog runs every 15 min');

    // The recovered sailsys:data alert is history, not an active banner —
    // no data-tap banner renders.
    await expect(page.getByTestId('data-tap-banner')).not.toBeVisible();

    // The watchdog alert log below the table lists both alerts.
    const log = page.getByTestId('watchdog-alert-log');
    await expect(log).toBeVisible();
    await expect(log).toContainText('TopYacht (AU/regattas)');
    await expect(log).toContainText('active');
    await expect(log).toContainText('SailSys (AU clubs) (no new data)');
    await expect(log).toContainText('recovered');
  });

  test('matches the Admin design (inverse Paper / Dusk palette)', async ({
    page,
  }) => {
    await openScrapers(page);

    // Card surface for the table container: --sr-dusk-card = #14111f →
    // rgb(20, 17, 31). A Paper (light) surface here would mean the page
    // escaped the admin theme.
    const card = await page
      .getByTestId('scrapers-table')
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(card).toBe('rgb(20, 17, 31)');

    // The admin shell root sits on the Dusk ground — --sr-dusk-ground =
    // #0d0b16 → rgb(13, 11, 22) (same anchor the AD-01-12 shell spec uses).
    const ground = await page
      .getByTestId('admin-shell')
      .evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(ground).toBe('rgb(13, 11, 22)');

    // Manual refresh control present with the "as of" stamp.
    await expect(page.getByTestId('scrapers-refresh')).toBeVisible();
    await expect(page.getByTestId('scrapers-as-of')).toBeVisible();
  });

  test('manual refresh re-fetches the summary', async ({ page }) => {
    await openScrapers(page);
    let hits = 0;
    page.on('request', (req) => {
      if (req.url().includes('/admin/scrapers') && !req.url().includes('/runs')) {
        hits += 1;
      }
    });
    await page.getByTestId('scrapers-refresh').click();
    await expect
      .poll(() => hits, { timeout: 3000 })
      .toBeGreaterThanOrEqual(1);
  });
});
