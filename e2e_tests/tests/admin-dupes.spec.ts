import { test, expect, type Page } from '@playwright/test';

/**
 * AD-01-14 — Duplicate-boats queue decision flow.
 *
 * Runs against the self-contained stack in playwright.dupes.config.ts:
 * the admin_dupes router over a seeded SQLite fixture on :4102 and the
 * Next.js frontend on :4203.
 *
 * The fixture mirrors the acceptance scenario: FIFTH AVENUE|AUS holds
 * boats 17213 (551 race results) and 18390 (146) — merging leaves one
 * boat with 697 results; FOX BAT|GBR (size 3) exercises the not-dupe
 * footer; GREY GULL|NZL exercises skip.
 *
 * Each spec runs against a fresh queue: the seeded API restarts per
 * Playwright run, and the specs share it through a single worker, so each
 * test decides a *different* cluster.
 */

const ADMIN_PW = process.env.ADMIN_PASSWORD || 'sailfast2026';
const FIXTURE_API = process.env.PW_API_PORT
  ? `http://127.0.0.1:${process.env.PW_API_PORT}`
  : 'http://127.0.0.1:4102';

const FIFTH_AVENUE = 'FIFTH AVENUE|AUS';
const FOX_BAT = 'FOX BAT|GBR';
const GREY_GULL = 'GREY GULL|NZL';

// Decisions mutate the shared fixture DB; reset it before every spec so the
// tests are order-independent and re-runnable against a warm server.
test.beforeEach(async ({ page, request }) => {
  await request.post(`${FIXTURE_API}/v1/fixture/reset`);
  // Plant the admin token (same flow the login form completes)
  await page.goto('/admin');
  await page.evaluate((pw) => localStorage.setItem('admin_token', pw), ADMIN_PW);
});

async function openQueue(page: Page) {
  await page.goto('/admin/dupes');
  await expect(
    page.getByRole('heading', { name: /duplicate boats/i }),
  ).toBeVisible();
}

test('queue lists clusters with boats ordered by evidence and filter chips', async ({ page }) => {
  await openQueue(page);

  // All three fixture clusters visible as cards.
  await expect(page.getByTestId(`dupe-card-${FIFTH_AVENUE}`)).toBeVisible();
  await expect(page.getByTestId(`dupe-card-${FOX_BAT}`)).toBeVisible();
  await expect(page.getByTestId(`dupe-card-${GREY_GULL}`)).toBeVisible();

  // FIFTH AVENUE: two boat columns, 551-result boat first and highlighted
  // as the most-evidenced merge target with the single Signal merge button.
  const card = page.getByTestId(`dupe-card-${FIFTH_AVENUE}`);
  await expect(card.getByTestId('dupe-boat-17213')).toBeVisible();
  await expect(card.getByTestId('dupe-boat-18390')).toBeVisible();
  await expect(card.getByTestId('most-evidenced-17213')).toBeVisible();
  await expect(card.getByTestId('winner-radio-17213')).toBeChecked();
  await expect(card.getByTestId(`merge-button-${FIFTH_AVENUE}`)).toContainText(
    /merge 1 into this boat/i,
  );

  // Evidence figures on the card.
  await expect(card.getByTestId('dupe-boat-17213')).toContainText('551');
  await expect(card.getByTestId('dupe-boat-18390')).toContainText('146');

  // Filter chips from /meta: tiers B + D, sizes 2 + 3, countries.
  await expect(page.getByTestId('chip-tier-B')).toBeVisible();
  await expect(page.getByTestId('chip-tier-D')).toBeVisible();
  await expect(page.getByTestId('chip-size-3')).toBeVisible();
  await expect(page.getByTestId('chip-country-AUS')).toBeVisible();

  // Tier-D chip leaves only FOX BAT; toggling it off restores the queue.
  await page.getByTestId('chip-tier-D').click();
  await expect(page.getByTestId(`dupe-card-${FOX_BAT}`)).toBeVisible();
  await expect(page.getByTestId(`dupe-card-${FIFTH_AVENUE}`)).toHaveCount(0);
  await page.getByTestId('chip-tier-D').click();
  await expect(page.getByTestId(`dupe-card-${FIFTH_AVENUE}`)).toBeVisible();
});

test('merge FIFTH AVENUE|AUS into boats/17213 — card animates out, counts decrement, history records it', async ({ page }) => {
  await openQueue(page);

  const pending = page.getByTestId('dupes-pending-count');
  await expect(pending).toHaveText('3 pending');

  const card = page.getByTestId(`dupe-card-${FIFTH_AVENUE}`);
  await card.getByTestId(`merge-button-${FIFTH_AVENUE}`).click();

  // Card animates out and leaves the queue; the header count decrements.
  await expect(card).toHaveCount(0);
  await expect(pending).toHaveText('2 pending');
  await expect(page.getByTestId('decision-notice')).toContainText(
    /merged 1 boat into #17213/i,
  );

  // Merge history shows the loser snapshot.
  await page.goto('/admin/dupes/history');
  await expect(
    page.getByRole('heading', { name: /merge history/i }),
  ).toBeVisible();
  const entry = page.getByTestId('history-entry-merged').first();
  await expect(entry).toBeVisible();
  await expect(entry).toContainText(/fifth avenue/i);
  await expect(entry).toContainText('#17213');
  await entry.getByRole('button').first().click();
  await expect(page.getByTestId('history-snapshot')).toContainText('18390');
  await expect(page.getByTestId('history-snapshot')).toContainText('6138');
});

test('not-dupe FOX BAT|GBR with a reason — footer select drives the verdict', async ({ page }) => {
  await openQueue(page);

  const card = page.getByTestId(`dupe-card-${FOX_BAT}`);
  // Size-3 cluster renders three boat columns.
  await expect(card.getByTestId('dupe-boat-20101')).toBeVisible();
  await expect(card.getByTestId('dupe-boat-20102')).toBeVisible();
  await expect(card.getByTestId('dupe-boat-20103')).toBeVisible();

  await card.getByTestId(`reason-select-${FOX_BAT}`).selectOption('different_design');
  await card.getByTestId(`not-dupe-button-${FOX_BAT}`).click();

  await expect(card).toHaveCount(0);
  await expect(page.getByTestId('decision-notice')).toContainText(/not duplicates/i);

  await page.goto('/admin/dupes/history');
  const entry = page.getByTestId('history-entry-not_dupe').first();
  await expect(entry).toBeVisible();
  await expect(entry).toContainText(/fox bat/i);
  await expect(entry).toContainText(/different_design/);
});

test('skip GREY GULL|NZL — deferral only, nothing written to history', async ({ page }) => {
  await openQueue(page);

  const card = page.getByTestId(`dupe-card-${GREY_GULL}`);
  await card.getByTestId(`skip-button-${GREY_GULL}`).click();

  await expect(card).toHaveCount(0);
  await expect(page.getByTestId('decision-notice')).toContainText(/skipped/i);

  // Skip is a deferral: it must not appear in the merge history.
  await page.goto('/admin/dupes/history');
  await expect(page.getByText(/grey gull/i)).toHaveCount(0);
});

test('visual check — the queue screen vs screens/duplicate-boats.png', async ({ page }) => {
  await openQueue(page);
  await expect(page.getByTestId(`dupe-card-${FIFTH_AVENUE}`)).toBeVisible();
  await expect(page.getByTestId(`dupe-card-${FOX_BAT}`)).toBeVisible();
  // Settle fonts + cards before the capture.
  await page.waitForTimeout(600);
  await page.screenshot({
    path: 'test-results/ad-01-14-duplicate-boats.png',
    fullPage: true,
  });
});
