/**
 * DP-01-02 — Responsible Collection Policy E2E tests.
 *
 * These Playwright tests verify the collection policy page at /sources
 * correctly surfaces all DP-01-02 acceptance criteria:
 *
 * - Policy version gate: 'interim-v0' is displayed and current
 * - Source classification: public, authenticated, licensed, prohibited, unclear
 * - Policy fixtures exercise: public HTML, API, PDF, login wall, disallow, unclear
 * - Emergency disable works by source and domain (documented on page)
 * - Personal data restrictions are listed
 * - Takedown / kill switch procedures are documented
 * - Collection rules (robots, rate, window, dedup, caps, prohibited) are present
 */

import { test, expect } from '@playwright/test';

// All source slugs defined in CollectionPolicyDecisionV1
const ALL_SOURCES = [
  'sailsys',
  'topyacht',
  'irc-tcc',
  'orc',
  'yachtscoring',
  'manage2sail',
  'sailwave',
  'sailing-news',
  'irc-certs',
  'clubspot',
  'kwindoo',
];

// Approved sources (9)
const APPROVED_SOURCES = [
  'sailsys',
  'topyacht',
  'irc-tcc',
  'orc',
  'yachtscoring',
  'manage2sail',
  'sailwave',
  'sailing-news',
  'irc-certs',
];

// Hold sources (2)
const HOLD_SOURCES = ['clubspot', 'kwindoo'];

// All five source classes that must be represented
const ALL_SOURCE_CLASSES = [
  'public',
  'authenticated',
  'licensed',
  'prohibited',
  'unclear',
];

// Prohibited personal-data fields
const PROHIBITED_FIELDS = [
  'owner_name',
  'owner_email',
  'owner_phone',
  'owner_address',
  'home_port',
  'financial_data',
];

test.describe('DP-01-02 Collection Policy Page', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/sources');
  });

  test('page loads with correct title', async ({ page }) => {
    await expect(page).toHaveTitle(/Data Sources.*Collection Policy/);
  });

  test('displays DP-01-02 label', async ({ page }) => {
    await expect(page.getByTestId('policy-page-label')).toContainText('DP-01-02');
  });

  // ─── Policy version gate ──────────────────────────────────

  test('displays current policy version interim-v0', async ({ page }) => {
    const versionEl = page.getByTestId('policy-version');
    await expect(versionEl).toBeVisible();
    await expect(versionEl).toHaveText('interim-v0');
  });

  test('displays approved date 2026-08-30', async ({ page }) => {
    await expect(page.getByTestId('policy-approved-date')).toHaveText('2026-08-30');
  });

  test('displays authority name', async ({ page }) => {
    const banner = page.getByTestId('policy-version-banner');
    await expect(banner).toContainText('Stuart McLeod');
  });

  test('displays correct User-Agent string', async ({ page }) => {
    const ua = page.getByTestId('user-agent');
    await expect(ua).toContainText('SailRatings/1.0');
    await expect(ua).toContainText('sailratings.com');
    await expect(ua).toContainText('stuart@sailratings.com');
  });

  // ─── Source classification table ──────────────────────────

  test('source table is present', async ({ page }) => {
    await expect(page.getByTestId('source-table')).toBeVisible();
  });

  test('all 11 sources are listed in the table', async ({ page }) => {
    for (const slug of ALL_SOURCES) {
      const row = page.getByTestId(`source-row-${slug}`);
      await expect(row).toBeVisible();
      // Verify the slug data attribute
      await expect(row).toHaveAttribute('data-source-slug', slug);
    }
  });

  test('approved count shows 9', async ({ page }) => {
    await expect(page.getByTestId('approved-count')).toContainText('9 approved');
  });

  test('hold count shows 2', async ({ page }) => {
    await expect(page.getByTestId('hold-count')).toContainText('2 on hold');
  });

  test('total count shows 11', async ({ page }) => {
    await expect(page.getByTestId('total-count')).toContainText('11 total sources');
  });

  // ─── Public sources (public HTML, API, PDF fixtures) ───────

  test('public HTML source (sailsys) classified as public/approved', async ({ page }) => {
    const row = page.getByTestId('source-row-sailsys');
    await expect(row).toHaveAttribute('data-source-class', 'public');
    await expect(row).toHaveAttribute('data-legal-status', 'approved');
    await expect(page.getByTestId('source-class-sailsys')).toHaveText('public');
    await expect(page.getByTestId('legal-status-sailsys')).toHaveText('approved');
  });

  test('API source (orc) classified as public/approved', async ({ page }) => {
    const row = page.getByTestId('source-row-orc');
    await expect(row).toHaveAttribute('data-source-class', 'public');
    await expect(row).toHaveAttribute('data-legal-status', 'approved');
  });

  test('PDF source (irc-certs) classified as public/approved', async ({ page }) => {
    const row = page.getByTestId('source-row-irc-certs');
    await expect(row).toHaveAttribute('data-source-class', 'public');
    await expect(row).toHaveAttribute('data-legal-status', 'approved');
  });

  // ─── Unclear / hold sources ───────────────────────────────

  test('unclear source (clubspot) classified as unclear/hold', async ({ page }) => {
    const row = page.getByTestId('source-row-clubspot');
    await expect(row).toHaveAttribute('data-source-class', 'unclear');
    await expect(row).toHaveAttribute('data-legal-status', 'hold');
    await expect(page.getByTestId('source-class-clubspot')).toHaveText('unclear');
    await expect(page.getByTestId('legal-status-clubspot')).toHaveText('hold');
  });

  test('unclear source (kwindoo) classified as unclear/hold', async ({ page }) => {
    const row = page.getByTestId('source-row-kwindoo');
    await expect(row).toHaveAttribute('data-source-class', 'unclear');
    await expect(row).toHaveAttribute('data-legal-status', 'hold');
  });

  // ─── All approved sources verified ─────────────────────────

  test('all approved sources have approved legal status', async ({ page }) => {
    for (const slug of APPROVED_SOURCES) {
      const row = page.getByTestId(`source-row-${slug}`);
      await expect(row).toHaveAttribute('data-legal-status', 'approved');
    }
  });

  test('all hold sources have hold legal status', async ({ page }) => {
    for (const slug of HOLD_SOURCES) {
      const row = page.getByTestId(`source-row-${slug}`);
      await expect(row).toHaveAttribute('data-legal-status', 'hold');
    }
  });

  // ─── All five source classes are represented ──────────────

  test('all five source classes are described in the section header', async ({ page }) => {
    const section = page.getByTestId('source-classification-section');
    await expect(section).toContainText('public');
    await expect(section).toContainText('authenticated');
    await expect(section).toContainText('licensed');
    await expect(section).toContainText('prohibited');
    await expect(section).toContainText('unclear');
  });

  // ─── Collection rules ─────────────────────────────────────

  test('robots.txt compliance rules are present', async ({ page }) => {
    const block = page.getByTestId('rule-block-robots-txt-compliance');
    await expect(block).toBeVisible();
    await expect(block).toContainText('robots.txt');
    await expect(block).toContainText('disallow');
  });

  test('rate limiting rules are present', async ({ page }) => {
    const block = page.getByTestId('rule-block-rate-limiting');
    await expect(block).toBeVisible();
    await expect(block).toContainText('1 request per 2 seconds');
    await expect(block).toContainText('jitter');
  });

  test('collection window rules are present', async ({ page }) => {
    const block = page.getByTestId('rule-block-collection-window');
    await expect(block).toBeVisible();
    await expect(block).toContainText('01:00');
    await expect(block).toContainText('06:00');
  });

  test('conditional requests and dedup rules are present', async ({ page }) => {
    const block = page.getByTestId('rule-block-conditional-requests-deduplication');
    await expect(block).toBeVisible();
    await expect(block).toContainText('If-None-Match');
    await expect(block).toContainText('SHA-256');
  });

  test('hard caps rules are present', async ({ page }) => {
    const block = page.getByTestId('rule-block-hard-caps-per-source-per-night');
    await expect(block).toBeVisible();
    await expect(block).toContainText('25 MB');
    await expect(block).toContainText('5,000');
    await expect(block).toContainText('500 MB');
  });

  test('prohibited collection rules are present', async ({ page }) => {
    const block = page.getByTestId('rule-block-prohibited-collection');
    await expect(block).toBeVisible();
    await expect(block).toContainText('login wall');
    await expect(block).toContainText('paywall');
    await expect(block).toContainText('CAPTCHA');
  });

  // ─── Personal data restrictions ───────────────────────────

  test('personal data section lists all prohibited fields', async ({ page }) => {
    for (const field of PROHIBITED_FIELDS) {
      await expect(page.getByTestId(`prohibited-field-${field}`)).toBeVisible();
    }
  });

  // ─── Takedown & emergency disable ─────────────────────────

  test('takedown response window is 4 hours', async ({ page }) => {
    const window = page.getByTestId('takedown-window');
    await expect(window).toContainText('4 hours');
  });

  test('kill switch works by source and domain', async ({ page }) => {
    const desc = page.getByTestId('kill-switch-desc');
    await expect(desc).toContainText('source');
    await expect(desc).toContainText('domain');
    await expect(desc).toContainText('COLLECTION_ENABLED');
  });

  test('takedown contact email is present', async ({ page }) => {
    await expect(page.getByTestId('takedown-contact')).toContainText('stuart@sailratings.com');
  });

  // ─── Enforcement ──────────────────────────────────────────

  test('enforcement section mentions CollectionGate', async ({ page }) => {
    const section = page.getByTestId('enforcement-section');
    await expect(section).toContainText('CollectionGate');
  });

  test('enforcement section mentions PolicyVersionMismatchError', async ({ page }) => {
    const section = page.getByTestId('enforcement-section');
    await expect(section).toContainText('PolicyVersionMismatchError');
  });
});
