const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto('https://admin.sailratings.com/admin/swarm', { waitUntil: 'networkidle' });
  await page.screenshot({ path: 'screenshot.png' });
  await browser.close();
})();
