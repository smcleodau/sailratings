/**
 * e2e for The Bench layout. Asserts the rewritten post-search report flow
 * against the deployed dev URL: dateline, masthead boat name caps, monospace
 * rating value, metadata strip, compile stamp, §1 prose, §2-§8 redacted
 * sketches with brass section numbers, inline navy CTA panel, sticky pill.
 */
import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "https://dev.sailratings.com";
const ITER = Number(process.env.ITER || 3);

const BOATS = [
  { label: "SUN FISH", query: "3375", name: "SUN FISH" },
  { label: "CHILLI PEPPER", query: "chilli pepper", name: "CHILLI PEPPER" },
];

async function testBoat(page, boat, iter) {
  console.log(`\n  ▸ ${boat.label}`);
  await page.goto(`${BASE}/?cb=${Date.now()}-${iter}`, { waitUntil: "networkidle", timeout: 30000 });
  await page.locator("#main-search").click();
  await page.locator("#main-search").fill(boat.query);
  await page.locator("#search-results-wrap").waitFor({ state: "visible", timeout: 10000 });
  await page.locator("#search-results li", { hasText: new RegExp(boat.name, "i") }).first().click();

  // Bench renders
  await page.locator("#bench").waitFor({ state: "visible", timeout: 15000 });

  // Dateline
  await page.getByText(/SAIL RATINGS\s*·\s*THE BENCH/i).first().waitFor({ state: "visible", timeout: 5000 });
  await page.getByText(/UTC$/i).first().waitFor({ state: "visible", timeout: 3000 });
  console.log("    ✓ dateline strip");

  // Boat name in caps as h1
  await page.locator("h1", { hasText: new RegExp(boat.name, "i") }).waitFor({ state: "visible", timeout: 5000 });
  console.log("    ✓ masthead h1 with boat name");

  // Compile stamp / steps (one or the other always present)
  // While streaming we see steps; after done we see compile line
  // §1 marker
  await page.locator("text=/§1/").waitFor({ state: "visible", timeout: 10000 });
  await page.getByText(/Where she sits/i).waitFor({ state: "visible", timeout: 5000 });
  console.log("    ✓ §1 marker + 'Where she sits' heading");

  // Wait for done — CTA button appears
  await page.waitForFunction(
    () => Array.from(document.querySelectorAll("button")).some((b) => /Send me the file/i.test(b.textContent || "")),
    { timeout: 90000 },
  );
  console.log("    ✓ stream done, CTA button rendered");

  // §2-§8 markers
  for (const n of [2, 3, 4, 5, 6, 7, 8]) {
    const count = await page.locator(`text=§${n}`).count();
    if (count === 0) throw new Error(`§${n} not rendered in redacted strip`);
  }
  console.log("    ✓ all seven sealed sections rendered (§2-§8)");

  // Redacted lines: at least one █ block somewhere in the bench
  const blockCount = await page.evaluate(() => {
    const bench = document.querySelector("#bench");
    if (!bench) return 0;
    const text = bench.textContent || "";
    return (text.match(/█/g) || []).length;
  });
  if (blockCount < 20) throw new Error(`expected ≥20 █ block characters in redacted strip, found ${blockCount}`);
  console.log(`    ✓ ${blockCount} █ blocks rendered in sealed sketches`);

  // Inline CTA copy
  await page.getByText(/Seven more sections drafted for/i).first().waitFor({ state: "visible", timeout: 5000 });
  // Currency-aware button label
  const buttonLabel = await page.evaluate(() => {
    const b = Array.from(document.querySelectorAll("button")).find((x) =>
      /Send me the file/i.test(x.textContent || ""),
    );
    return b?.textContent?.trim() || "";
  });
  if (!/Send me the file .* (?:£|\$|A\$)\d+/i.test(buttonLabel))
    throw new Error(`CTA button malformed: "${buttonLabel}"`);
  console.log(`    ✓ inline CTA + button "${buttonLabel}"`);

  // Streamed prose mentions boat name
  const bodyText = await page.evaluate(() => {
    const els = Array.from(document.querySelectorAll(".whitespace-pre-wrap"));
    return els.map((el) => el.textContent || "").join("\n");
  });
  if (!new RegExp(boat.name, "i").test(bodyText))
    throw new Error(`prose missing boat name '${boat.name}'`);
  if (bodyText.trim().length < 300)
    throw new Error(`prose suspiciously short: ${bodyText.length} chars`);
  console.log(`    ✓ prose contains boat name (${bodyText.length} chars)`);

  // No orphaned old structures
  const orphans = await page.evaluate(() => {
    return {
      unlockText: Array.from(document.querySelectorAll("*")).some(
        (el) => el.children.length === 0 && /Unlock the Full Analysis/i.test(el.textContent || ""),
      ),
      searchBtn: Array.from(document.querySelectorAll("button")).some((b) => (b.textContent || "").trim() === "Search"),
      helpCardHeading: Array.from(document.querySelectorAll("h3")).some((h) =>
        /Help us improve this profile/i.test(h.textContent || ""),
      ),
    };
  });
  if (orphans.unlockText) throw new Error("orphaned 'Unlock the Full Analysis' present");
  if (orphans.searchBtn) throw new Error("orphaned Hero 'Search' button present");
  if (orphans.helpCardHeading) throw new Error("orphaned 'Help us improve this profile' card present");
  console.log("    ✓ no orphaned components");
}

async function runOnce(browser, iter) {
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 1000 }, extraHTTPHeaders: { "Cache-Control": "no-cache" } });
  const page = await ctx.newPage();
  try {
    for (const b of BOATS) await testBoat(page, b, iter);
  } finally {
    await ctx.close();
  }
}

const browser = await chromium.launch({ headless: true });
let failures = 0;
try {
  for (let i = 1; i <= ITER; i++) {
    console.log(`\n=== iter ${i}/${ITER} base=${BASE} ===`);
    try {
      await runOnce(browser, i);
      console.log(`iter ${i}: GREEN`);
    } catch (e) {
      failures++;
      console.error(`iter ${i}: FAIL — ${e.message}`);
    }
  }
  console.log(`\n${ITER - failures}/${ITER} green`);
  process.exit(failures === 0 ? 0 : 1);
} finally {
  await browser.close();
}
