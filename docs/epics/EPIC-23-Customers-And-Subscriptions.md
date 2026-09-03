# EPIC-23: Customers, Plans & Subscriptions

Make "a customer" a real thing: Clerk users in Postgres, a Stripe customer per user, Pro/Team recurring plans, subscription state from webhooks, boat claims, and an admin Users & plans page. Spec: `docs/specs/SPEC-23-Customers-And-Subscriptions.md`. Supersedes SPEC-09 §3 and SPEC-10.

Starting position (2 Sep 2026): 0 Stripe customers, 0 subscriptions, 1 real charge, `users` table does not exist. Pro prices already exist in Stripe (`pro_monthly_gbp`, `pro_annual_gbp`).

## Issues
1. Alembic 0026: `users`, `subscriptions`, `stripe_events`, `boat_claims`, `orders.user_id`, `v_admin_users` — SPEC-23 §1
2. Clerk → Postgres sync: webhook + backfill CLI (existing issue "Sync Clerk Users to Backend Postgres DB", re-pointed at SPEC-23 §2)
3. Stripe customers: `customer_creation='always'`, `users.stripe_customer_id`, link on checkout.session.completed — SPEC-23 §3
4. Prices: Team product/price, one-off report prices per currency on the catalogue, drop ad-hoc amounts from `currency.ts` — SPEC-23 §4
5. Subscription checkout + Customer Portal endpoints — SPEC-23 §4
6. Stripe webhook: `customer.subscription.*` → `subscriptions`, idempotent via `stripe_events` — SPEC-23 §5
7. API: admin users / claims / orders / billing endpoints — SPEC-23 §6
8. Web: Users & plans, Reports & orders, Stripe & pricing screens in the admin shell — SPEC-23 §7
9. Stripe Dashboard hygiene (manual): statement descriptor `SAILRATINGS`, public business name, branding — SPEC-23 §0
10. Playwright: test-mode end-to-end Pro purchase → webhook → admin shows plan — SPEC-23 §8

## Done when
SPEC-23 §8 acceptance passes in Stripe test mode against dev.
