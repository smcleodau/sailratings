import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "http://localhost:4200";

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const page = await ctx.newPage();

// Capture network
const calls = [];
page.on("response", async (res) => {
  const url = res.url();
  if (url.includes("/v1/search")) {
    try {
      const body = await res.json();
      calls.push({ url, status: res.status(), body });
    } catch {}
  }
});

await page.goto(`${BASE}/`, { waitUntil: "networkidle", timeout: 30000 });

const input = page.locator("#main-search");
await input.click();
await input.fill("3775");
await page.waitForTimeout(2000);

console.log("=== /v1/search calls ===");
for (const c of calls) {
  console.log("  ", c.url);
  console.log("    total:", c.body.total, "results:", c.body.results?.length, "suggestions:", c.body.suggestions?.length);
}

console.log("\n=== #search-results outerHTML ===");
const results = page.locator("#search-results");
const exists = (await results.count()) > 0;
if (exists) {
  console.log(await results.evaluate((el) => el.outerHTML.slice(0, 4000)));
} else {
  console.log("  no #search-results element");
}

console.log("\n=== entire visible #search-empty-state ===");
const empty = page.locator("#search-empty-state");
console.log("  count:", await empty.count(), "visible:", await empty.isVisible().catch(() => false));
if ((await empty.count()) > 0) console.log("  text:", await empty.innerText());

await browser.close();
