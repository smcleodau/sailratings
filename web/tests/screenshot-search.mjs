// Search bar: empty, typed, dropdown — plus the jump-free transition to the bench.
// Usage: node tests/screenshot-search.mjs [base-url]

import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'https://dev.sailratings.com/';
const browser = await chromium.launch();
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(baseUrl, { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(800);

// Empty state
await page.screenshot({ path: '/tmp/search-empty.png', clip: { x: 360, y: 350, width: 720, height: 120 } });

// Typed state
await page.fill('#main-search', 'sun fish');
await page.waitForTimeout(700);
await page.screenshot({ path: '/tmp/search-typed.png', clip: { x: 360, y: 350, width: 720, height: 120 } });

// Dropdown
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/search-dropdown.png', clip: { x: 360, y: 350, width: 720, height: 480 } });

// Jump-free transition: capture the viewport in the 250ms after click to confirm no scroll jolt.
const firstResult = page.locator('[role="option"], [data-result], li button').first();
const scrollBefore = await page.evaluate(() => window.scrollY);
await firstResult.click();
await page.waitForTimeout(250);
const scrollAfter = await page.evaluate(() => window.scrollY);
await page.screenshot({ path: '/tmp/search-after-click.png', fullPage: false });

console.log('OK', baseUrl);
console.log('  /tmp/search-empty.png');
console.log('  /tmp/search-typed.png');
console.log('  /tmp/search-dropdown.png');
console.log('  /tmp/search-after-click.png');
console.log(`  scrollY before=${scrollBefore} after=${scrollAfter} (delta=${scrollAfter - scrollBefore})`);
if (scrollAfter - scrollBefore > 4) {
  console.error('FAIL: scroll jolt detected');
  process.exit(2);
}
await browser.close();
