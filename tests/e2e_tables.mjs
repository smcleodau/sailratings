/**
 * /justin/tables editor — real Playwright flow:
 *   - sign in via /justin (drops admin_token into localStorage)
 *   - open /justin/tables, assert ≥6 tables listed and the right ones are locked
 *   - click into `boats`, assert columns + rows + pagination
 *   - edit a single cell on a throwaway boat — assert PATCH succeeds, optimistic
 *     update lands in the DOM, and the audit row hits admin_edits
 *   - navigate to a read-only table (race_results), assert click-to-edit is
 *     disabled (no input opens)
 */
import { chromium } from "playwright";

const BASE = process.env.TEST_BASE_URL || "http://localhost:4200";
const API_BASE = process.env.API_BASE_URL || "http://localhost:4100/v1";
const ADMIN_PW = process.env.ADMIN_PASSWORD || "sailfast2026";

async function signIn(page) {
  // Plant the admin_token directly so we skip the login form
  await page.goto(`${BASE}/justin`, { waitUntil: "networkidle" });
  await page.evaluate((pw) => localStorage.setItem("admin_token", pw), ADMIN_PW);
}

async function run() {
  const browser = await chromium.launch({ headless: true });
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  try {
    await signIn(page);

    // /justin/tables index
    await page.goto(`${BASE}/justin/tables?cb=${Date.now()}`, { waitUntil: "networkidle" });
    await page.locator("h1", { hasText: /Tables/i }).waitFor({ state: "visible", timeout: 8000 });

    // Expect at least 6 visible tables
    const tableCount = await page.locator("a[href^='/justin/tables/']").count();
    if (tableCount < 6) throw new Error(`expected ≥6 tables in index, got ${tableCount}`);
    console.log(`  ✓ tables index lists ${tableCount} tables`);

    // race_results should be marked locked
    const raceRow = page.locator("a[href='/justin/tables/race_results']");
    await raceRow.waitFor({ state: "visible", timeout: 4000 });
    const raceText = await raceRow.innerText();
    // It should show a lock icon (Lucide renders as svg) — we just check no "edit" word
    if (/\bedit\b/i.test(raceText)) throw new Error("race_results should be read-only");
    console.log("  ✓ race_results marked read-only");

    // boats should be editable
    const boatsRow = page.locator("a[href='/justin/tables/boats']");
    const boatsText = await boatsRow.innerText();
    if (!/\bedit\b/i.test(boatsText)) throw new Error("boats should be editable");
    console.log("  ✓ boats marked editable");

    // Open boats
    await boatsRow.click();
    await page.waitForURL(/\/justin\/tables\/boats/, { timeout: 5000 });
    await page.locator("table thead").waitFor({ state: "visible", timeout: 8000 });
    const headerCount = await page.locator("table thead th").count();
    if (headerCount < 5) throw new Error(`boats table: expected ≥5 column headers, got ${headerCount}`);
    const rowCount = await page.locator("table tbody tr").count();
    if (rowCount < 5) throw new Error(`boats table: expected ≥5 data rows, got ${rowCount}`);
    console.log(`  ✓ boats table: ${headerCount} columns, ${rowCount} rows`);

    // Edit a cell — find a boat with a known designer field we can modify
    // We'll pick the FIRST row's `boat_name` field, save the original, set a test
    // value, then revert.
    const firstNameCell = page.locator("table tbody tr").first().locator("td").nth(1);
    const original = (await firstNameCell.innerText()).trim();
    if (!original) throw new Error("first boat_name cell is empty");
    const probe = `${original} [pw-test ${Date.now()}]`;

    await firstNameCell.click();
    await page.locator("table tbody tr").first().locator("td input").fill(probe);
    await page.keyboard.press("Enter");
    // Wait for input to disappear (= success)
    await page.locator("table tbody tr").first().locator("td input").waitFor({ state: "detached", timeout: 6000 });
    const newText = (await firstNameCell.innerText()).trim();
    if (newText !== probe)
      throw new Error(`after edit, cell shows '${newText}' (expected '${probe}')`);
    console.log(`  ✓ edited cell: '${original}' → '${probe}'`);

    // Audit table should have a row for this edit
    const res = await fetch(`${API_BASE}/admin/tables/admin_edits?order_by=id&order_dir=desc&limit=1`, {
      headers: { Authorization: `Bearer ${ADMIN_PW}` },
    });
    if (!res.ok) throw new Error(`admin_edits fetch failed: ${res.status}`);
    const audit = await res.json();
    const last = audit.rows?.[0];
    if (!last) throw new Error("audit log empty after edit");
    if (last.table_name !== "boats" || last.column_name !== "boat_name" || last.new_value !== probe)
      throw new Error(`audit row mismatch: ${JSON.stringify(last)}`);
    console.log(`  ✓ admin_edits logged the change (id ${last.id})`);

    // Revert via API so we don't pollute dev data
    const pkCell = await page.locator("table tbody tr").first().locator("td").first().innerText();
    const revertRes = await fetch(
      `${API_BASE}/admin/tables/boats/${encodeURIComponent(pkCell.trim())}`,
      {
        method: "PATCH",
        headers: { Authorization: `Bearer ${ADMIN_PW}`, "Content-Type": "application/json" },
        body: JSON.stringify({ column: "boat_name", value: original }),
      },
    );
    if (!revertRes.ok) throw new Error("revert PATCH failed");
    console.log("  ✓ reverted to original");

    // Navigate to read-only race_results
    await page.goto(`${BASE}/justin/tables/race_results?cb=${Date.now()}`, { waitUntil: "networkidle" });
    await page.locator("table thead").waitFor({ state: "visible", timeout: 10000 });
    await page.getByText(/read-only/i).first().waitFor({ state: "visible", timeout: 4000 });
    // Click a cell — input should NOT open
    await page.locator("table tbody tr").first().locator("td").nth(2).click();
    const inputCount = await page.locator("table tbody td input").count();
    if (inputCount !== 0)
      throw new Error("read-only table opened an input on click");
    console.log("  ✓ race_results is read-only, click-to-edit suppressed");

    console.log("\nALL GREEN");
  } finally {
    await browser.close();
  }
}

await run().catch((e) => {
  console.error("FAIL:", e.message);
  process.exit(1);
});
