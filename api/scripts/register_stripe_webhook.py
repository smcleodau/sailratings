#!/usr/bin/env python3
"""Register the Stripe webhook endpoint for PAY-01-09.

Creates (or updates, matched by URL) a Stripe webhook endpoint subscribing to:

    checkout.session.completed
    customer.subscription.created
    customer.subscription.updated
    customer.subscription.deleted
    customer.subscription.paused
    customer.subscription.resumed

Run once per mode — test mode and live mode have separate endpoint spaces,
keyed off which secret key you pass:

    # Test mode
    STRIPE_SECRET_KEY=sk_test_... python api/scripts/register_stripe_webhook.py \
        --url https://api.sailratings.com/v1/checkout/webhook

    # Live mode
    STRIPE_SECRET_KEY=sk_live_... python api/scripts/register_stripe_webhook.py \
        --url https://api.sailratings.com/v1/checkout/webhook

The endpoint's signing secret is printed on creation — store it as
STRIPE_WEBHOOK_SECRET. (Stripe only returns the secret at creation time;
for an existing endpoint, roll the secret in the Dashboard.)
"""

from __future__ import annotations

import argparse
import os
import sys

import stripe

EVENTS = [
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "customer.subscription.paused",
    "customer.subscription.resumed",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--url",
        default=os.environ.get(
            "STRIPE_WEBHOOK_URL", "https://api.sailratings.com/v1/checkout/webhook"
        ),
        help="Public URL of the webhook endpoint",
    )
    parser.add_argument(
        "--mode",
        choices=["test", "live"],
        default=None,
        help="Expected key mode; aborts if STRIPE_SECRET_KEY doesn't match",
    )
    args = parser.parse_args()

    secret_key = os.environ.get("STRIPE_SECRET_KEY")
    if not secret_key:
        print("ERROR: STRIPE_SECRET_KEY is not set", file=sys.stderr)
        return 1

    key_mode = "live" if secret_key.startswith("sk_live") else "test"
    if args.mode and args.mode != key_mode:
        print(
            f"ERROR: requested --mode {args.mode} but STRIPE_SECRET_KEY is {key_mode}",
            file=sys.stderr,
        )
        return 1

    stripe.api_key = secret_key
    print(f"Registering webhook endpoint in {key_mode.upper()} mode: {args.url}")

    existing = stripe.WebhookEndpoint.list(limit=100)
    for ep in existing.auto_paging_iter():
        if ep.url == args.url:
            ep = stripe.WebhookEndpoint.modify(
                ep.id, enabled_events=EVENTS, disabled=False
            )
            print(f"Updated existing endpoint {ep.id} (status={ep.status})")
            print("Enabled events:", ", ".join(ep.enabled_events))
            print(
                "NOTE: the signing secret is only shown at creation time — "
                "roll it in the Dashboard if you don't have it."
            )
            return 0

    ep = stripe.WebhookEndpoint.create(url=args.url, enabled_events=EVENTS)
    print(f"Created endpoint {ep.id}")
    print("Enabled events:", ", ".join(ep.enabled_events))
    print(f"STRIPE_WEBHOOK_SECRET={ep.secret}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
