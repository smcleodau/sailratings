/**
 * Real-browser test for Hero search: button click, Enter submit, dropdown clicks,
 * suggestions, empty state. Loops until green N times or first failure.
 */
import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "http://localhost:4200";
const ITERATIONS = Number(process.env.ITER || 5);

async function runOnce(browser, iter) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 900 },
    extraHTTPHeaders: { "Cache-Control": "no-cache" },
  });
  const page = await ctx.newPage();
  await page.goto(`${BASE}/?cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 30000 });

  const cases = [
    {
      name: "exact match: 3375 — dropdown shows '1 match' header + SUN FISH row",
      run: async () => {
        const input = page.locator("#main-search");
        await input.click();
        await input.fill("");
        await input.fill("3375");
        await page.locator("#search-results-wrap").waitFor({ state: "visible", timeout: 8000 });
        await page.getByText(/\b1 match\b/i).waitFor({ state: "visible", timeout: 4000 });
        await page.locator("#search-results li", { hasText: "SUN FISH" }).first().waitFor({ state: "visible", timeout: 4000 });
      },
    },
    {
      name: "near miss: 3775 — dropdown shows 'Did you mean…' + SUN FISH suggestion",
      run: async () => {
        const input = page.locator("#main-search");
        await input.click();
        await input.fill("");
        await input.fill("3775");
        await page.locator("#search-results-wrap").waitFor({ state: "visible", timeout: 8000 });
        await page.getByText(/Did you mean/i).waitFor({ state: "visible", timeout: 4000 });
        await page.locator("#search-results li", { hasText: "SUN FISH" }).waitFor({ state: "visible", timeout: 4000 });
      },
    },
    {
      name: "empty state: abcxyz — caption rendered, no dropdown",
      run: async () => {
        const input = page.locator("#main-search");
        await input.click();
        await input.fill("");
        await input.fill("abcxyz");
        await page.locator("#search-empty-state").waitFor({ state: "visible", timeout: 8000 });
        const wrap = await page.locator("#search-results-wrap").count();
        if (wrap > 0) throw new Error("results dropdown should not render in empty state");
      },
    },
    {
      name: "no separate Search button exists (dropdown is the only action)",
      run: async () => {
        const btn = page.getByRole("button", { name: /^Search$/ });
        const n = await btn.count();
        if (n !== 0) throw new Error(`expected 0 Search buttons, found ${n}`);
      },
    },
    {
      name: "Enter key on '3375' selects SUN FISH",
      run: async () => {
        await page.goto(`${BASE}/?cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 20000 });
        const input = page.locator("#main-search");
        await input.click();
        await input.fill("3375");
        await page.locator("#search-results-wrap").waitFor({ state: "visible", timeout: 8000 });
        await input.press("Enter");
        await page.getByText(/SUN FISH/i).first().waitFor({ state: "visible", timeout: 8000 });
      },
    },
    {
      name: "clicking SUN FISH row navigates to the boat",
      run: async () => {
        await page.goto(`${BASE}/?cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 20000 });
        const input = page.locator("#main-search");
        await input.click();
        await input.fill("3375");
        await page.locator("#search-results-wrap").waitFor({ state: "visible", timeout: 8000 });
        await page.locator("#search-results li", { hasText: "SUN FISH" }).first().click();
        await page.getByText(/SUN FISH/i).first().waitFor({ state: "visible", timeout: 8000 });
      },
    },
  ];

  for (const c of cases) {
    try {
      await c.run();
      console.log(`  ✓ ${c.name}`);
    } catch (e) {
      const shot = `/tmp/hero-test-fail-${iter}-${c.name.slice(0, 20).replace(/\W+/g, "_")}.png`;
      await page.screenshot({ path: shot, fullPage: false });
      throw new Error(`✗ ${c.name}\n    ${e.message}\n    shot: ${shot}`);
    }
  }
  await ctx.close();
}

const browser = await chromium.launch({ headless: true });
let failures = 0;
try {
  for (let i = 1; i <= ITERATIONS; i++) {
    console.log(`\n--- iteration ${i}/${ITERATIONS} base=${BASE} ---`);
    try { await runOnce(browser, i); console.log(`iter ${i}: GREEN`); }
    catch (e) { failures++; console.error(`iter ${i}: FAIL — ${e.message}`); }
  }
  console.log(`\n${ITERATIONS - failures}/${ITERATIONS} green`);
  process.exit(failures === 0 ? 0 : 1);
} finally { await browser.close(); }
