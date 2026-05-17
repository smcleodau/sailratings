// Capture the bench at three beats:
//   1) streaming in progress (working log mid-flight, prose just starting)
//   2) sealed sections visible (post-stream, scrolled to show §2–§8)
//   3) sticky CTA rail engaged (scrolled to bottom)
// Usage: node tests/screenshot-bench.mjs [base-url]
//        defaults to https://dev.sailratings.com/

import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'https://dev.sailratings.com/';
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(baseUrl, { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(600);

// Search and pick the first result
await page.fill('#main-search', 'sun fish');
await page.waitForTimeout(900);
// First dropdown result — <ul id="search-results"><li onClick=…> in Hero.tsx.
const firstResult = page.locator('#search-results li').first();
await firstResult.click();

// Wait for the bench panel to mount (PinnedMasthead is sticky top-0)
await page.waitForSelector('#bench', { timeout: 15000 });
// Wait until the prose column has at least 100 chars of streamed text before screenshotting.
await page.waitForFunction(
  () => {
    const proseEl = document.querySelector('#bench .body-text');
    return proseEl && (proseEl.textContent || '').trim().length > 100;
  },
  { timeout: 60000 },
);
await page.waitForTimeout(400); // settle one more chunk

// Beat 1: streaming in progress, prose visible
await page.screenshot({ path: '/tmp/bench-streaming.png', fullPage: false });

// Wait for streaming to finish + sealed sections to appear
await page.waitForFunction(
  () => document.querySelector('[aria-label="Sealed sections"]') !== null,
  { timeout: 60000 },
);
await page.waitForTimeout(800);

// Beat 2: sealed sections visible (in view)
await page.evaluate(() => {
  const el = document.querySelector('[aria-label="Sealed sections"]');
  if (el) el.scrollIntoView({ behavior: 'instant', block: 'start' });
});
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/bench-sealed.png', fullPage: false });

// Beat 3: scrolled to bottom, sticky CTA rail engaged
await page.evaluate(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'instant' }));
await page.waitForTimeout(400);
await page.screenshot({ path: '/tmp/bench-cta.png', fullPage: false });

console.log('OK', baseUrl);
console.log('  /tmp/bench-streaming.png');
console.log('  /tmp/bench-sealed.png');
console.log('  /tmp/bench-cta.png');
await browser.close();
