import { test, expect, type Page, type Route } from '@playwright/test';

/* PAY-01-02 — "already own this report" state on the checkout rail.
 *
 * The whole API surface the home page touches is mocked with page.route, so
 * this spec needs no database, Stripe or Clerk:
 *   search → boat detail → SSE teaser → POST /checkout/create-session.
 * The only thing that differs between the two tests is the create-session
 * response: the owner gets {already_owned: true, report_url}, a non-owner
 * gets a Stripe checkout_url.
 */

const BOAT = {
  id: 4242,
  boat_name: 'Mocked Owner Boat',
  sail_number: 'GBR 4242',
  design: 'J/109',
  country: 'GBR',
  year_built: 2012,
  irc_tcc: 1.021,
};

const CORS = {
  'access-control-allow-origin': '*',
  'access-control-allow-headers': '*',
  'access-control-allow-methods': 'GET,POST,OPTIONS',
};

function json(route: Route, body: unknown) {
  return route.fulfill({
    status: 200,
    headers: { ...CORS, 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

const sse = (events: unknown[]) =>
  events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');

async function mockApi(
  page: Page,
  createSession: (route: Route) => Promise<void>,
): Promise<{ createSessionBodies: unknown[] }> {
  const createSessionBodies: unknown[] = [];

  await page.route('**/v1/**', async (route) => {
    const req = route.request();
    const url = new URL(req.url());

    if (req.method() === 'OPTIONS') {
      return route.fulfill({ status: 204, headers: CORS });
    }
    if (url.pathname.endsWith('/v1/search')) {
      return json(route, {
        query: BOAT.boat_name, total: 1, limit: 20, offset: 0,
        results: [{ ...BOAT, score: 1 }], suggestions: [],
      });
    }
    if (url.pathname.endsWith(`/v1/boats/${BOAT.id}`)) {
      return json(route, BOAT);
    }
    if (url.pathname.endsWith('/v1/insights/ask')) {
      return route.fulfill({
        status: 200,
        headers: { ...CORS, 'content-type': 'text/event-stream' },
        body: sse([
          { type: 'thought_chunk', data: 'Reading the certificate.' },
          { type: 'phase', data: 'report' },
          { type: 'text', data: 'A tidy J/109 with headroom on stability.' },
          { type: 'done', data: {} },
        ]),
      });
    }
    if (url.pathname.endsWith('/v1/checkout/create-session')) {
      createSessionBodies.push(req.postDataJSON());
      return createSession(route);
    }
    if (url.pathname.endsWith('/v1/stats')) {
      return json(route, {});
    }
    // Anything else (analytics, ads, …) is irrelevant to this spec.
    return route.fulfill({ status: 204, headers: CORS });
  });

  return { createSessionBodies };
}

/** Search for the mocked boat and wait for the checkout rail to appear. */
async function openReportRail(page: Page) {
  await page.goto('/');
  // The dev server hydrates lazily: a fill() before hydration is wiped when
  // React takes over, leaving Search disabled. Retry until the controlled
  // input has actually enabled the button.
  const search = page.getByRole('button', { name: 'Search boats' });
  await expect(async () => {
    await page.getByRole('combobox').fill('Mocked Owner');
    await expect(search).toBeEnabled({ timeout: 1_000 });
  }).toPass({ timeout: 30_000 });
  await search.click();
  // Sealed sections are revealed on a ~15 s timer; the rail follows them.
  await expect(page.getByRole('region', { name: 'Checkout' })).toBeVisible({
    timeout: 25_000,
  });
}

test.describe('PAY-01-02 already-owned checkout state', () => {
  test.setTimeout(60_000);

  test('owner sees the "already own this report" state, not a charge', async ({ page }) => {
    const reportUrl = 'http://localhost:4201/report/tok-owned-1';
    const { createSessionBodies } = await mockApi(page, (route) =>
      json(route, { already_owned: true, report_url: reportUrl }),
    );

    await openReportRail(page);
    const rail = page.getByRole('region', { name: 'Checkout' });
    await rail.getByTestId('buy-report').click();

    const owned = rail.getByTestId('already-own-report');
    await expect(owned).toBeVisible();
    await expect(owned).toContainText('You already own this report — open it');
    await expect(owned).toHaveAttribute('href', reportUrl);
    // The buy button is gone and we never left for Stripe.
    await expect(rail.getByTestId('buy-report')).toHaveCount(0);
    expect(page.url()).not.toContain('stripe');
    expect(createSessionBodies).toHaveLength(1);

    // Evidence screenshots at the design widths (1440 desktop / 390 mobile).
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.screenshot({ path: '/tmp/pay-01-02-owned.png' });
    await page.setViewportSize({ width: 390, height: 844 });
    await expect(owned).toBeVisible();
    await page.screenshot({ path: '/tmp/pay-01-02-owned-390.png' });
  });

  test('non-owner sees the buy button and is sent to Stripe', async ({ page }) => {
    const checkoutUrl = 'https://checkout.stripe.test/c/pay/cs_test_1';
    const { createSessionBodies } = await mockApi(page, (route) =>
      json(route, {
        checkout_url: checkoutUrl,
        order_token: 'tok-new-1',
        already_owned: false,
      }),
    );
    await page.route('https://checkout.stripe.test/**', (route) =>
      route.fulfill({ status: 200, contentType: 'text/html', body: '<h1>stripe stub</h1>' }),
    );

    await openReportRail(page);
    const rail = page.getByRole('region', { name: 'Checkout' });
    await expect(rail.getByTestId('already-own-report')).toHaveCount(0);

    const buy = rail.getByTestId('buy-report');
    await expect(buy).toBeVisible();
    await expect(buy).toContainText('Send me the file');
    await buy.click();

    await page.waitForURL(checkoutUrl);
    expect(createSessionBodies).toHaveLength(1);
    expect(createSessionBodies[0]).toMatchObject({ boat_id: BOAT.id });
  });
});
