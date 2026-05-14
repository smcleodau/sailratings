import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "http://localhost:4200";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(`${BASE}/?cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 30000 });
await page.locator("#main-search").click();
await page.locator("#main-search").fill("3375");
await page.locator("#search-results-wrap").waitFor({ state: "visible", timeout: 10000 });
await page.locator("#search-results li", { hasText: "SUN FISH" }).first().click();

await page.waitForFunction(
  () => Array.from(document.querySelectorAll("button")).some((b) => /Send me the file/i.test(b.textContent || "")),
  { timeout: 90000 },
);
await page.waitForTimeout(1500);

// Scroll to bench
await page.evaluate(() => document.querySelector("#bench")?.scrollIntoView({ behavior: "instant", block: "start" }));
await page.waitForTimeout(500);
await page.screenshot({ path: "/tmp/strip-1-top.png", fullPage: false });

await page.evaluate(() => window.scrollBy(0, 500));
await page.waitForTimeout(300);
await page.screenshot({ path: "/tmp/strip-2-prose.png", fullPage: false });

await page.evaluate(() => window.scrollBy(0, 500));
await page.waitForTimeout(300);
await page.screenshot({ path: "/tmp/strip-3-redacted.png", fullPage: false });

await page.evaluate(() => window.scrollBy(0, 500));
await page.waitForTimeout(300);
await page.screenshot({ path: "/tmp/strip-4-cta.png", fullPage: false });

console.log("done");
await browser.close();
