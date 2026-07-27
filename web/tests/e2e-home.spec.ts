import { test, expect } from "@playwright/test";

/**
 * Self-contained E2E smoke test for the SailRatings landing page.
 *
 * It does not depend on a live external API: the hero search input and
 * headline are rendered on first paint, so we assert the hero is present and
 * interactive. This keeps `npx playwright test` green in any environment that
 * can run `next dev` (Playwright spins the dev server up via webServer).
 */
test.describe("Hero search", () => {
  test("renders the search input on first paint", async ({ page }) => {
    await page.goto("/");

    const input = page.locator("#main-search");
    await expect(input).toBeVisible();
    await expect(input).toHaveAttribute(
      "placeholder",
      "Boat name, sail number, or design"
    );
    await expect(input).toHaveAttribute("role", "combobox");
  });

  test("renders the headline and subline", async ({ page }) => {
    await page.goto("/");

    await expect(
      page.getByRole("heading", {
        level: 1,
        name: /analysis your competitors wish they had/i,
      })
    ).toBeVisible();

    await expect(
      page.getByText(/We analyze over 31,000 race results/i)
    ).toBeVisible();
  });

  test("exposes an accessible Search button that is disabled until a query is typed", async ({
    page,
  }) => {
    await page.goto("/");

    const searchButton = page.getByRole("button", { name: "Search boats" });
    await expect(searchButton).toBeVisible();
    // No query yet → button is disabled.
    await expect(searchButton).toBeDisabled();

    // Type a query → button becomes enabled.
    await page.locator("#main-search").fill("SUN FISH");
    await expect(searchButton).toBeEnabled();
  });
});

