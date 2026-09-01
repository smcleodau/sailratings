import { test, expect } from '@playwright/test';

const POLICY_PAGE = '/sources-policy';

test.describe('DP-01-02 Collection Policy Page', () => {

  test('page loads with correct title', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    await expect(page).toHaveTitle(/Sail Ratings/i);
  });

  test('displays DP-01-02 label', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const label = page.getByTestId('issue-label');
    await expect(label).toBeVisible();
    await expect(label).toContainText('DP-01-02');
  });

  test('displays current policy version interim-v0', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const version = page.getByTestId('policy-version');
    await expect(version).toBeVisible();
    await expect(version).toContainText('interim-v0');
  });

  test('displays authority name', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const authority = page.getByTestId('authority-name');
    await expect(authority).toBeVisible();
    await expect(authority).toContainText('Stuart McLeod');
  });

  test('displays approved date 2026-08-30', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const date = page.getByTestId('approved-date');
    await expect(date).toBeVisible();
    await expect(date).toContainText('2026-08-30');
  });

  test('displays correct User-Agent string', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const ua = page.getByTestId('user-agent');
    await expect(ua).toBeVisible();
    await expect(ua).toContainText('SailRatings/1.0');
    await expect(ua).toContainText('sailratings.com');
    await expect(ua).toContainText('stuart@sailratings.com');
  });

  test('approved count shows 9', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const count = page.getByTestId('approved-count');
    await expect(count).toBeVisible();
    await expect(count).toContainText('9');
  });

  test('hold count shows 2', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const count = page.getByTestId('hold-count');
    await expect(count).toBeVisible();
    await expect(count).toContainText('2');
  });

  test('total count shows 11', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const count = page.getByTestId('total-count');
    await expect(count).toBeVisible();
    await expect(count).toContainText('11');
  });

  test('source table is present', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const table = page.getByTestId('source-table');
    await expect(table).toBeVisible();
  });

  test('source table has all expected columns', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const headers = page.locator('thead th');
    const headerTexts = await headers.allTextContents();
    expect(headerTexts).toEqual(
      expect.arrayContaining(['Source', 'Category', 'Type', 'Class', 'Status', 'Notes'])
    );
  });

  test('source table contains sailsys', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="sailsys"]');
    await expect(row).toBeVisible();
  });

  test('source table contains orc', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="orc"]');
    await expect(row).toBeVisible();
  });

  test('source table contains irc-certs', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="irc-certs"]');
    await expect(row).toBeVisible();
  });

  test('source table contains clubspot', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="clubspot"]');
    await expect(row).toBeVisible();
  });

  test('source table contains kwindoo', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="kwindoo"]');
    await expect(row).toBeVisible();
  });

  test('public HTML source (sailsys) classified as public/approved', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="sailsys"]');
    await expect(row).toHaveAttribute('data-source-class', 'public');
    await expect(row).toHaveAttribute('data-source-classification', 'approved');
    await expect(row).toHaveAttribute('data-source-content-type', 'html');
  });

  test('API source (orc) classified as public/approved', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="orc"]');
    await expect(row).toHaveAttribute('data-source-class', 'public');
    await expect(row).toHaveAttribute('data-source-classification', 'approved');
    await expect(row).toHaveAttribute('data-source-content-type', 'api');
  });

  test('PDF source (irc-certs) classified as public/approved', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="irc-certs"]');
    await expect(row).toHaveAttribute('data-source-class', 'public');
    await expect(row).toHaveAttribute('data-source-classification', 'approved');
    await expect(row).toHaveAttribute('data-source-content-type', 'pdf');
  });

  test('unclear source (clubspot) classified as unclear/hold', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="clubspot"]');
    await expect(row).toHaveAttribute('data-source-class', 'unclear');
    await expect(row).toHaveAttribute('data-source-classification', 'hold');
  });

  test('unclear source (kwindoo) classified as unclear/hold', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="kwindoo"]');
    await expect(row).toHaveAttribute('data-source-class', 'unclear');
    await expect(row).toHaveAttribute('data-source-classification', 'hold');
  });

  test('source table has exactly 11 source rows', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const rows = page.locator('tbody tr[data-source-slug]');
    await expect(rows).toHaveCount(11);
  });

  test('all approved sources show approved classification', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const approvedRows = page.locator('tr[data-source-classification="approved"]');
    await expect(approvedRows).toHaveCount(9);
  });

  test('all hold sources show hold classification', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const holdRows = page.locator('tr[data-source-classification="hold"]');
    await expect(holdRows).toHaveCount(2);
  });

  test('all sources reference policy version interim-v0', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const versionEl = page.getByTestId('policy-version');
    await expect(versionEl).toContainText('interim-v0');
  });

  test('source table contains topyacht', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="topyacht"]');
    await expect(row).toBeVisible();
  });

  test('source table contains yachtscoring', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="yachtscoring"]');
    await expect(row).toBeVisible();
  });

  test('source table contains manage2sail', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="manage2sail"]');
    await expect(row).toBeVisible();
  });

  test('source table contains sailwave', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="sailwave"]');
    await expect(row).toBeVisible();
  });

  test('source table contains sailing-news', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="sailing-news"]');
    await expect(row).toBeVisible();
  });

  test('source table contains irc-tcc', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    const row = page.locator('tr[data-source-slug="irc-tcc"]');
    await expect(row).toBeVisible();
  });

  test('collection policy heading is visible', async ({ page }) => {
    await page.goto(POLICY_PAGE);
    await expect(page.getByRole('heading', { name: /Collection Policy/i })).toBeVisible();
  });
});
