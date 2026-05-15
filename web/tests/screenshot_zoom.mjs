import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "http://localhost:4200";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 2400 } });
const page = await ctx.newPage();

await page.goto(`${BASE}/?cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 30000 });
await page.locator("#main-search").click();
await page.locator("#main-search").fill("3375");
await page.locator("#search-results-wrap").waitFor({ state: "visible", timeout: 10000 });
await page.locator("#search-results li", { hasText: "SUN FISH" }).first().click();

await page.locator("text=Rating File").waitFor({ state: "visible", timeout: 15000 });

// Wait until the inline CTA button appears (signals isDone)
await page.waitForFunction(
  () => Array.from(document.querySelectorAll("button")).some((b) => /Open the full report/i.test(b.textContent || "")),
  { timeout: 60000 },
);
await page.waitForTimeout(1500);

// Whole card capture
const reportCard = page.locator("text=Rating File").first().locator("xpath=ancestor::div[contains(@class, 'border-border')][1]");
const box = await reportCard.boundingBox();
if (box) {
  await page.screenshot({
    path: "/tmp/report-card-only.png",
    clip: { x: Math.max(0, box.x - 20), y: Math.max(0, box.y - 10), width: Math.min(box.width + 40, 1440), height: Math.min(box.height + 20, 2400 - Math.max(0, box.y - 10)) },
  });
  console.log(`card: w=${box.width.toFixed(0)} h=${box.height.toFixed(0)}`);
}

// Top of card — steps panel
if (box) {
  await page.screenshot({
    path: "/tmp/report-steps-zoom.png",
    clip: { x: Math.max(0, box.x - 20), y: Math.max(0, box.y - 10), width: Math.min(box.width + 40, 1440), height: 500 },
  });
  console.log("steps captured");
}

// Bottom of card — TOC + CTA
const ctaButton = page.locator("button", { hasText: /Open the full report/i });
const ctaBox = await ctaButton.boundingBox();
if (ctaBox) {
  await page.screenshot({
    path: "/tmp/report-cta-zoom.png",
    clip: { x: Math.max(0, (box?.x ?? 0) - 20), y: Math.max(0, ctaBox.y - 500), width: Math.min((box?.width ?? 1400) + 40, 1440), height: 600 },
  });
  console.log("cta captured");
}

await browser.close();
