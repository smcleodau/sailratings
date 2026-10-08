import { test, expect, type Page } from '@playwright/test';

/**
 * AUTH-01-02 — Sign-in / create-account pages (incl. Google).
 *
 * What is verified:
 *   1. Both auth pages render the SailRatings frame (wordmark, strapline,
 *      corner-bracketed card on the Abyss ground) in every environment.
 *   2. When Clerk is NOT configured (default `npm run test` server), both
 *      pages render an explicit "not available" notice instead of crashing —
 *      the no-false-done contract shared with the root layout + middleware.
 *   3. When Clerk IS configured (run via playwright.auth.config.ts), the
 *      full form contract is exercised:
 *        - email + password fields and Continue action (email+password)
 *        - "Continue with Google" social button (Google OAuth enabled)
 *        - "Forgot password?" reset link
 *        - protected-route bounce (/admin → /sign-in?redirect_url=…)
 *        - error state on submit with an unknown identifier
 *        - cross-links between /sign-in and /sign-up
 *
 * Error and redirect behaviour depends on the live Clerk instance, so those
 * assertions are retried for a short window to absorb hosted-UI latency.
 */

const CLERK_CONFIGURED = process.env.E2E_CLERK === '1';

// ── Environment-independent frame checks ──────────────────────────────────

test.describe('AUTH-01-02 auth frame', () => {
  for (const route of ['/sign-in', '/sign-up']) {
    test(`${route} renders the SailRatings auth frame`, async ({ page }) => {
      const response = await page.goto(route, { waitUntil: 'domcontentloaded' });
      expect(response?.status(), `${route} must not 5xx`).toBeLessThan(500);

      const frame = page.getByTestId('auth-frame');
      await expect(frame).toBeVisible();

      // Wordmark links back to the funnel.
      const wordmark = frame.getByRole('link', { name: 'SailRatings home' });
      await expect(wordmark).toBeVisible();
      await expect(wordmark).toHaveAttribute('href', '/');

      // Corner-bracketed card is present and styled from DS tokens.
      const card = frame.locator('.sr-auth-card');
      await expect(card).toBeVisible();
      expect(await frame.locator('.sr-auth-corner').count()).toBe(4);

      // Abyss ground — rgb(7, 16, 15), not a generic white/grey page.
      const ground = await frame.evaluate((el) => getComputedStyle(el).backgroundColor);
      expect(ground).toBe('rgb(7, 16, 15)');
    });
  }

  test('/sign-in carries the sign-in strapline', async ({ page }) => {
    await page.goto('/sign-in', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('auth-frame')).toContainText(/sign in to your account/i);
  });

  test('/sign-up carries the create-account strapline', async ({ page }) => {
    await page.goto('/sign-up', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('auth-frame')).toContainText(/create your account/i);
  });
});

// ── Unconfigured (default `npm run test`) — graceful degradation ──────────

test.describe('AUTH-01-02 without Clerk configured', () => {
  test.skip(CLERK_CONFIGURED, 'Clerk IS configured for this run');

  for (const route of ['/sign-in', '/sign-up']) {
    test(`${route} renders an explicit notice instead of crashing`, async ({ page }) => {
      const response = await page.goto(route, { waitUntil: 'domcontentloaded' });
      expect(response?.status()).toBeLessThan(500);

      const notice = page.getByTestId('auth-not-configured');
      await expect(notice).toBeVisible();
      await expect(notice).toContainText(/not available on this environment/i);
      await expect(notice.getByRole('link', { name: /return to sailratings/i })).toHaveAttribute('href', '/');
    });
  }
});

// ── Clerk-configured (playwright.auth.config.ts) — full form contract ─────

test.describe('AUTH-01-02 with Clerk configured', () => {
  test.skip(!CLERK_CONFIGURED, 'requires E2E_CLERK=1 and live Clerk keys (playwright.auth.config.ts)');

  async function expectSignInForm(page: Page) {
    const form = page.getByTestId('sign-in-form');
    await expect(form).toBeVisible();

    // Email + password
    await expect(form.getByLabel(/email/i).first()).toBeVisible();
    await expect(form.locator('input[type="password"]').first()).toBeVisible();

    // Google OAuth
    await expect(form.getByRole('button', { name: /google/i }).first()).toBeVisible();

    // Forgot password (link or button depending on Clerk version)
    const forgot = form.getByText(/forgot password/i).first();
    await expect(forgot).toBeVisible();
  }

  test('sign-in renders email+password, Google, and forgot-password', async ({ page }) => {
    await page.goto('/sign-in', { waitUntil: 'domcontentloaded' });
    await expect(async () => {
      await expectSignInForm(page);
    }).toPass({ timeout: 20000 });
  });

  test('sign-up renders email+password and Google', async ({ page }) => {
    await page.goto('/sign-up', { waitUntil: 'domcontentloaded' });

    const form = page.getByTestId('sign-up-form');
    await expect(async () => {
      await expect(form).toBeVisible();
      await expect(form.getByLabel(/email/i).first()).toBeVisible();
      await expect(form.locator('input[type="password"]').first()).toBeVisible();
      await expect(form.getByRole('button', { name: /google/i }).first()).toBeVisible();
    }).toPass({ timeout: 20000 });
  });

  test('/admin bounces to /sign-in carrying the intended page', async ({ page }) => {
    await page.goto('/admin', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/sign-in/);
    // Clerk receives the intended destination so the user lands back on
    // /admin after authenticating.
    expect(page.url()).toContain('redirect_url');
  });

  test('submitting an unknown identifier surfaces an error state', async ({ page }) => {
    await page.goto('/sign-in', { waitUntil: 'domcontentloaded' });

    const form = page.getByTestId('sign-in-form');
    await expect(async () => {
      await expect(form.getByLabel(/email/i).first()).toBeVisible();
    }).toPass({ timeout: 20000 });

    await form.getByLabel(/email/i).first().fill('no-such-user-auth0102@example.com');
    await form.getByRole('button', { name: /continue/i }).first().click();

    // Clerk either flags the field inline or shows an alert above the form —
    // either way the user gets a visible error state, never a silent dead end.
    await expect(async () => {
      const feedback = form.locator(
        '[id$="-error"], [role="alert"], .cl-formFieldErrorText, .cl-alert',
      );
      expect(await feedback.count()).toBeGreaterThan(0);
    }).toPass({ timeout: 15000 });
  });

  test('/sign-up links across to /sign-in', async ({ page }) => {
    await page.goto('/sign-up', { waitUntil: 'domcontentloaded' });
    const link = page.getByRole('link', { name: /sign in/i }).first();
    await expect(async () => {
      await expect(link).toBeVisible();
      await expect(link).toHaveAttribute('href', /\/sign-in/);
    }).toPass({ timeout: 20000 });
  });

  test('/sign-in links across to /sign-up', async ({ page }) => {
    await page.goto('/sign-in', { waitUntil: 'domcontentloaded' });
    const link = page.getByRole('link', { name: /sign up/i }).first();
    await expect(async () => {
      await expect(link).toBeVisible();
      await expect(link).toHaveAttribute('href', /\/sign-up/);
    }).toPass({ timeout: 20000 });
  });
});

// ── AD-01-17 — Clerk role claim enforced in middleware ────────────────────
//
// /admin must gate on more than "is signed in": the session's role (Clerk
// publicMetadata.role, mirrored from users.role — SPEC-23 §1) has to be
// 'admin' or 'staff'. These two tests exercise the live Clerk instance end
// to end rather than stubbing the gate:
//   1. create a throwaway Clerk user via the Backend API (CLERK_SECRET_KEY)
//      with the desired publicMetadata.role,
//   2. mint a sign-in token for it and redeem the ticket against the app's
//      own /sign-in page (no UI interaction, no password needed),
//   3. request /admin from that authenticated context and assert the
//      middleware's decision.
// The user is deleted again in `finally` so the Clerk instance doesn't
// accumulate test accounts.
test.describe('AUTH-01-02 with Clerk configured', () => {
  test.skip(!CLERK_CONFIGURED, 'requires E2E_CLERK=1 and live Clerk keys (playwright.auth.config.ts)');

  async function clerkBackend(path: string, init?: RequestInit) {
    const secret = process.env.CLERK_SECRET_KEY;
    if (!secret) throw new Error('CLERK_SECRET_KEY is required to mint test Clerk sessions');
    const res = await fetch(`https://api.clerk.com/v1${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${secret}`,
        'Content-Type': 'application/json',
        ...(init?.headers ?? {}),
      },
    });
    if (!res.ok) {
      throw new Error(`Clerk Backend API ${path} → ${res.status}: ${await res.text()}`);
    }
    return res.json();
  }

  async function createClerkUser(role?: 'admin' | 'staff'): Promise<string> {
    const suffix = `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
    const user = await clerkBackend('/users', {
      method: 'POST',
      body: JSON.stringify({
        email_address: [`ad0117_${role ?? 'none'}_${suffix}@example.com`],
        password: `Sr!${suffix}Aa1`,
        public_metadata: role ? { role } : {},
        skip_password_checks: true,
      }),
    });
    return user.id as string;
  }

  async function deleteClerkUser(userId: string) {
    await fetch(`https://api.clerk.com/v1/users/${userId}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${process.env.CLERK_SECRET_KEY}` },
    }).catch(() => {
      // best-effort cleanup only
    });
  }

  // Redeem a Clerk sign-in token ("ticket" strategy) against our own
  // /sign-in page — the same mechanism Clerk documents for passwordless
  // test sign-in — then wait for the resulting redirect away from
  // /sign-in, which only happens once ClerkJS has called setActive().
  // Tickets are single-use and short-lived, and the hosted-UI round trip
  // occasionally outruns a single attempt, so a fresh ticket is minted on
  // each retry rather than reusing (and failing with) a spent one.
  async function signInWithTicket(page: Page, userId: string) {
    await expect(async () => {
      const { token } = await clerkBackend('/sign_in_tokens', {
        method: 'POST',
        body: JSON.stringify({ user_id: userId, expires_in_seconds: 60 }),
      });
      await page.goto(`/sign-in?__clerk_ticket=${token}`, { waitUntil: 'domcontentloaded' });
      await page.waitForURL((url) => !url.pathname.startsWith('/sign-in'), { timeout: 10000 });
    }).toPass({ timeout: 45000 });
  }

  test('signed-in non-admin → /admin redirects to /account?denied=admin', async ({ page }) => {
    const userId = await createClerkUser();
    try {
      await signInWithTicket(page, userId);
      await page.goto('/admin', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/account\?denied=admin/);
    } finally {
      await deleteClerkUser(userId);
    }
  });

  test('admin → /admin renders the sidebar', async ({ page }) => {
    const userId = await createClerkUser('admin');
    try {
      await signInWithTicket(page, userId);
      await page.goto('/admin', { waitUntil: 'domcontentloaded' });
      await expect(page).toHaveURL(/\/admin$/);
      await expect(page.getByTestId('admin-sidebar')).toBeVisible();
    } finally {
      await deleteClerkUser(userId);
    }
  });
});
