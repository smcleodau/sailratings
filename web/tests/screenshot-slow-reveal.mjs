// Slow-reveal capture: records the bench at seven moments after boat selection.
// Confirms the experience streams continuously rather than clumping.
//
//   T+1s   page settled after smooth-scroll, Hero compressed up
//   T+3s   prose streaming, first working steps complete
//   T+6s   first sealed card has materialised (5s threshold + 1s settle)
//   T+9s   third sealed card around now
//   T+13s  fifth/sixth sealed card
//   T+17s  all seven sealed cards out
//   T+20s  CTA rail in view at the bottom
//
// Usage: node tests/screenshot-slow-reveal.mjs [base-url]
//        defaults to https://dev.sailratings.com/

import { chromium } from 'playwright';

const baseUrl = process.argv[2] || 'https://dev.sailratings.com/';
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(baseUrl, { waitUntil: 'networkidle', timeout: 30000 });
await page.waitForTimeout(600);

await page.fill('#main-search', 'sun fish');
await page.waitForTimeout(900);
await page.locator('#search-results li').first().click();

const beats = [
  { name: 'T01s', delayMs: 1000 },
  { name: 'T03s', delayMs: 3000 },
  { name: 'T06s', delayMs: 6000 },
  { name: 'T09s', delayMs: 9000 },
  { name: 'T13s', delayMs: 13000 },
  { name: 'T17s', delayMs: 17000 },
  { name: 'T20s', delayMs: 20000 },
];

const clickedAt = Date.now();
for (const beat of beats) {
  const target = clickedAt + beat.delayMs;
  const wait = Math.max(0, target - Date.now());
  if (wait > 0) await page.waitForTimeout(wait);
  // Capture viewport (what the user sees at their current scroll position) AND a
  // full-page version (everything top to bottom) — sealed cards live below the
  // initial scroll target, so fullPage is the only way to see them all in one shot.
  await page.screenshot({ path: `/tmp/slow-reveal-${beat.name}-viewport.png`, fullPage: false });
  await page.screenshot({ path: `/tmp/slow-reveal-${beat.name}-full.png`, fullPage: true });
  const stats = await page.evaluate(() => ({
    cards: document.querySelectorAll('[aria-label="Sealed sections"] li').length,
    docHeight: document.documentElement.scrollHeight,
  }));
  console.log(`  ${beat.name}  cards=${stats.cards}  doc=${stats.docHeight}px`);
}

await browser.close();
console.log('OK', baseUrl);
