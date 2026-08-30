# SPEC-10: Subscription & Payment Engine

## 1. Overview
This specification details the implementation of a $290/yr recurring subscription model utilizing Stripe Elements (custom embedded checkout) and a Playwright test suite to verify the end-to-end payment and access-granting flow.

## 2. Frontend Integration (Stripe Elements)
- **Embedded Checkout:** Implement the checkout flow natively within the Next.js `web/` app using Stripe Elements (specifically the Payment Element). The user must not be redirected to Stripe Checkout's hosted pages.
- **Pricing Context:** Ensure the UI logic clearly distinguishes between the $99 one-off report purchase and the $290/yr recurring subscription, handling cases like "already owned" or "credit to Skipper".
- **Styling:** The Elements form should be themed to match the "Paper" design system, using `--color-navy`, `--color-charcoal`, and `--color-brass`.

## 3. Backend Integration (Stripe Webhooks)
- **Webhook Endpoint:** Implement `POST /v1/webhooks/stripe`.
- **Signature Validation:** Use the Stripe Python SDK (`stripe.Webhook.construct_event`) to validate the `STRIPE_WEBHOOK_SECRET`.
- **Required Events to Handle:**
  - `customer.subscription.created`: Locate the user in Postgres via `stripe_customer_id` (or email fallback) and update `subscription_status` to `'premium'`.
  - `customer.subscription.updated`: Update the `subscription_status` if the plan changes.
  - `customer.subscription.deleted`: Immediately downgrade the user's `subscription_status` to `'none'`.
  - `invoice.payment_succeeded`: Log the payment and provision the corresponding premium access.

## 4. End-to-End Testing (Playwright)
- **Testing Strategy:** Use Playwright to run end-to-end tests against a live Stripe Test Mode environment and a local Postgres database. Do not mock Stripe.
- **Test Scenarios:**
  - Complete the full $290/yr signup flow using a test credit card (e.g., `4242 4242 4242 4242`).
  - Verify that the webhook successfully fires and updates the Postgres user record.
  - Verify the user is granted access to the soft-gated Premium sections after a successful payment.

## 5. Acceptance Criteria
- [ ] Next.js checkout page uses Stripe Elements embedded securely.
- [ ] `POST /v1/webhooks/stripe` handles `created`, `updated`, `deleted`, and `payment_succeeded` events to update the `users` table correctly.
- [ ] A Playwright test successfully executes an end-to-end checkout with a test card and asserts that database records are updated correctly.
