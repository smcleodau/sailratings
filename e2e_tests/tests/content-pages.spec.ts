import { test, expect } from '@playwright/test';

test.describe('Content page smoke tests', () => {
  test('ratings page loads with heading', async ({ page }) => {
    await page.goto('/ratings');
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 10000 });
  });

  test('fleet page loads with heading', async ({ page }) => {
    await page.goto('/fleet');
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 10000 });
  });

  test('results page loads with heading', async ({ page }) => {
    await page.goto('/results');
    await expect(page.locator('h1, h2').first()).toBeVisible({ timeout: 10000 });
  });

  test('ratings page has the SailRatings brand', async ({ page }) => {
    await page.goto('/ratings');
    await expect(page.locator('body')).toContainText(/Sail\s*Ratings/i, { timeout: 10000 });
  });
});
