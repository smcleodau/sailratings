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

// Wait long enough for stream to finish + TOC to render
await page.waitForTimeout(20000);

// Now scroll to the report card
await page.evaluate(() => {
  const el = Array.from(document.querySelectorAll("h3")).find((h) => /SUN FISH/i.test(h.textContent || ""));
  if (el) el.scrollIntoView({ behavior: "instant", block: "start" });
});
await page.waitForTimeout(500);

// Capture viewport from where the card starts (after scroll)
await page.screenshot({ path: "/tmp/zoom-top.png", fullPage: false });
console.log("top → /tmp/zoom-top.png");

// Scroll down ~600px and shoot again (middle of card)
await page.evaluate(() => window.scrollBy(0, 600));
await page.waitForTimeout(300);
await page.screenshot({ path: "/tmp/zoom-mid.png", fullPage: false });
console.log("mid → /tmp/zoom-mid.png");

// Scroll down to the CTA panel
await page.evaluate(() => {
  const btns = Array.from(document.querySelectorAll("button")).filter((b) => /Open the full report/i.test(b.textContent || ""));
  if (btns[0]) btns[0].scrollIntoView({ behavior: "instant", block: "center" });
});
await page.waitForTimeout(500);
await page.screenshot({ path: "/tmp/zoom-cta.png", fullPage: false });
console.log("cta → /tmp/zoom-cta.png");

await browser.close();
