/* PAY-01-10 screenshot capture — runs against the E2E rig (fixture API on
 * PW_API_PORT, web dev server on PW_WEB_PORT) and refreshes
 * docs/screens/{users-plans,orders,billing}.png.
 *
 * Usage (servers must already be running):
 *   PW_BASE_URL=http://localhost:4201 node capture-admin-screenshots.js
 */
const { chromium } = require('@playwright/test');

const BASE = process.env.PW_BASE_URL || 'http://localhost:4201';
const OUT = process.env.SCREEN_DIR || '../docs/screens';
const ADMIN_PW = process.env.ADMIN_PASSWORD || 'sailfast2026';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

  // Plant the admin token before app JS runs (same flow the login form completes).
  await page.addInitScript((pw) => localStorage.setItem('admin_token', pw), ADMIN_PW);

  const shots = [
    ['/admin/users', 'users-plans.png'],
    ['/admin/orders', 'orders.png'],
    ['/admin/billing', 'billing.png'],
  ];
  for (const [path, file] of shots) {
    await page.goto(BASE + path, { waitUntil: 'networkidle' });
    await page.waitForTimeout(800); // let client-side fetches settle
    await page.screenshot({ path: `${OUT}/${file}`, fullPage: false });
    console.log('captured', `${OUT}/${file}`);
  }
  await browser.close();
})();
