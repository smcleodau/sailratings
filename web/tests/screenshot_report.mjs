/**
 * Walk through Search → click SUN FISH → capture the report card at several states:
 * - mid-streaming (some steps ticking)
 * - text-streaming
 * - fully done (TOC + CTA panel visible)
 */
import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "http://localhost:4200";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1800 } });
const page = await ctx.newPage();

await page.goto(`${BASE}/?cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 30000 });

// Search → SUN FISH
const input = page.locator("#main-search");
await input.click();
await input.fill("3375");
await page.locator("#search-results-wrap").waitFor({ state: "visible", timeout: 10000 });
await page.locator("#search-results li", { hasText: "SUN FISH" }).first().click();
await page.getByText(/Rating File/i).waitFor({ state: "visible", timeout: 10000 });

// Snapshot at multiple stream stages
await page.waitForTimeout(800);
await page.screenshot({ path: "/tmp/report-1-steps-early.png", fullPage: true });
console.log("1. early steps → /tmp/report-1-steps-early.png");

await page.waitForTimeout(2500);
await page.screenshot({ path: "/tmp/report-2-mid-stream.png", fullPage: true });
console.log("2. mid-stream → /tmp/report-2-mid-stream.png");

// Wait until the TOC appears (signals isDone)
await page.locator("text=The full report — 8 sections").waitFor({ state: "visible", timeout: 60000 });
await page.waitForTimeout(800);
await page.screenshot({ path: "/tmp/report-3-done.png", fullPage: true });
console.log("3. fully done → /tmp/report-3-done.png");

// Cropped: just the TOC + inline CTA area
const cta = page.locator("text=Open the full report").first();
const ctaBox = await cta.boundingBox();
if (ctaBox) {
  await page.screenshot({
    path: "/tmp/report-4-cta.png",
    clip: { x: 0, y: Math.max(0, ctaBox.y - 600), width: 1440, height: Math.min(1000, 1800 - ctaBox.y + 600) },
  });
  console.log("4. CTA panel zoom → /tmp/report-4-cta.png");
}

await browser.close();
