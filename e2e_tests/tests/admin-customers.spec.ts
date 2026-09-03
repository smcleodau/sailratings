import { test, expect } from '@playwright/test';

/**
 * PAY-01-10 — Admin Customers zone.
 *
 * Covers the three pages backed by the new endpoints:
 *   /admin/users    (Users & plans — plan, boats claimed, reports, joined, last seen)
 *   /admin/orders   (Reports & orders — 47 rows incl. 37 abandoned)
 *   /admin/billing  (Stripe & pricing — pro_monthly_gbp / pro_annual_gbp)
 *
 * The webServer in playwright.config.ts starts the frontend on :4201 with
 * NEXT_PUBLIC_API_BASE pointing at the verification API on :4101 (scratch
 * DB pay0110_verify seeded with 5 users / 6 claims / 47 orders).
 */

const ADMIN_PW = process.env.ADMIN_PASSWORD || 'sailfast2026';

test.beforeEach(async ({ page }) => {
  // Plant the admin token (same flow the login form completes)
  await page.goto('/admin');
  await page.evaluate((pw) => localStorage.setItem('admin_token', pw), ADMIN_PW);
});

test('users & plans page lists customers with plan, boats, reports, joined, last seen', async ({ page }) => {
  await page.goto('/admin/users');

  await expect(page.getByRole('heading', { name: /users & plans/i })).toBeVisible();

  // Table headers required by the acceptance criteria
  for (const col of ['Customer', 'Plan', 'Role', 'Boats', 'Reports', 'Spend', 'Joined', 'Last seen']) {
    await expect(page.getByRole('columnheader', { name: new RegExp(col, 'i') })).toBeVisible();
  }

  // Seeded demo customers visible
  await expect(page.getByTestId('user-row-alice.waters@example.com')).toBeVisible();
  await expect(page.getByTestId('user-row-bob.north@example.com')).toBeVisible();

  // Alice: plan pro badge, 1 verified boat, 5 reports bought
  const alice = page.getByTestId('user-row-alice.waters@example.com');
  await expect(alice.getByText('pro', { exact: true })).toBeVisible();
  await expect(alice.getByText('claim pending')).toBeVisible();
  await expect(alice).toContainText('5'); // reports bought
  await expect(alice).toContainText('$455'); // total spend 45500 cents

  // Open in Stripe link for customers with a stripe_customer_id
  await expect(alice.getByRole('link', { name: /stripe/i })).toHaveAttribute(
    'href',
    /dashboard\.stripe\.com\/customers\//,
  );

  // Filters: claims=pending keeps only users with pending claims
  await page.getByRole('button', { name: /claims pending/i }).click();
  await expect(page.getByTestId('user-row-alice.waters@example.com')).toBeVisible();
  await expect(page.getByTestId('user-row-dave.helm@example.com')).toHaveCount(0);

  // Search reset: q=carol shows only Carol
  await page.getByRole('button', { name: /claims pending/i }).click();
  await page.getByPlaceholder(/search email, name or boat/i).fill('carol');
  await expect(page.getByTestId('user-row-carol.drift@example.com')).toBeVisible();
  await expect(page.getByTestId('user-row-alice.waters@example.com')).toHaveCount(0);

  // Expand a row → detail with boats + orders sections (click the chevron
  // cell — the first column carries no stopPropagation controls)
  await page.getByPlaceholder(/search email, name or boat/i).fill('alice');
  const aliceRow = page.getByTestId('user-row-alice.waters@example.com');
  await expect(aliceRow).toBeVisible();
  await aliceRow.locator('td').first().click();
  await expect(page.getByText(/Boats \(2\)/)).toBeVisible();
  await expect(page.getByText(/Orders \(5\)/)).toBeVisible();
});

test('reports & orders page shows all 47 rows incl. 37 abandoned', async ({ page }) => {
  await page.goto('/admin/orders');

  await expect(page.getByRole('heading', { name: /reports & orders/i })).toBeVisible();

  // Summary line carries the honest counts
  await expect(page.getByText(/47 orders/i)).toBeVisible();
  await expect(page.getByText(/37 abandoned/i)).toBeVisible();

  // Status filter pills include counts
  await expect(page.getByRole('button', { name: /abandoned \(37\)/i })).toBeVisible();

  // First page renders 47 rows (limit 200 default covers all)
  await expect(page.locator('[data-testid^="order-row-"]')).toHaveCount(47);

  // Filter to abandoned only
  await page.getByRole('button', { name: /^abandoned \(37\)$/i }).click();
  await expect(page.locator('[data-testid^="order-row-"]')).toHaveCount(37);
  await expect(page.getByText(/Abandoned/).first()).toBeVisible();

  // Money column renders in starboard class (design rule: money in Starboard)
  const moneyCell = page.locator('td.text-\\[var\\(--sr-starboard\\)\\]').first();
  await expect(moneyCell).toBeVisible();
});

test('stripe & pricing page shows pro plans from the live catalogue', async ({ page }) => {
  await page.goto('/admin/billing');

  await expect(page.getByRole('heading', { name: /stripe & pricing/i })).toBeVisible();

  // Acceptance: pro_monthly_gbp and pro_annual_gbp from the live catalogue
  await expect(page.getByTestId('plan-pro_monthly_gbp')).toBeVisible();
  await expect(page.getByTestId('plan-pro_annual_gbp')).toBeVisible();
  await expect(page.getByTestId('plan-pro_monthly_gbp')).toContainText('£29');
  await expect(page.getByTestId('plan-pro_annual_gbp')).toContainText('£290');
  await expect(page.getByTestId('plan-pro_annual_gbp')).toContainText('per year');

  // Promo codes + balance + charges sections
  await expect(page.getByRole('heading', { name: /promo codes \(\d+\)/i })).toBeVisible();
  await expect(page.getByText(/LAUNCH20/)).toBeVisible();
  await expect(page.getByRole('heading', { name: /^balance$/i })).toBeVisible();
  await expect(page.getByRole('heading', { name: /last \d+ charges/i })).toBeVisible();
});

test('customers zone tabs appear in the admin nav', async ({ page }) => {
  await page.goto('/admin/users');
  await expect(page.getByRole('link', { name: /users & plans/i })).toBeVisible();
  await expect(page.getByRole('link', { name: /reports & orders/i })).toBeVisible();
  await expect(page.getByRole('link', { name: /stripe & pricing/i })).toBeVisible();
});
