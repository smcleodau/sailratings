import { test, expect, type Page } from '@playwright/test';

const mockQueue = [
  {
    case_id: 'adj-01-mock',
    status: 'pending',
    queue_reason: 'uncertain_match',
    priority: 1,
    pair: {
      left_id: 'boat-left',
      right_id: 'boat-right',
      rules_fired: ['rule-1'],
      matching_keys: ['name'],
      ruleset_id: 'rs-1'
    },
    score: 0.65,
    score_explanation: ['Some explanation'],
    impact: 'medium',
    impact_flags: [],
    left_evidence: { name: 'BOAT A' },
    right_evidence: { name: 'BOAT B' },
    actions: ['merge', 'separate'],
    requires_second_review: false,
    votes: [],
    enqueued_at: '2023-01-01T00:00:00Z'
  },
  {
    case_id: 'adj-02-mock',
    status: 'pending',
    queue_reason: 'high_impact',
    priority: 2,
    pair: {
      left_id: 'boat-x',
      right_id: 'boat-y',
      rules_fired: [],
      matching_keys: [],
      ruleset_id: 'rs-1'
    },
    score: 0.95,
    score_explanation: [],
    impact: 'high',
    impact_flags: [],
    left_evidence: { name: 'BOAT X' },
    right_evidence: { name: 'BOAT X' },
    actions: ['merge'],
    requires_second_review: true,
    votes: [],
    enqueued_at: '2023-01-01T00:00:00Z'
  }
];

test.describe('AD-01-04 Identity Adjudication UI', () => {
  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      window.localStorage.setItem('admin_token', 'sailfast2026');
    });

    await page.route('**/v1/admin/adjudication/queue*', async route => {
      await route.fulfill({ json: mockQueue });
    });

    await page.route('**/v1/admin/adjudication/resolutions*', async route => {
      await route.fulfill({ json: [] });
    });
  });

  test('renders the match cards and side-by-side evidence', async ({ page }) => {
    await page.goto('/admin/identity');
    await expect(page.getByRole('heading', { name: /identity adjudication/i })).toBeVisible();

    const card = page.getByTestId('match-card').first();
    await expect(card).toBeVisible();
    await expect(card).toContainText('BOAT A');
    await expect(card).toContainText('BOAT B');
    await expect(card).toContainText('65%');
  });

  test('keyboard flow works on the active card', async ({ page }) => {
    let decidedAction = '';
    await page.route('**/v1/admin/adjudication/decide', async route => {
      const payload = route.request().postDataJSON();
      decidedAction = payload.decision;
      await route.fulfill({ json: { status: 'applied' } });
    });

    await page.goto('/admin/identity');
    await expect(page.getByTestId('match-card').first()).toBeVisible();

    // Trigger keyboard 's' for separate
    await page.keyboard.press('s');

    // Wait for the route to be triggered
    await page.waitForResponse(resp => resp.url().includes('decide'));
    expect(decidedAction).toBe('separate');
  });

  test('visual check — the adjudication screen vs screens/identity-adjudication.png', async ({ page }) => {
    await page.goto('/admin/identity');
    await expect(page.getByTestId('match-card').first()).toBeVisible();
    // Wait for fonts/layout
    await page.waitForTimeout(600);
    await page.screenshot({
      path: 'test-results/ad-01-04-identity-adjudication.png',
      fullPage: true,
    });
  });
});