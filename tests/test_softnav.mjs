import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "http://localhost:4200";
const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await ctx.newPage();

page.on("pageerror", (e) => console.error("PAGE ERROR:", e.message));
page.on("console", (m) => {
  if (m.type() === "error") console.error("CONSOLE ERROR:", m.text());
});

await page.goto(`${BASE}/justin`, { waitUntil: "domcontentloaded" });
await page.evaluate(() => localStorage.setItem("admin_token", "sailfast2026"));
await page.goto(`${BASE}/justin?cb=${Date.now()}`, { waitUntil: "domcontentloaded" });

const header = page.locator("header").first();
await header.waitFor({ state: "visible", timeout: 8000 });

await page.evaluate(() => {
  const h = document.querySelector("header");
  if (h) h.setAttribute("data-persist-tag", "TAG_" + Date.now());
});
const before = await page.evaluate(
  () => document.querySelector("header")?.getAttribute("data-persist-tag"),
);
console.log("tag before:", before);

async function softNav(label, href) {
  const link = page.locator(`header a[href="${href}"]`).first();
  await link.waitFor({ state: "visible", timeout: 5000 });
  console.log(`  pre-click url=${page.url()}  matches=${await page.locator(`header a[href="${href}"]`).count()}`);
  await page.screenshot({ path: `/tmp/softnav-pre-${label.replace(/[^a-z0-9]/gi, "_")}.png` });
  const linkInfo = await link.evaluate((el) => ({
    href: el.getAttribute("href"),
    text: el.textContent,
    onclick: !!el.onclick,
    inHeader: !!el.closest("header"),
    box: el.getBoundingClientRect(),
  }));
  console.log(`  link:`, JSON.stringify(linkInfo));
  const navPromise = page.waitForFunction(
    (target) => location.pathname === target,
    href,
    { timeout: 10000 },
  );
  await link.click({ force: true });
  try {
    await navPromise;
  } catch (e) {
    console.error(`  post-click url=${page.url()}  trying JS click`);
    await page.evaluate((h) => {
      document
        .querySelectorAll(`header a[href="${h}"]`)
        .forEach((a) => a.click());
    }, href);
    await page.waitForFunction((t) => location.pathname === t, href, {
      timeout: 5000,
    });
  }
  // small settle for React commit
  await page.waitForTimeout(150);

  const tag = await page.evaluate(
    () => document.querySelector("header")?.getAttribute("data-persist-tag"),
  );
  const headerCount = await page.locator("header").count();
  const url = page.url();
  console.log(`${label}: url=${url}  tag=${tag}  persisted=${tag === before}  headers=${headerCount}`);
  return tag === before;
}

const r1 = await softNav("→/justin/corrections", "/justin/corrections");
const r2 = await softNav("→/justin/tables", "/justin/tables");
const r3 = await softNav("→/justin/tables/admin_edits", "/justin/tables/admin_edits");
const r4 = await softNav("→/justin", "/justin");

console.log("\nALL PERSISTED:", r1 && r2 && r3 && r4);

await page.screenshot({ path: "/tmp/softnav-final.png", fullPage: false });
console.log("screenshot: /tmp/softnav-final.png");

await browser.close();
process.exit(r1 && r2 && r3 && r4 ? 0 : 1);
