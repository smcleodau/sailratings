import { test, expect } from '@playwright/test';

test.describe('Home page smoke tests', () => {
  test('loads and shows the hero search input', async ({ page }) => {
    await page.goto('/');
    // The main search input is the core interaction element on the home page
    const searchInput = page.locator('#main-search');
    await expect(searchInput).toBeVisible({ timeout: 15000 });
  });

  test('displays the SailRatings brand wordmark', async ({ page }) => {
    await page.goto('/');
    // The brand wordmark image is in MainNav
    const brandImg = page.locator('img[alt="SailRatings"]');
    await expect(brandImg).toBeVisible({ timeout: 15000 });
  });

  test('nav links to Ratings, Fleet Analysis, and Results', async ({ page }) => {
    await page.goto('/');
    const nav = page.locator('nav');
    await expect(nav.getByRole('link', { name: 'Ratings', exact: true })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Fleet Analysis' })).toBeVisible();
    await expect(nav.getByRole('link', { name: 'Results' })).toBeVisible();
  });

  test('footer disclaimer text is present', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText(/Not affiliated with the RORC Rating Office/i)).toBeVisible({ timeout: 15000 });
  });
});
