import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "http://localhost:4200";
const ADMIN_PW = process.env.ADMIN_PASSWORD || "sailfast2026";

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

// plant token
await page.goto(`${BASE}/justin`, { waitUntil: "networkidle" });
await page.evaluate((pw) => localStorage.setItem("admin_token", pw), ADMIN_PW);

const routes = [
  { label: "chat", url: `/justin` },
  { label: "tables-index", url: `/justin/tables` },
  { label: "tables-boats", url: `/justin/tables/boats` },
  { label: "corrections", url: `/justin/corrections` },
  { label: "audit", url: `/justin/tables/admin_edits` },
];

for (const r of routes) {
  await page.goto(`${BASE}${r.url}?cb=${Date.now()}`, { waitUntil: "networkidle", timeout: 20000 });
  await page.waitForTimeout(800);
  await page.screenshot({ path: `/tmp/nav-${r.label}.png`, fullPage: false });
  console.log(`${r.label} -> /tmp/nav-${r.label}.png`);
}

await browser.close();
