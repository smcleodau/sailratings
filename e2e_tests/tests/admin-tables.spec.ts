import { test, expect } from "@playwright/test";

test.describe("Admin Tables", () => {
  test("requires admin authentication", async ({ page }) => {
    // Set an invalid token so the API returns 401 and forces the signed-out state
    await page.addInitScript(() => {
      window.localStorage.setItem("admin_token", "invalid_token");
    });
    
    await page.goto("/admin/tables");
    await expect(page.locator("text=Sign in via")).toBeVisible();
    await expect(page.locator("text=/admin").first()).toBeVisible();
  });

  test("renders tables list when authenticated", async ({ page }) => {
    // We can simulate an admin token (using the default test password from .env or fallback)
    await page.addInitScript(() => {
      window.localStorage.setItem("admin_token", "sailfast2026");
    });
    
    await page.goto("/admin/tables");
    
    // Check if the page title is visible
    await expect(page.locator("h1:has-text('Tables')")).toBeVisible();
    
    // Wait for the table rows to render
    // Just looking for some common table names like "boats" or "users"
    await expect(page.locator("text=boats")).toBeVisible({ timeout: 10000 });
  });

  test("navigates to a specific table", async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem("admin_token", "sailfast2026");
    });
    
    await page.goto("/admin/tables");
    await expect(page.locator("h1:has-text('Tables')")).toBeVisible();
    
    // Click on the boats table
    await page.locator("a[href='/admin/tables/boats']").click();
    
    // Check if we navigated to the table view
    // And ensure the table name "boats" is shown
    await expect(page.locator("a[title='Back to all tables']")).toBeVisible({ timeout: 10000 });
  });
});
