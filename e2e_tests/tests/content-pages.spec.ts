import { test, expect } from '@playwright/test';

test.describe('Content page smoke tests', () => {
  test('ratings page loads with heading', async ({ page }) => {
    await page.goto('/ratings');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15000 });
  });

  test('fleet page loads with heading', async ({ page }) => {
    await page.goto('/fleet');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15000 });
  });

  test('results page loads with heading', async ({ page }) => {
    await page.goto('/results');
    await expect(page.getByRole('heading', { level: 1 })).toBeVisible({ timeout: 15000 });
  });

  test('ratings page has the SailRatings brand', async ({ page }) => {
    await page.goto('/ratings');
    await expect(page.locator('img[alt="SailRatings"]')).toBeVisible({ timeout: 15000 });
  });
});
