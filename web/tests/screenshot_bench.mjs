import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "http://localhost:4200";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 1800 } });
const page = await ctx.newPage();

await page.goto(`${BASE}/?cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 30000 });
await page.locator("#main-search").click();
await page.locator("#main-search").fill("3375");
await page.locator("#search-results-wrap").waitFor({ state: "visible", timeout: 10000 });
await page.locator("#search-results li", { hasText: "SUN FISH" }).first().click();

// Wait for bench to render
await page.locator("#bench").waitFor({ state: "visible", timeout: 15000 });

// State 1 — early, working steps streaming
await page.waitForTimeout(1500);
await page.screenshot({ path: "/tmp/bench-1-early.png", fullPage: true });
console.log("1. early → /tmp/bench-1-early.png");

// State 2 — prose mid-stream
await page.waitForTimeout(3000);
await page.screenshot({ path: "/tmp/bench-2-mid.png", fullPage: true });
console.log("2. mid → /tmp/bench-2-mid.png");

// Wait for done (button appears)
await page.waitForFunction(
  () => Array.from(document.querySelectorAll("button")).some((b) => /Send me the file/i.test(b.textContent || "")),
  { timeout: 90000 },
);
await page.waitForTimeout(1200);
await page.screenshot({ path: "/tmp/bench-3-done.png", fullPage: true });
console.log("3. done → /tmp/bench-3-done.png");

// Crops:
const bench = page.locator("#bench");
const box = await bench.boundingBox();
if (box) {
  // Top — masthead + start of §1
  await page.screenshot({
    path: "/tmp/bench-top.png",
    clip: { x: 0, y: box.y, width: 1440, height: Math.min(900, box.height) },
  });
  console.log("crop top → /tmp/bench-top.png");

  // Middle — prose + redacted strip
  await page.screenshot({
    path: "/tmp/bench-mid.png",
    clip: { x: 0, y: box.y + 700, width: 1440, height: Math.min(900, box.height - 700) },
  });
  console.log("crop mid → /tmp/bench-mid.png");

  // Bottom — redacted + CTA
  await page.screenshot({
    path: "/tmp/bench-cta.png",
    clip: { x: 0, y: Math.max(box.y, box.y + box.height - 900), width: 1440, height: Math.min(900, box.height) },
  });
  console.log("crop cta → /tmp/bench-cta.png");
}

// Scroll past prose and capture sticky pill
await page.evaluate(() => window.scrollTo({ top: window.innerHeight * 1.5, behavior: "instant" }));
await page.waitForTimeout(600);
await page.screenshot({ path: "/tmp/bench-sticky.png", fullPage: false });
console.log("sticky → /tmp/bench-sticky.png");

await browser.close();
