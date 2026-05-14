import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 2400 } });
const page = await ctx.newPage();

await page.goto(`http://localhost:4200/?cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 30000 });
await page.locator("#main-search").click();
await page.locator("#main-search").fill("3375");
await page.locator("#search-results-wrap").waitFor({ state: "visible", timeout: 10000 });
await page.locator("#search-results li", { hasText: "SUN FISH" }).first().click();

// Wait 25s for streaming to finish
await page.waitForTimeout(25000);

await page.screenshot({ path: "/tmp/debug-after-25s.png", fullPage: true });
console.log("snapshot after 25s → /tmp/debug-after-25s.png");

// Dump button labels
const btns = await page.evaluate(() => Array.from(document.querySelectorAll("button")).map((b) => b.textContent?.trim()));
console.log("buttons on page:", btns);

// Check for "Section" text
const hasSectionText = await page.evaluate(() => {
  return Array.from(document.querySelectorAll("*")).filter((el) => el.children.length === 0 && /Section 1 covers/i.test(el.textContent || "")).length;
});
console.log("'Section 1 covers' instances:", hasSectionText);

// Check for "8 sections" text
const hasFullReport = await page.evaluate(() => {
  return Array.from(document.querySelectorAll("*")).filter((el) => el.children.length === 0 && /full report/i.test(el.textContent || "")).length;
});
console.log("'full report' instances:", hasFullReport);

await browser.close();
