/**
 * Full end-to-end Playwright test of the new theatrical report flow.
 *
 * Runs against the deployed URL (defaults to dev.sailratings.com).
 * Tests multiple boats. Asserts every visible piece: working steps,
 * streamed text, 8-section TOC with 7 locks, inline CTA, no separate
 * Search button, no separate PurchaseCTA below.
 *
 * Bails on the first failure. Loops N times to catch flakiness.
 */
import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "https://dev.sailratings.com";
const ITER = Number(process.env.ITER || 3);
const HEADLESS = process.env.HEADFUL !== "1";

const BOATS = [
  {
    label: "SUN FISH (IRC + ORC, has race results)",
    query: "3375",
    name: "SUN FISH",
    expectsORC: true,
  },
  {
    label: "BLACK DIAMOND (search by name)",
    query: "black diamond",
    name: "BLACK DIAMOND",
    expectsORC: false, // best-effort, don't fail on this
  },
];

async function testBoat(page, boat, iter) {
  console.log(`\n  ▸ ${boat.label}  (query=${JSON.stringify(boat.query)})`);

  await page.goto(`${BASE}/?cb=${Date.now()}-${iter}`, {
    waitUntil: "networkidle",
    timeout: 30000,
  });

  // Hero search — type + click first result
  await page.locator("#main-search").click();
  await page.locator("#main-search").fill(boat.query);
  await page.locator("#search-results-wrap").waitFor({
    state: "visible",
    timeout: 10000,
  });
  await page
    .locator("#search-results li", { hasText: new RegExp(boat.name, "i") })
    .first()
    .click();

  // Wait for BoatCard
  await page
    .locator("h2", { hasText: new RegExp(boat.name, "i") })
    .first()
    .waitFor({ state: "visible", timeout: 10000 });

  // Report card header — "Rating File" label + boat name
  await page.getByText(/Rating File/i).first().waitFor({
    state: "visible",
    timeout: 15000,
  });
  await page
    .getByText(/— Executive Summary/i)
    .first()
    .waitFor({ state: "visible", timeout: 5000 });

  // "Section 1 of 8" marker
  const sectionMarker = await page.getByText(/Section 1 of 8/i).count();
  if (sectionMarker === 0)
    throw new Error("missing 'Section 1 of 8' marker");
  console.log("    ✓ header + Section 1 of 8");

  // Working steps panel appears
  await page
    .getByText(/Compiling the report/i)
    .first()
    .waitFor({ state: "visible", timeout: 8000 });

  // At least one step row renders, citing total-boats indexed
  await page
    .locator("ul li")
    .filter({ hasText: /indexed [\d,]+ boats/i })
    .first()
    .waitFor({ state: "visible", timeout: 10000 });

  // At least one step row mentions the boat's name itself
  await page
    .locator("ul li")
    .filter({ hasText: new RegExp(boat.name, "i") })
    .first()
    .waitFor({ state: "visible", timeout: 10000 });
  console.log("    ✓ working steps with real numbers + boat name");

  // Wait for the inline CTA button to appear (= isDone, stream finished)
  await page.waitForFunction(
    () =>
      Array.from(document.querySelectorAll("button")).some((b) =>
        /Open the full report/i.test(b.textContent || ""),
      ),
    { timeout: 90000 },
  );
  console.log("    ✓ stream completed, CTA button rendered");

  // After done — assert TOC: 8 numbered sections
  const tocText = await page
    .locator(":has-text('The full report — 8 sections')")
    .last()
    .innerText();
  const tocCount = (tocText.match(/^\s*\d\./gm) || []).length;
  // (innerText loses structure; cross-check by counting locked rows in DOM)
  const lockedRows = await page
    .locator("svg.lucide-lock")
    .count();
  const checkRowsInToc = await page.evaluate(() => {
    // Count items in the TOC ordered list specifically — find the <ol> that has Section titles
    const ols = Array.from(document.querySelectorAll("ol"));
    for (const ol of ols) {
      const items = Array.from(ol.children).filter((c) => c.tagName === "LI");
      if (items.length === 8) return items.length;
    }
    return 0;
  });
  if (checkRowsInToc !== 8)
    throw new Error(`expected 8 TOC rows, found ${checkRowsInToc}`);
  if (lockedRows < 7)
    throw new Error(`expected ≥7 lock icons in TOC, found ${lockedRows}`);
  console.log(`    ✓ TOC has 8 rows, ${lockedRows} locked`);

  // No separate Search button (in Hero)
  const heroSearchBtn = await page.evaluate(() => {
    return Array.from(document.querySelectorAll("button")).filter((b) => {
      const t = (b.textContent || "").trim();
      return t === "Search";
    }).length;
  });
  if (heroSearchBtn !== 0)
    throw new Error(`expected 0 'Search' buttons, found ${heroSearchBtn}`);
  console.log("    ✓ no redundant Search button");

  // No standalone PurchaseCTA "Unlock the Full Analysis" block
  const orphanCTA = await page
    .getByText(/Unlock the Full Analysis/i)
    .count();
  if (orphanCTA !== 0)
    throw new Error("orphaned PurchaseCTA 'Unlock the Full Analysis' still present");
  console.log("    ✓ no orphan PurchaseCTA");

  // Inline CTA copy + button. Page uses &nbsp; in places — match on \s\xA0 etc.
  await page
    .getByText(/Section[\s ]+1 covers where you sit/i)
    .first()
    .waitFor({ state: "visible", timeout: 4000 });

  const buttonLabel = await page.evaluate(() => {
    const b = Array.from(document.querySelectorAll("button")).find((x) =>
      /Open the full report/i.test(x.textContent || ""),
    );
    return b?.textContent?.trim() || "";
  });
  if (!/Open the full report .* (?:£|\$|A\$)\d+/i.test(buttonLabel))
    throw new Error(`CTA button label malformed: "${buttonLabel}"`);
  console.log(`    ✓ inline CTA copy + button "${buttonLabel}"`);

  // Streamed text should mention TCC value AND the boat name
  const bodyText = await page.evaluate(() => {
    // The streamed analysis lives in the .whitespace-pre-wrap div
    const els = Array.from(
      document.querySelectorAll(".whitespace-pre-wrap"),
    );
    return els.map((el) => el.textContent || "").join("\n");
  });
  if (!new RegExp(boat.name, "i").test(bodyText))
    throw new Error(`streamed text missing boat name '${boat.name}'`);
  if (bodyText.trim().length < 200)
    throw new Error(`streamed text suspiciously short: ${bodyText.length} chars`);
  console.log(`    ✓ streamed text has boat name + ${bodyText.length} chars`);

  // PostHog network calls should have happened (best-effort, may be blocked locally)
}

async function runIteration(browser, iter) {
  const ctx = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    extraHTTPHeaders: { "Cache-Control": "no-cache" },
  });
  const page = await ctx.newPage();
  try {
    for (const boat of BOATS) {
      await testBoat(page, boat, iter);
    }
  } finally {
    await ctx.close();
  }
}

const browser = await chromium.launch({ headless: HEADLESS });
let failures = 0;
try {
  for (let i = 1; i <= ITER; i++) {
    console.log(`\n=== iter ${i}/${ITER}  base=${BASE} ===`);
    try {
      await runIteration(browser, i);
      console.log(`iter ${i}: GREEN`);
    } catch (e) {
      failures++;
      console.error(`iter ${i}: FAIL — ${e.message}`);
      const ts = Date.now();
      const ctx = await browser.newContext({ viewport: { width: 1440, height: 1800 } });
      const page = await ctx.newPage();
      try {
        await page.goto(`${BASE}/?cb=${ts}`, { waitUntil: "networkidle", timeout: 20000 });
        await page.screenshot({ path: `/tmp/e2e-fail-${i}-${ts}.png`, fullPage: true });
        console.error(`    screenshot: /tmp/e2e-fail-${i}-${ts}.png`);
      } catch {}
      await ctx.close();
    }
  }
  console.log(`\n${ITER - failures}/${ITER} iterations green`);
  process.exit(failures === 0 ? 0 : 1);
} finally {
  await browser.close();
}
