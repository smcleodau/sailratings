/* AUTH-01-02 evidence capture — renders /sign-in and /sign-up on the
   E2E (Clerk-disabled) server and screenshots the auth frame. */
import { chromium } from '@playwright/test';

const BASE = 'http://localhost:4201';
const shots = [
  ['/sign-in', 'auth-01-02-sign-in.png'],
  ['/sign-up', 'auth-01-02-sign-up.png'],
];

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
for (const [route, file] of shots) {
  const resp = await page.goto(`${BASE}${route}`, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(600);
  await page.screenshot({ path: file, fullPage: false });
  console.log(`${route} -> ${file} (HTTP ${resp.status()})`);
}
await browser.close();
