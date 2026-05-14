import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "https://dev.sailratings.com";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(`${BASE}/?cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 30000 });

// 1. Initial state — nothing typed
await page.screenshot({ path: "/tmp/hero-1-initial.png", fullPage: false });
console.log("1. initial → /tmp/hero-1-initial.png");

// 2. Mid-typing — type "3" only (under min length)
await page.locator("#main-search").click();
await page.locator("#main-search").fill("3");
await page.waitForTimeout(500);
await page.screenshot({ path: "/tmp/hero-2-typing-3.png", fullPage: false });
console.log("2. typing '3' → /tmp/hero-2-typing-3.png");

// 3. After typing 3375 (exact match)
await page.locator("#main-search").fill("3375");
await page.waitForTimeout(1500);
await page.screenshot({ path: "/tmp/hero-3-exact.png", fullPage: false });
console.log("3. after '3375' (exact) → /tmp/hero-3-exact.png");

// 4. After typing 3775 (suggestions)
await page.locator("#main-search").fill("");
await page.waitForTimeout(300);
await page.locator("#main-search").fill("3775");
await page.waitForTimeout(1500);
await page.screenshot({ path: "/tmp/hero-4-suggestions.png", fullPage: false });
console.log("4. after '3775' (suggestions) → /tmp/hero-4-suggestions.png");

// 5. After typing abcxyz (empty state)
await page.locator("#main-search").fill("");
await page.waitForTimeout(300);
await page.locator("#main-search").fill("abcxyz");
await page.waitForTimeout(1500);
await page.screenshot({ path: "/tmp/hero-5-empty.png", fullPage: false });
console.log("5. after 'abcxyz' (empty) → /tmp/hero-5-empty.png");

// 6. After clearing back to nothing — is anything still showing?
await page.locator("#main-search").fill("");
await page.waitForTimeout(800);
await page.screenshot({ path: "/tmp/hero-6-cleared.png", fullPage: false });
console.log("6. after clear → /tmp/hero-6-cleared.png");

// Also dump the visible DOM under the search container
const containerHTML = await page.evaluate(() => {
  const input = document.querySelector("#main-search");
  if (!input) return "no input found";
  const container = input.closest(".relative")?.parentElement;
  return container?.outerHTML?.slice(0, 5000) ?? "no container";
});
console.log("\n=== container DOM after clear ===");
console.log(containerHTML);

await browser.close();
