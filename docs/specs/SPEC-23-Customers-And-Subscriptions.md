# SPEC-23: Customers, Plans & Subscriptions

Supersedes SPEC-09 §3 (users schema) and SPEC-10 (which specifies a $290/yr plan, Stripe Elements and the Paper design system — none of which match the business plan or the live Stripe account). SPEC-09 §2 (Clerk in Next.js) is done.

## 0. Facts this spec is built on (live Stripe account acct_1TBDqmBgNLKGD8v8, 2 Sep 2026)
- 0 customers, 0 subscriptions. Checkout sessions use `customer_creation: if_required`, so every buyer is a guest.
- Product `prod_UAxQjIqAtriLAT` "Premium Ratings Report" £69 one-time is unused: `web/src/lib/currency.ts` computes $99 / £79 / A$149 at checkout as ad-hoc line items.
- Product `prod_VBgRYFAtXdOAmt` "SailRatings Pro" created 2 Sep with prices `pro_monthly_gbp` (£12/mo, default) and `pro_annual_gbp` (£96/yr). **Reference prices by `lookup_key`, never by id.**
- 10 checkout sessions ever; 1 real charge ($99); 4 completed with 100% promo `BETASAIL`; 5 expired. `orders` has 47 rows, so 37 orders never reached Stripe.
- Existing webhook: `POST /v1/checkout/webhook` (checkout.py) handles the one-off report flow. Keep it; add subscription events beside it.
- Dashboard-only fixes still owed: statement descriptor → `SAILRATINGS`; public business name → `SailRatings`; checkout branding.

## 1. Schema (Alembic 0026_users_and_subscriptions)
```sql
CREATE TABLE users (
  id                 bigserial PRIMARY KEY,
  clerk_id           text NOT NULL UNIQUE,
  email              text NOT NULL,
  full_name          text,
  stripe_customer_id text UNIQUE,
  role               text NOT NULL DEFAULT 'user' CHECK (role IN ('user','staff','admin')),
  last_seen_at       timestamptz,
  created_at         timestamptz NOT NULL DEFAULT now(),
  updated_at         timestamptz NOT NULL DEFAULT now(),
  deleted_at         timestamptz
);
CREATE UNIQUE INDEX users_email_lower_idx ON users (lower(email)) WHERE deleted_at IS NULL;

CREATE TABLE subscriptions (
  id                     bigserial PRIMARY KEY,
  user_id                bigint NOT NULL REFERENCES users(id),
  stripe_subscription_id text NOT NULL UNIQUE,
  stripe_customer_id     text NOT NULL,
  plan                   text NOT NULL,           -- 'pro' | 'team' | 'enterprise', from price lookup_key prefix
  price_lookup_key       text,
  status                 text NOT NULL,           -- Stripe status verbatim: trialing, active, past_due, canceled, unpaid, incomplete, incomplete_expired, paused
  current_period_start   timestamptz,
  current_period_end     timestamptz,
  cancel_at_period_end   boolean NOT NULL DEFAULT false,
  canceled_at            timestamptz,
  ended_at               timestamptz,
  raw                    jsonb,
  created_at             timestamptz NOT NULL DEFAULT now(),
  updated_at             timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX subscriptions_user_idx   ON subscriptions (user_id);
CREATE INDEX subscriptions_status_idx ON subscriptions (status);

CREATE TABLE stripe_events (            -- idempotency + audit for every webhook delivery
  id           text PRIMARY KEY,        -- evt_…
  type         text NOT NULL,
  created      timestamptz NOT NULL,
  received_at  timestamptz NOT NULL DEFAULT now(),
  processed_at timestamptz,
  error        text,
  payload      jsonb NOT NULL
);

CREATE TABLE boat_claims (
  id          bigserial PRIMARY KEY,
  user_id     bigint  NOT NULL REFERENCES users(id),
  boat_id     integer NOT NULL REFERENCES boats(id),
  status      text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','verified','rejected')),
  evidence_url text,
  note        text,
  verified_by text,
  verified_at timestamptz,
  created_at  timestamptz NOT NULL DEFAULT now(),
  UNIQUE (user_id, boat_id)
);

ALTER TABLE orders ADD COLUMN user_id bigint REFERENCES users(id);
CREATE INDEX orders_user_idx ON orders (user_id);

-- Read model for the admin Users & plans page
CREATE VIEW v_admin_users AS
SELECT u.id, u.clerk_id, u.email, u.full_name, u.role, u.stripe_customer_id, u.created_at, u.last_seen_at,
       s.plan, s.status AS plan_status, s.current_period_end, s.price_lookup_key,
       (SELECT count(*) FROM boat_claims c WHERE c.user_id=u.id AND c.status='verified') AS boats_claimed,
       (SELECT count(*) FROM boat_claims c WHERE c.user_id=u.id AND c.status='pending')  AS claims_pending,
       (SELECT count(*) FROM orders o WHERE o.user_id=u.id AND o.status='paid')          AS reports_bought
FROM users u
LEFT JOIN LATERAL (SELECT * FROM subscriptions s WHERE s.user_id=u.id AND s.status IN ('trialing','active','past_due') ORDER BY created_at DESC LIMIT 1) s ON true
WHERE u.deleted_at IS NULL;
```
Note: `subscription_status` is **not** a column on `users` (SPEC-09 had it there). Plan is derived from `subscriptions`, so a webhook never has to touch two tables.

## 2. Clerk → Postgres (issue already In Progress: "Sync Clerk Users to Backend Postgres DB")
- `POST /v1/webhooks/clerk`, Svix-verified with `CLERK_WEBHOOK_SECRET`.
- `user.created` / `user.updated` → upsert `users` on `clerk_id` (email = primary email address; full_name = first + last).
- `user.deleted` → set `deleted_at`; keep the row for order history.
- `session.created` → `last_seen_at = now()` (cheap, gives "last seen" without tracking).
- Backfill once: `clerkClient.users.getUserList()` paged, upsert all. Expose as CLI `irc-data sync-clerk-users`.
- Pattern reference (Clerk SDK snippet "clerk-client-backend", webhook section). The Python side uses `svix` and the same three headers.

## 3. Stripe customers
- `POST /v1/checkout/create-session` (exists): when the caller is signed in, look up `users.stripe_customer_id`; if null, `stripe.Customer.create(email, metadata={clerk_id})` and store it; pass `customer=` to the session. Set `customer_creation='always'` for guests so a customer always exists; on `checkout.session.completed` link `session.customer` to the user by email if the user exists, else leave for the next sign-in to claim.
- Store `users.stripe_customer_id`; never store card data.

## 4. Prices
- Done: `pro_monthly_gbp` £12, `pro_annual_gbp` £96 (product `prod_VBgRYFAtXdOAmt`).
- To create (issue): product "SailRatings Team" with `team_monthly_gbp` £120, `quantity` = seats; product "SailRatings Enterprise API" with metered `enterprise_api_gbp` (decide meter first).
- Move the one-off report onto the catalogue: prices `report_gbp` £79, `report_usd` $99, `report_aud` A$149 on `prod_UAxQjIqAtriLAT`, and stop passing ad-hoc amounts from `currency.ts` — select the price by currency instead.
- Subscription checkout: `mode='subscription'`, `line_items=[{price: <id from lookup_key>, quantity: 1}]`, `customer=<users.stripe_customer_id>`, `metadata={clerk_id, plan}`. Hosted Checkout, not Elements (SPEC-10's Elements requirement is dropped; nothing in the business plan needs it).
- Customer Portal for self-serve cancel/upgrade: `POST /v1/billing/portal` → `stripe.billing_portal.Session.create(customer, return_url)`.

## 5. Subscription webhook
Extend `POST /v1/checkout/webhook` (or add `POST /v1/webhooks/stripe` and point the endpoint there; one endpoint, one secret):
- Insert into `stripe_events` first (`ON CONFLICT DO NOTHING`; if it existed, return 200 — idempotent).
- `customer.subscription.created|updated|deleted|paused|resumed` → upsert `subscriptions` by `stripe_subscription_id` with status, period, cancel flags, `plan` from `items[0].price.lookup_key.split('_')[0]`; `user_id` via `users.stripe_customer_id`, else via customer email, else park the event with `error='no user'` for the admin to resolve.
- `invoice.payment_failed` → no schema change; surface in admin (status becomes `past_due` via the subscription.updated event anyway).
- `checkout.session.completed` (existing) additionally sets `orders.user_id` when the customer maps to a user.
- Mark `processed_at`; on exception write `error` and return 500 so Stripe retries.

## 6. Admin endpoints (Customers zone)
- `GET /v1/admin/users?q=&plan=&role=&claims=pending&cursor=` → rows from `v_admin_users`.
- `GET /v1/admin/users/{id}` → user + subscriptions + claims + orders + last 20 `stripe_events` for their customer.
- `POST /v1/admin/users/{id}/role {"role":"staff"}` → writes `admin_edits`.
- `POST /v1/admin/claims/{id}/verify` / `/reject {"note":""}`.
- `GET /v1/admin/orders` → `orders` joined to Stripe session status (cache `payment_status` on `orders` when the webhook fires: add `orders.stripe_payment_status text`). Show the 37 never-sent orders as `abandoned`.
- `POST /v1/admin/orders/{id}/regenerate` → re-runs report generation.
- `GET /v1/admin/billing` → catalogue (products, prices by lookup_key), promo codes with redemptions, balance, last 20 charges — read live from Stripe, cached 60s.

## 7. Users & plans page (admin)
Columns: email, name, plan (pill: pro/team/none) + status, boats claimed (+ pending badge), reports bought, joined, last seen, role. Filters: plan, role, claims pending. Row → user detail with subscription timeline and a "Open in Stripe" link (`https://dashboard.stripe.com/customers/{stripe_customer_id}`). Stat tiles: users, active subscriptions by plan, claims pending, MRR (sum of active `pro`/`team` price amounts ÷ interval months).

## 8. Acceptance
- Signing up in Clerk creates a `users` row within 5 s (webhook) and the user appears on Users & plans.
- Buying Pro via hosted Checkout with card 4242… in test mode creates a Stripe customer, a `subscriptions` row with `status='active'`, and the user shows plan "pro" in admin; cancelling in the Customer Portal flips it to `cancel_at_period_end=true`, then `canceled`.
- Re-delivering the same Stripe event produces no second row (`stripe_events` PK).
- A claimed boat verified in admin shows on the user's detail and increments `boats_claimed`.
- Orders page lists all 47 order rows with an honest status, including the 37 abandoned ones.
