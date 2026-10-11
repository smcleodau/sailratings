import { test, expect, type Page, type Route } from '@playwright/test';

/**
 * AD-01-09 — Admin: Corrections moderation page.
 *
 * The combined fixture API (fixtures/admin_combined_api.py) does not mount
 * the corrections router, so every /v1/admin/corrections call is stubbed
 * with page.route — the assertions are about what the page sends and how it
 * renders, not about the backend (api/tests/test_corrections_api.py covers
 * that).
 */

const CORRECTIONS_URL = /\/v1\/admin\/corrections(\?|\/|$)/;

const PENDING_ROW = {
  id: 7,
  boat_id: 1,
  boat_name: 'Testarossa',
  field_name: 'designer',
  current_value: 'Old Designer',
  proposed_value: 'New Designer',
  submitted_email: 'owner@example.com',
  submitted_at: new Date().toISOString(),
  status: 'pending',
};

const APPLIED_ROW = { ...PENDING_ROW, id: 8, status: 'applied' };

interface Recorded {
  method: string;
  path: string;
}

/** Stub the corrections API: `byStatus` answers GET /admin/corrections?status=. */
async function stubCorrections(
  page: Page,
  byStatus: Record<string, unknown[]>,
): Promise<Recorded[]> {
  const calls: Recorded[] = [];
  await page.route(CORRECTIONS_URL, async (route: Route) => {
    const req = route.request();
    const url = new URL(req.url());
    calls.push({ method: req.method(), path: url.pathname + url.search });
    if (req.method() === 'POST') {
      const id = Number(url.pathname.split('/').slice(-2)[0]);
      const action = url.pathname.split('/').pop();
      const status = action === 'approve' ? 'applied' : action === 'revert' ? 'reverted' : 'rejected';
      return route.fulfill({ json: { status, id } });
    }
    const status = url.searchParams.get('status') ?? 'pending';
    return route.fulfill({ json: byStatus[status] ?? [] });
  });
  return calls;
}

test.describe('AD-01-09 admin corrections', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('admin_token', 'sailfast2026');
    });
  });

  test('renders inside the admin shell', async ({ page }) => {
    await stubCorrections(page, { pending: [PENDING_ROW] });
    await page.goto('/admin/corrections', { waitUntil: 'domcontentloaded' });

    await expect(page.getByTestId('admin-shell')).toBeVisible();
    await expect(page.getByTestId('admin-sidebar')).toBeVisible();
    await expect(page.locator('h1:has-text("Corrections")')).toBeVisible();
    await expect(page.getByText('Testarossa')).toBeVisible();
    await expect(page.getByText('New Designer')).toBeVisible();

    await page.screenshot({ path: 'test-results/ad-01-09-corrections.png', fullPage: true });
  });

  test('shows the honest empty state when the API returns []', async ({ page }) => {
    await stubCorrections(page, {});
    await page.goto('/admin/corrections', { waitUntil: 'domcontentloaded' });

    await expect(
      page.getByText('No corrections proposed yet — boat_corrections is empty'),
    ).toBeVisible();
  });

  test('approve button POSTs /approve', async ({ page }) => {
    const calls = await stubCorrections(page, { pending: [PENDING_ROW] });
    await page.goto('/admin/corrections', { waitUntil: 'domcontentloaded' });

    const approve = page.waitForRequest(
      (r) => r.method() === 'POST' && /\/v1\/admin\/corrections\/7\/approve$/.test(r.url()),
    );
    await page.getByRole('button', { name: 'Approve', exact: true }).click();
    await approve;

    await expect(page.getByText('Testarossa')).toHaveCount(0);
    expect(calls.some((c) => c.method === 'POST' && c.path.endsWith('/7/approve'))).toBe(true);
  });

  test('revert button POSTs /revert', async ({ page }) => {
    const calls = await stubCorrections(page, { applied: [APPLIED_ROW] });
    page.on('dialog', (d) => d.accept());
    await page.goto('/admin/corrections', { waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: 'applied', exact: true }).click();

    const revert = page.waitForRequest(
      (r) => r.method() === 'POST' && /\/v1\/admin\/corrections\/8\/revert$/.test(r.url()),
    );
    await page.getByRole('button', { name: 'Revert', exact: true }).click();
    await revert;

    await expect(page.getByText('Testarossa')).toHaveCount(0);
    expect(calls.some((c) => c.method === 'POST' && c.path.endsWith('/8/revert'))).toBe(true);
  });
});
