import { test, expect } from '@playwright/test';

/**
 * AD-01-12 — Admin shell in SailRatings DS.
 *
 * Verifies every admin route renders inside the new shell: 232px sidebar
 * (Today / Data quality / Operations / Customers / Agents), Dusk ground,
 * topbar with global search (`/` focuses), env badge, and health pills.
 *
 * Admin pages use an internal bearer-token gate (AD-01-01), not Clerk, so
 * the shell chrome renders in dev without auth. We seed localStorage before
 * navigation so the pages render their content rather than a login gate.
 */

const ADMIN_ROUTES = [
  '/admin',
  '/admin/data-health',
  '/admin/corrections',
  '/admin/identity',
  '/admin/tables',
  '/admin/scrapers',
  '/admin/discovery',
  '/admin/firecrawl',
  '/admin/stripe-events',
  '/admin/swarm',
] as const;

const SIDEBAR_SECTIONS = ['Today', 'Data quality', 'Operations', 'Customers', 'Agents'];

test.describe('AD-01-12 admin shell', () => {
  test.beforeEach(async ({ page }) => {
    // Seed the admin token so pages skip their password gate; the API may
    // reject it (overview 404s → counts render 0), which the shell tolerates.
    await page.addInitScript(() => {
      window.localStorage.setItem('admin_token', 'sailfast2026');
    });
  });

  for (const route of ADMIN_ROUTES) {
    test(`${route} renders inside the shell`, async ({ page }) => {
      await page.goto(route, { waitUntil: 'domcontentloaded' });

      // Shell chrome present
      await expect(page.getByTestId('admin-shell')).toBeVisible();
      await expect(page.getByTestId('admin-sidebar')).toBeVisible();
      await expect(page.getByTestId('admin-topbar')).toBeVisible();

      // Sidebar is exactly 232px wide
      const width = await page.getByTestId('admin-sidebar').evaluate((el) =>
        el.getBoundingClientRect().width,
      );
      expect(width).toBe(232);

      // All five sections listed
      for (const section of SIDEBAR_SECTIONS) {
        await expect(page.getByTestId('admin-sidebar')).toContainText(section);
      }

      // Topbar chrome: search, env badge, health pills
      await expect(page.getByTestId('admin-global-search')).toBeVisible();
      await expect(page.getByTestId('admin-env-badge')).toBeVisible();
      await expect(page.getByTestId('admin-health-pills')).toBeVisible();

      // Dusk ground: the shell root must resolve to the Dusk surface colour,
      // not Paper. --sr-dusk-ground = #0d0b16 → rgb(13, 11, 22).
      const ground = await page.getByTestId('admin-shell').evaluate((el) =>
        getComputedStyle(el).backgroundColor,
      );
      expect(ground).toBe('rgb(13, 11, 22)');
    });
  }

  test('`/` focuses the global search', async ({ page }) => {
    await page.goto('/admin');
    const search = page.getByTestId('admin-global-search');
    await expect(search).toBeVisible();
    // Move focus out of the input, press `/`, and confirm the topbar's
    // keydown handler pulls focus back into the global search. Retrying the
    // press handles the keydown listener attaching after hydration.
    await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
    await expect(async () => {
      await page.keyboard.press('/');
      await expect(search).toBeFocused({ timeout: 1000 });
    }).toPass({ timeout: 10000 });
  });

  test('sidebar counts render 0 until AD-01-13 ships', async ({ page }) => {
    await page.goto('/admin', { waitUntil: 'domcontentloaded' });
    for (const key of ['today', 'data_quality', 'operations', 'customers', 'agents']) {
      await expect(page.getByTestId(`sidebar-count-${key}`)).toHaveText('0');
    }
  });

  test('no lucide-react SVG sprite attributes leak into the shell', async ({ page }) => {
    await page.goto('/admin', { waitUntil: 'domcontentloaded' });
    // lucide-react marks its svgs with data-lucide / class "lucide".
    await expect(page.locator('.admin-theme svg.lucide')).toHaveCount(0);
    await expect(page.locator('.admin-theme svg[data-lucide]')).toHaveCount(0);
  });
});
