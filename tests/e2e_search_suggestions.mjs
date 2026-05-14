/**
 * Real-browser test for SearchBar suggestions + empty state on dev.sailratings.com.
 * Runs N iterations; bails on first failure.
 */
import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "https://dev.sailratings.com";
const ITERATIONS = Number(process.env.ITER || 5);

const SCENARIOS = [
  {
    name: "exact match: 3375 → SUN FISH visible",
    query: "3375",
    expect: async (page) => {
      const list = page.locator("#search-results");
      await list.waitFor({ state: "visible", timeout: 8000 });
      // Wait for SUN FISH specifically to be in the dropdown (not previous query state)
      await page.locator("#search-results li", { hasText: "SUN FISH" }).first().waitFor({ state: "visible", timeout: 8000 });
      await page.locator("#search-results li", { hasText: "3375" }).first().waitFor({ state: "visible", timeout: 4000 });
    },
  },
  {
    name: "near miss: 3775 → Did you mean… with SUN FISH 3375",
    query: "3775",
    expect: async (page) => {
      // Wait for the API call to land + DOM to settle
      const list = page.locator("#search-results");
      await list.waitFor({ state: "visible", timeout: 8000 });
      await page.locator("#search-results li", { hasText: /Did you mean/i }).waitFor({ state: "visible", timeout: 8000 });
      await page.locator("#search-results li", { hasText: "SUN FISH" }).waitFor({ state: "visible", timeout: 8000 });
      await page.locator("#search-results li", { hasText: "3375" }).waitFor({ state: "visible", timeout: 4000 });
    },
  },
  {
    name: "no results: abcxyz → 'No boats matching' empty state",
    query: "abcxyz",
    expect: async (page) => {
      const empty = page.locator("#search-empty-state");
      await empty.waitFor({ state: "visible", timeout: 8000 });
      const txt = await empty.innerText();
      if (!/No boats matching/i.test(txt)) throw new Error(`empty-state text wrong: ${txt}`);
      if (!/abcxyz/i.test(txt)) throw new Error(`empty-state missing query echo: ${txt}`);
    },
  },
];

async function runOnce(browser, iter) {
  // Fresh isolated context so cookies/cache don't leak between iterations.
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    extraHTTPHeaders: {
      // Defeat any aggressive CDN HTML caching during testing
      "Cache-Control": "no-cache",
      Pragma: "no-cache",
    },
  });
  const page = await ctx.newPage();

  // Cache-bust the HTML on first load
  const cacheBuster = Date.now();
  await page.goto(`${BASE}/?cb=${cacheBuster}`, { waitUntil: "networkidle", timeout: 30000 });

  // Sanity: bundle has new code
  const bundleHas = await page.evaluate(() => {
    return Array.from(document.scripts).some((s) => s.src.includes("/_next/static/"));
  });
  if (!bundleHas) throw new Error("No Next.js static chunks on page");

  for (const sc of SCENARIOS) {
    const input = page.locator("#main-search");
    await input.click();
    await input.fill("");
    await input.fill(sc.query);
    try {
      await sc.expect(page);
      console.log(`  ✓ ${sc.name}`);
    } catch (e) {
      // Capture screenshot for debugging
      const screenshotPath = `/tmp/playwright-fail-${iter}-${sc.query}.png`;
      await page.screenshot({ path: screenshotPath, fullPage: true });
      throw new Error(`✗ ${sc.name}\n    ${e.message}\n    screenshot: ${screenshotPath}`);
    }
  }

  await ctx.close();
}

const browser = await chromium.launch({ headless: true });
try {
  let failures = 0;
  for (let i = 1; i <= ITERATIONS; i++) {
    console.log(`\n--- iteration ${i} / ${ITERATIONS} (base=${BASE}) ---`);
    try {
      await runOnce(browser, i);
      console.log(`iteration ${i}: GREEN`);
    } catch (e) {
      failures++;
      console.error(`iteration ${i}: FAIL — ${e.message}`);
    }
  }
  console.log(`\n${ITERATIONS - failures} / ${ITERATIONS} green`);
  process.exit(failures === 0 ? 0 : 1);
} finally {
  await browser.close();
}
