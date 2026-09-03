/* AD-01-06 visual check — capture /admin/scrapers against the seeded
 * ledger fixture for comparison with the Admin design (inverse Paper /
 * Dusk palette, design 2a).
 *
 * Requires the fixture API on :4102 and the dev server on :4203 (see
 * playwright.scrapers.config.ts for how both are booted).
 *
 *   node capture-scrapers-screenshot.js [out.png]
 */
const { chromium } = require('@playwright/test');

(async () => {
  const out = process.argv[2] || '/tmp/ad-01-06-scrapers.png';
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
  await page.addInitScript(() => {
    window.localStorage.setItem('admin_token', 'sailfast2026');
  });
  await page.goto('http://localhost:4203/admin/scrapers', { waitUntil: 'domcontentloaded' });
  await page.getByTestId('source-row-sailsys').waitFor({ state: 'visible' });
  // Open the sailsys recent-runs drawer so the screenshot shows the
  // expandable table as well as the summary rows.
  await page.getByTestId('source-row-header-sailsys').click();
  await page.getByTestId('recent-runs-sailsys').waitFor({ state: 'visible' });
  await page.waitForTimeout(400);
  await page.screenshot({ path: out, fullPage: true });
  await browser.close();
  console.log(`screenshot saved to ${out}`);
})();
