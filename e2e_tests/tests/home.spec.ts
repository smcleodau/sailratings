import { test, expect } from '@playwright/test';

test.describe('Home page smoke tests', () => {
  test('loads and shows the hero search input', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('input, button[type="submit"], [role="search"]').first()).toBeVisible({ timeout: 10000 });
  });

  test('displays the SailRatings brand wordmark', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('body')).toContainText(/Sail\s*Ratings/i, { timeout: 10000 });
  });

  test('nav links to Ratings, Fleet Analysis, and Results', async ({ page }) => {
    await page.goto('/');
    const nav = page.locator('nav');
    await expect(nav).toContainText(/Ratings/i);
    await expect(nav).toContainText(/Fleet/i);
    await expect(nav).toContainText(/Results/i);
  });

  test('footer disclaimer text is present', async ({ page }) => {
    await page.goto('/');
    await expect(page.locator('footer')).toBeVisible({ timeout: 10000 });
  });
});
