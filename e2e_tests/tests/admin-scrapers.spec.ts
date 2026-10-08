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

// Default to the combined admin fixture on :4101 (started by the default
// playwright.config.ts). The scrapers-dedicated config
// (playwright.scrapers.config.ts) exports PW_SCRAPERS_API_BASE pointing at
// its scrapers-only fixture on :4102.
const API_BASE = process.env.PW_SCRAPERS_API_BASE || 'http://127.0.0.1:4101/v1';

async function openScrapers(page: Page, query = '') {
  // Wait for the summary fetch to actually resolve OK *while* the page
  // loads — not just for the row to render. Waiting on the response removes
  // the first-fetch race (a cold Next.js dev compile can hold or fail the
  // initial request) and guarantees the assertions below run against real
  // fixture data rather than a half-rendered error state.
  //
  // AD-01-20: the page also fires a GET .../admin/scrapers/schedule-state
  // on the same cycle (control-plane router, OPS-02-04). This fixture API
  // only mounts the AD-01-06 summary/runs router, so that call legitimately
  // 404s here unless a test stubs it — excluded below so it can never be
  // the response this helper resolves on.
  const summary = page.waitForResponse(
    (res) =>
      res.url().includes('/admin/scrapers') &&
      !res.url().includes('/runs') &&
      !res.url().includes('/schedule-state') &&
      res.request().method() === 'GET',
    { timeout: 20000 },
  );
  await page.goto(`/admin/scrapers${query}`, { waitUntil: 'domcontentloaded' });
  const res = await summary;
  if (!res.ok()) {
    throw new Error(
      `GET /admin/scrapers -> ${res.status()} ${res.statusText()}. ` +
        `Body: ${(await res.text()).slice(0, 300)}`,
    );
  }
  await expect(page.getByTestId('source-row-sailsys')).toBeVisible();
}

/**
 * AD-01-20 — stub the control-plane router (schedule-state / pause /
 * resume / run) that this fixture API doesn't mount (it only serves the
 * AD-01-06 summary + runs endpoints; the OPS-02-04 control router needs a
 * `data_sources` / `source_schedule_state` schema this fixture doesn't
 * seed). Intercepting at the network layer keeps the AD-01-06 fixture
 * files untouched while still exercising the real page code against the
 * real request/response shapes the API documents.
 */
async function stubScheduleState(
  page: Page,
  schedule: Record<string, { cadence: string; schedule_id: string | null; schedule_paused: boolean | null }>,
) {
  await page.route('**/admin/scrapers/schedule-state', async (route) => {
    const scrapers = Object.entries(schedule).map(([slug, s]) => ({
      slug,
      cadence: s.cadence,
      schedule_id: s.schedule_id,
      schedule_paused: s.schedule_paused,
    }));
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ count: scrapers.length, scrapers }),
    });
  });
}

const SCHEDULE_FIXTURE: Record<
  string,
  { cadence: string; schedule_id: string | null; schedule_paused: boolean | null }
> = {
  sailsys: { cadence: 'every 30 min', schedule_id: 'sched-sailsys', schedule_paused: false },
  topyacht: { cadence: 'every 6 h', schedule_id: 'sched-topyacht', schedule_paused: true },
  orc_api: { cadence: 'every 24 h', schedule_id: 'sched-orc_api', schedule_paused: false },
};

test.describe('AD-01-06 scrapers health page', () => {
  test.beforeEach(async ({ page }) => {
    // Fail fast with a clear message if the seeded *scrapers* fixture API
    // didn't come up (or the wrong fixture is squatting on the port). The
    // scrapers-specific ping proves the scrapers router is mounted — a stray
    // customers/health server would pass a /v1/health probe yet 404 every
    // /admin/scrapers call.
    const ping = await page.request.get(`${API_BASE}/admin/scrapers/ping`);
    expect(
      ping.ok(),
      `scrapers fixture API not reachable at ${API_BASE} (status ${ping.status()}). ` +
        `Is fixtures/admin_scrapers_api.py running on this port?`,
    ).toBeTruthy();

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
    // Poll instead of a fixed wall-clock sleep: proves the interval fires
    // repeatedly without being sensitive to CI timing jitter.
    await expect
      .poll(() => hits, { timeout: 8000 })
      .toBeGreaterThanOrEqual(initial + 2);
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

  /* ── AD-01-20 — pause / resume / run-now ──────────────────────────────
   * The control-plane router (schedule-state / pause / resume / run,
   * OPS-02-04) isn't mounted by this fixture API (see stubScheduleState
   * above for why) — these four tests stub it at the network layer so the
   * real page code is exercised against the real request/response shapes
   * without touching the AD-01-06 fixture files. */

  test('schedule column shows cadence and paused pill from fixture', async ({
    page,
  }) => {
    await stubScheduleState(page, SCHEDULE_FIXTURE);
    await openScrapers(page);

    const sailsys = page.getByTestId('source-row-sailsys');
    await expect(sailsys).toContainText('every 30 min');
    await expect(sailsys.getByTestId('schedule-pill-sailsys')).toHaveAttribute(
      'data-state',
      'active',
    );
    await expect(
      sailsys.getByTestId('schedule-id-sailsys'),
    ).toContainText('sched-sailsys');

    const topyacht = page.getByTestId('source-row-topyacht');
    await expect(topyacht).toContainText('every 6 h');
    await expect(
      topyacht.getByTestId('schedule-pill-topyacht'),
    ).toHaveAttribute('data-state', 'paused');
  });

  test('clicking Pause POSTs /pause and flips the pill; Resume flips back', async ({
    page,
  }) => {
    await stubScheduleState(page, SCHEDULE_FIXTURE);

    const pauseRequests: string[] = [];
    await page.route('**/admin/scrapers/sailsys/pause', async (route) => {
      pauseRequests.push(route.request().method());
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ paused: true, schedule_id: 'sched-sailsys' }),
      });
    });
    const resumeRequests: string[] = [];
    await page.route('**/admin/scrapers/sailsys/resume', async (route) => {
      resumeRequests.push(route.request().method());
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ paused: false, schedule_id: 'sched-sailsys' }),
      });
    });

    await openScrapers(page);
    const sailsys = page.getByTestId('source-row-sailsys');
    await expect(sailsys.getByTestId('schedule-pill-sailsys')).toHaveAttribute(
      'data-state',
      'active',
    );

    await sailsys.getByTestId('btn-pause-sailsys').click();
    await expect(sailsys.getByTestId('schedule-pill-sailsys')).toHaveAttribute(
      'data-state',
      'paused',
    );
    await expect.poll(() => pauseRequests.length).toBeGreaterThanOrEqual(1);
    expect(pauseRequests[0]).toBe('POST');

    // The row now shows Resume, not Pause.
    await expect(sailsys.getByTestId('btn-resume-sailsys')).toBeVisible();

    await sailsys.getByTestId('btn-resume-sailsys').click();
    await expect(sailsys.getByTestId('schedule-pill-sailsys')).toHaveAttribute(
      'data-state',
      'active',
    );
    await expect.poll(() => resumeRequests.length).toBeGreaterThanOrEqual(1);
    expect(resumeRequests[0]).toBe('POST');
    await expect(sailsys.getByTestId('btn-pause-sailsys')).toBeVisible();
  });

  test('Run now POSTs /run and shows the queued toast', async ({ page }) => {
    await stubScheduleState(page, SCHEDULE_FIXTURE);

    const runRequests: string[] = [];
    await page.route('**/admin/scrapers/sailsys/run', async (route) => {
      runRequests.push(route.request().method());
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'queued', run_id: 42 }),
      });
    });

    await openScrapers(page);
    const sailsys = page.getByTestId('source-row-sailsys');
    await sailsys.getByTestId('btn-run-sailsys').click();

    await expect.poll(() => runRequests.length).toBeGreaterThanOrEqual(1);
    expect(runRequests[0]).toBe('POST');

    const toast = page.getByTestId('scrapers-toast');
    await expect(toast).toBeVisible();
    await expect(toast).toContainText('queued');
  });

  test('Paused filter chip toggles rows based on schedule_paused', async ({
    page,
  }) => {
    await stubScheduleState(page, SCHEDULE_FIXTURE);
    await openScrapers(page);

    // All sources visible by default.
    await expect(page.getByTestId('source-row-sailsys')).toBeVisible();
    await expect(page.getByTestId('source-row-topyacht')).toBeVisible();
    await expect(page.getByTestId('source-row-orc_api')).toBeVisible();

    const chip = page.getByTestId('filter-paused');
    await expect(chip).toHaveAttribute('aria-pressed', 'false');
    await chip.click();
    await expect(chip).toHaveAttribute('aria-pressed', 'true');

    // Only the paused source (topyacht) remains.
    await expect(page.getByTestId('source-row-topyacht')).toBeVisible();
    await expect(page.getByTestId('source-row-sailsys')).not.toBeVisible();
    await expect(page.getByTestId('source-row-orc_api')).not.toBeVisible();

    // Toggling off restores every row.
    await chip.click();
    await expect(page.getByTestId('source-row-sailsys')).toBeVisible();
    await expect(page.getByTestId('source-row-orc_api')).toBeVisible();
  });
});
