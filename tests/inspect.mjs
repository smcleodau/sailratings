import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "https://dev.sailratings.com";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(`${BASE}/?cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 30000 });
await page.locator("#main-search").click();
await page.locator("#main-search").fill("3375");
await page.waitForTimeout(2000);

// Zoomed crop of just the search region
const inputBox = await page.locator("#main-search").boundingBox();
if (inputBox) {
  await page.screenshot({
    path: "/tmp/zoom-search.png",
    clip: {
      x: Math.max(0, inputBox.x - 50),
      y: Math.max(0, inputBox.y - 30),
      width: Math.min(900, inputBox.width + 400),
      height: 500,
    },
  });
  console.log("zoom → /tmp/zoom-search.png");
}

// Full DOM of the search container and its siblings
const fullSearchContainer = await page.evaluate(() => {
  const input = document.querySelector("#main-search");
  if (!input) return null;
  // Walk up to the form container
  let el = input.closest("[ref], section");
  if (!el) el = input.parentElement?.parentElement?.parentElement;
  return el?.outerHTML?.slice(0, 8000) ?? null;
});
console.log("\n=== FULL HERO SEARCH SECTION ===");
console.log(fullSearchContainer);

// List EVERY visible element below the input
const below = await page.evaluate(() => {
  const input = document.querySelector("#main-search");
  if (!input) return [];
  const inputRect = input.getBoundingClientRect();
  const all = Array.from(document.querySelectorAll("*"));
  return all
    .filter((el) => {
      const r = el.getBoundingClientRect();
      return r.top > inputRect.bottom && r.top < inputRect.bottom + 200 && r.width > 50 && r.height > 5;
    })
    .map((el) => ({
      tag: el.tagName,
      id: el.id || null,
      cls: el.className?.toString().slice(0, 120),
      text: el.textContent?.slice(0, 80),
      rect: { top: el.getBoundingClientRect().top, left: el.getBoundingClientRect().left, w: el.getBoundingClientRect().width, h: el.getBoundingClientRect().height },
    }))
    .slice(0, 30);
});
console.log("\n=== visible elements within 200px below the input ===");
console.log(JSON.stringify(below, null, 2));

await browser.close();
