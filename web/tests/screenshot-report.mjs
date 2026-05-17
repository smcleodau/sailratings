// Capture the report-page payoff:
//   1) compiling beat (PinnedMasthead with "File compiled. Opening…" subline)
//   2) table-first (recommendations table visible, prose not yet started)
//   3) prose backfilling (prose visible, table still above)
// Usage: node tests/screenshot-report.mjs <full-report-url>
//   e.g. node tests/screenshot-report.mjs https://dev.sailratings.com/report/abc123

import { chromium } from 'playwright';

const url = process.argv[2];
if (!url) {
  console.error('Usage: node tests/screenshot-report.mjs <full-report-url>');
  process.exit(1);
}

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

await page.goto(url, { waitUntil: 'networkidle', timeout: 30000 });

// Beat 1: compiling moment (should land within 600ms of load)
await page.waitForTimeout(300);
await page.screenshot({ path: '/tmp/report-compiling.png', fullPage: false });

// Beat 2: table-first (around 900–1400ms after load — table mounted, prose not yet)
await page.waitForTimeout(700);
await page.screenshot({ path: '/tmp/report-table.png', fullPage: false });

// Beat 3: prose backfilling (around 2.5s — prose has started, table still on screen)
await page.waitForTimeout(1500);
await page.screenshot({ path: '/tmp/report-prose.png', fullPage: false });

console.log('OK', url);
console.log('  /tmp/report-compiling.png');
console.log('  /tmp/report-table.png');
console.log('  /tmp/report-prose.png');
await browser.close();
