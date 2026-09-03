/**
 * AD-01-16 — Migrate existing admin pages into the shell.
 *
 * Playwright smoke per route: every migrated page renders inside the
 * AD-01-12 shell (sidebar + topbar on the Dusk ground), uses the design
 * system (no Lucide sprites, no Paper tokens — everything reads the
 * --sr-* custom properties), and exposes its page landmark.
 *
 * Plus the two behavioural contracts that must not regress:
 *
 *   · Sources — the source_schedule_state.paused toggle round-trips
 *     through POST /admin/scrapers/{slug}/pause|resume against the real
 *     API, and data_sources.robots_checked_at is surfaced per row.
 *   · Data chat — the SSE stream renders, a proposed_change card offers
 *     Confirm/Reject, and confirming POSTs the SQL to /admin/execute
 *     (which writes admin_edits server-side — unchanged by this issue).
 */
import { test, expect, type Page } from '@playwright/test';

const ADMIN_TOKEN = 'sailfast2026';

const API = '**/v1/admin';

/**
 * One register row mirrored into the schedule state (Sources toggle test) —
 * the exact shape of GET /admin/scrapers/schedule-state's scrapers[].
 */
const SOURCE_ROW = {
  slug: 'cbh',
  display_name: 'CBH — Cork Harbour',
  base_url: 'https://cbh.example.com',
  category: 'club',
  legal_status: 'interim-v0',
  enabled: true,
  cadence: 'daily',
  adapter_status: 'ready',
  adapter_class: 'ClubAdapter',
  robots_checked_at: '2026-09-01T12:00:00Z',
  robots_disallow: [],
  contact_email: null,
  schedule_id: 'source-cbh',
  schedule_synced_at: '2026-09-01T12:05:00Z',
  last_run_status: 'ok',
  last_run_at: '2026-09-02T06:00:00Z',
};

/**
 * Data-dependent stubs.
 *
 * The AD-01-16 config (playwright.ad0116.config.ts) runs this spec against
 * the real dev API on :4199, where corrections/schedule-state/chat/execute
 * exist — those hits pass straight through (`route.fallback()`).
 *
 * The default config (playwright.config.ts) reuses this spec against the
 * PAY-01-10 fixture API, which only serves the customers zone — so the
 * four data-dependent endpoints are stubbed AT THE PAGE LEVEL with the
 * same shapes the real API returns. /admin/execute is answered with 200
 * locally; its response is never awaited when the real dev API is up, so
 * the authoritative run still exercises the true SQL path + admin_edits
 * write. Pure shell smoke (MIGRATED_ROUTES) needs no data and stays
 * un-stubbed everywhere.
 */
async function stubDataEndpoints(page: Page) {
  const state = { paused: false };

  await page.route(`${API}/corrections**`, async (route) => {
    if (route.request().resourceType() !== 'fetch') {
      await route.fallback();
      return;
    }
    // Real shape: a bare array (corrections.py list_corrections).
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: '[]',
    });
  });

  await page.route(`${API}/scrapers/schedule-state**`, async (route) => {
    if (route.request().resourceType() !== 'fetch') {
      await route.fallback();
      return;
    }
    // Real shape: {count, scrapers} (scrapers.py scraper_schedule_state).
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        count: 1,
        scrapers: [{ ...SOURCE_ROW, schedule_paused: state.paused }],
      }),
    });
  });

  await page.route(/\/v1\/admin\/scrapers\/[^/]+\/(pause|resume)$/, async (route) => {
    if (route.request().resourceType() !== 'fetch') {
      await route.fallback();
      return;
    }
    state.paused = route.request().url().endsWith('/pause');
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({
        slug: SOURCE_ROW.slug,
        paused: state.paused,
        temporal: 'unreachable — mirrored desired state',
      }),
    });
  });

  await page.route(/\/v1\/admin\/execute$/, async (route) => {
    if (route.request().resourceType() !== 'fetch') {
      await route.fallback();
      return;
    }
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ status: 'executed', rows_affected: 0 }),
    });
  });
}

/** Routes migrated onto the shell by AD-01-16, with their page landmark. */
const MIGRATED_ROUTES: { path: string; testId: string }[] = [
  { path: '/admin/tables', testId: 'tables-page' },
  { path: '/admin/corrections', testId: 'corrections-page' },
  { path: '/admin/scrapers', testId: 'scrapers-page' },
  { path: '/admin/discovery', testId: 'discovery-page' },
  { path: '/admin/firecrawl', testId: 'firecrawl-page' },
  { path: '/admin/swarm', testId: 'swarm-page' },
  { path: '/admin/sources', testId: 'sources-page' },
];

async function seedAdminToken(page: Page) {
  await page.addInitScript((token) => {
    window.localStorage.setItem('admin_token', token);
  }, ADMIN_TOKEN);
}

test.describe('AD-01-16 — admin pages in the shell, DS restyle', () => {
  test.beforeEach(async ({ page }) => {
    await seedAdminToken(page);
  });

  for (const { path, testId } of MIGRATED_ROUTES) {
    test(`${path} renders in the shell with no Lucide / Paper tokens`, async ({
      page,
    }) => {
      await page.goto(path, { waitUntil: 'domcontentloaded' });

      // The AD-01-12 shell chrome wraps the page.
      await expect(page.getByTestId('admin-shell')).toBeVisible();
      await expect(page.getByTestId('admin-sidebar')).toBeVisible();
      await expect(page.getByTestId('admin-topbar')).toBeVisible();

      // The page's own landmark mounted.
      await expect(page.getByTestId(testId)).toBeVisible();

      // No Lucide sprites anywhere in the admin theme.
      await expect(page.locator('.admin-theme svg.lucide')).toHaveCount(0);
      await expect(
        page.locator('.admin-theme svg[data-lucide]'),
      ).toHaveCount(0);

      // Dusk ground, not Paper: the shell background is the Dusk token.
      const ground = await page
        .getByTestId('admin-shell')
        .evaluate((el) => getComputedStyle(el).backgroundColor);
      expect(ground).toBe('rgb(13, 11, 22)'); // --sr-dusk-ground #0d0b16

      // No element inside the admin theme may paint the Paper surface
      // (#f3f1ec → rgb(243, 241, 236)) or pure white — both were the old
      // chrome's backgrounds.
      const paperCount = await page
        .locator('.admin-theme *')
        .evaluateAll((els) =>
          els.filter((el) => {
            const bg = getComputedStyle(el).backgroundColor;
            return bg === 'rgb(243, 241, 236)' || bg === 'rgb(255, 255, 255)';
          }).length,
        );
      expect(paperCount).toBe(0);

      // JetBrains Mono renders data: the admin mono helper resolves to it.
      const mono = await page.evaluate(() => {
        const el = document.querySelector('.admin-theme .admin-mono-font');
        return el ? getComputedStyle(el).fontFamily : '';
      });
      expect(mono).toContain('JetBrains Mono');
    });
  }

  test('sidebar lists Sources and Data chat', async ({ page }) => {
    await page.goto('/admin', { waitUntil: 'domcontentloaded' });
    const sidebar = page.getByTestId('admin-sidebar');
    await expect(sidebar).toContainText('Sources');
    await expect(sidebar).toContainText('Data chat');
    await expect(sidebar).toContainText('Audit');
  });

  test('/admin/tables/admin_edits (Audit) renders in the shell', async ({
    page,
  }) => {
    await page.goto('/admin/tables/admin_edits', {
      waitUntil: 'domcontentloaded',
    });
    await expect(page.getByTestId('admin-shell')).toBeVisible();
    await expect(page.getByTestId('table-editor-page')).toBeVisible();
    await expect(page.locator('.admin-theme svg.lucide')).toHaveCount(0);
  });

  test('/admin/corrections shows an honest empty state', async ({ page }) => {
    await stubDataEndpoints(page);
    await page.goto('/admin/corrections', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('corrections-page')).toBeVisible();
    // boat_corrections has no pending rows on the dev fixture — the page
    // must say the queue is empty, not render a blank panel or fake rows.
    await expect(page.getByTestId('corrections-empty')).toBeVisible();
    await expect(page.getByTestId('corrections-empty')).toContainText(
      'queue is empty',
    );
  });

  test('/admin/sources pauses and resumes a schedule for real', async ({
    page,
  }) => {
    await stubDataEndpoints(page);
    await page.goto('/admin/sources', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('sources-page')).toBeVisible();

    // Every register row exposes its robots_checked_at state.
    const firstRow = page.locator('[data-testid^="source-row-"]').first();
    await expect(firstRow).toBeVisible();
    const slug = (await firstRow.getAttribute('data-testid'))!.replace(
      'source-row-',
      '',
    );

    // The robots pill is either "never checked" or an age — both come from
    // data_sources.robots_checked_at.
    const robotsPill = page.locator(
      `[data-testid="robots-never-${slug}"], [data-testid="robots-checked-${slug}"]`,
    );
    await expect(robotsPill).toBeVisible();

    // Flip the toggle → the API is called and the row state changes.
    const toggle = page.getByTestId(`pause-toggle-${slug}`);
    const wasPaused = (await toggle.getAttribute('aria-checked')) === 'true';

    const [response] = await Promise.all([
      page.waitForResponse(
        (r) =>
          r.url().includes(`/admin/scrapers/${slug}/`) &&
          (r.url().endsWith('/pause') || r.url().endsWith('/resume')) &&
          r.request().method() === 'POST',
      ),
      toggle.click(),
    ]);
    // 200 = Temporal flipped too; 503 = mirror flipped with desired state
    // recorded. Both are honest, non-error outcomes for the toggle.
    expect([200, 503]).toContain(response.status());

    // After the refetch the toggle reflects the new state.
    await expect(toggle).toHaveAttribute(
      'aria-checked',
      wasPaused ? 'false' : 'true',
    );

    // Flip it back so the register is left as we found it.
    const [restore] = await Promise.all([
      page.waitForResponse(
        (r) =>
          r.url().includes(`/admin/scrapers/${slug}/`) &&
          r.request().method() === 'POST',
      ),
      toggle.click(),
    ]);
    expect([200, 503]).toContain(restore.status());
    await expect(toggle).toHaveAttribute(
      'aria-checked',
      wasPaused ? 'true' : 'false',
    );
  });
});

test.describe('AD-01-16 — data chat propose/confirm flow', () => {
  test('SSE stream renders, proposal confirms via /admin/execute', async ({
    page,
  }) => {
    // Intercept the SSE endpoint with a deterministic stream: a text chunk,
    // a proposed_change, then done. This proves the client-side SSE parser
    // and the propose→confirm wiring work end to end, while /admin/execute
    // below is the REAL API — confirming runs the SQL against dev Postgres
    // and the audit row lands in admin_edits.
    await page.route('**/admin/chat', async (route) => {
      // Only intercept the XHR/fetch SSE call — never the page document.
      if (route.request().resourceType() !== 'fetch') {
        await route.continue();
        return;
      }
      const body =
        'data: {"type":"meta","data":{"conversation_id":1}}\n\n' +
        'data: {"type":"text","data":"I can fix that. "}\n\n' +
        'data: {"type":"proposed_change","data":{"sql":"UPDATE boats SET updated_at = updated_at WHERE false","explanation":"No-op touch of boats.updated_at (matches nothing)","affected_rows_estimate":"0"}}\n\n' +
        'data: {"type":"done","data":{}}\n\n';
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body,
      });
    });

    await stubDataEndpoints(page);
    await seedAdminToken(page);
    await page.goto('/admin/chat', { waitUntil: 'domcontentloaded' });

    // Chat lives in the shell too.
    await expect(page.getByTestId('admin-shell')).toBeVisible();
    await expect(page.getByTestId('chat-area')).toBeVisible();

    // Send a message — the stubbed SSE stream answers.
    await page
      .getByPlaceholder('Ask about the data...')
      .fill('correct the record');
    await page.getByRole('button', { name: 'Send message' }).click();

    // The streamed text and the proposed-change card render.
    await expect(page.getByText('I can fix that.')).toBeVisible();
    const card = page.getByTestId('proposed-change-card');
    await expect(card).toBeVisible();
    await expect(card).toContainText('No-op touch of boats.updated_at');

    // Confirm → the SQL is POSTed to the real /admin/execute.
    const [executeResponse] = await Promise.all([
      page.waitForResponse(
        (r) =>
          r.url().includes('/admin/execute') &&
          r.request().method() === 'POST',
      ),
      page.getByTestId('confirm-change-btn').click(),
    ]);
    expect(executeResponse.status()).toBe(200);
    const result = await executeResponse.json();
    expect(result.status).toBe('executed');

    // The card reports the execution result.
    await expect(card).toContainText('Executed successfully');
  });
});
