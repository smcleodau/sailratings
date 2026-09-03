"""Human-reachable alerting transports (OPS-02-03).

Goal: *a silent 37-day outage cannot happen again.* Every alert raised by
the platform must reach a human through at least two independent channels
(Slack webhook + email), and the daily heartbeat must ping a dead-man URL
so that a *dead cron* — the failure mode that produces no output at all —
still pages someone the next morning.

This module is the single, transport-agnostic home for the three outbound
signals used across the ops tooling:

* :func:`send_slack`        — post a message to a Slack incoming webhook
  (also understands Discord webhooks, which accept a similar payload).
* :func:`send_email_alert`  — send a subject/HTML email via Resend.
* :func:`ping_deadman`      — GET a dead-man / healthchecks.io-style ping
  URL. The external monitor is configured with a grace period ending at
  09:30 UTC; if the daily ``health-check --notify`` cron is dead and no
  ping arrives by then, the *external* service raises the alarm.

Secrets (Slack webhook URL, Resend API key, alert email, dead-man URL) are
injected via environment variables. In production those variables are
populated from the 1Password vault by ``op run`` (see ``api/start-api.sh``
and ``api/crontab.txt``: ``OP_ENV="source ~/.credentials/op-service-account.env
&& op run --environment … --"``). **No secret is ever committed to the
repo** — only the *names* of the environment variables live here.

All senders are injectable so tests can capture messages without touching
the network, and every transport is *best-effort*: a failed notification
returns ``False`` / records the error but never raises into the caller's
database transaction (alert-log writes are never lost to a flaky webhook).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable

import httpx

# ---------------------------------------------------------------------------
# Environment variable names (values live in the 1Password vault, injected
# by `op run`). Documented here so the vault entries are easy to audit.
# ---------------------------------------------------------------------------

#: Slack incoming-webhook URL for ops alerts. Falls back to the generic
#: ``WEBHOOK_URL`` used by the older health-check if a dedicated Slack
#: webhook is not provisioned.
SLACK_WEBHOOK_ENV = "SLACK_WEBHOOK_URL"
SLACK_WEBHOOK_FALLBACK_ENV = "WEBHOOK_URL"

#: Resend API key + recipient for the email channel.
RESEND_API_KEY_ENV = "RESEND_API_KEY"
ALERT_EMAIL_ENV = "ALERT_EMAIL"

#: Dead-man ping URL (healthchecks.io / Cronitor / Better Uptime etc.). The
#: monitor behind this URL is configured to alert if no ping lands by
#: 09:30 UTC each day.
DEADMAN_URL_ENV = "DEADMAN_PING_URL"

DEFAULT_ALERT_EMAIL = "stuart@stuartmcleod.me"
DEFAULT_TIMEOUT = 10.0

# Sender signatures — kept simple so tests can capture calls with a lambda.
SlackSender = Callable[[str, str], bool]            # (webhook_url, text) -> ok
EmailSender = Callable[[str, str, str], None]       # (subject, html, text) -> None
DeadmanPinger = Callable[[str], bool]               # (url) -> ok


# ---------------------------------------------------------------------------
# Result container
# ---------------------------------------------------------------------------


@dataclass
class AlertDispatch:
    """Outcome of fanning one message out to every configured channel."""

    channels: list[str] = field(default_factory=list)   # e.g. ["slack", "email"]
    attempted: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def sent(self) -> bool:
        """True if at least one channel accepted the message."""
        return bool(self.channels)


# ---------------------------------------------------------------------------
# Slack (and Discord) webhook
# ---------------------------------------------------------------------------


def send_slack(webhook_url: str, text: str, *, timeout: float = DEFAULT_TIMEOUT) -> bool:
    """Post *text* to a Slack incoming webhook. Returns True on <300.

    Discord webhooks are detected by URL and get an ``embeds`` payload
    instead of the Slack ``{"text": …}`` shape, matching the convention
    already used by :mod:`irc_data.monitoring` / :mod:`irc_data.scraper_health`.
    """
    if not webhook_url:
        return False
    if "discord" in webhook_url.lower():
        payload: dict[str, Any] = {"embeds": [{"description": text, "color": 0xE01B22}]}
    else:
        payload = {"text": text}
    try:
        resp = httpx.post(webhook_url, json=payload, timeout=timeout)
        return resp.status_code < 300
    except Exception:  # noqa: BLE001 — best-effort, never raise into a txn
        return False


# ---------------------------------------------------------------------------
# Email (Resend)
# ---------------------------------------------------------------------------


def send_email_alert(
    subject: str,
    html: str,
    text: str = "",
    *,
    api_key: str | None = None,
    to_addr: str | None = None,
    from_addr: str = "SailRatings Alerts <alerts@sailratings.com>",
) -> None:
    """Send an alert email via Resend. Raises if the API key is missing.

    Raises:
        RuntimeError: if no Resend API key is configured (so the caller can
            record the channel as unavailable rather than silently no-op).
    """
    import resend

    api_key = api_key or os.environ.get(RESEND_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(f"{RESEND_API_KEY_ENV} not configured")
    to_addr = to_addr or os.environ.get(ALERT_EMAIL_ENV, DEFAULT_ALERT_EMAIL)
    resend.api_key = api_key
    resend.Emails.send({
        "from": from_addr,
        "to": [to_addr],
        "subject": subject,
        "html": html,
        "text": text or subject,
    })


# ---------------------------------------------------------------------------
# Dead-man ping
# ---------------------------------------------------------------------------


def ping_deadman(url: str, *, timeout: float = DEFAULT_TIMEOUT) -> bool:
    """GET a dead-man ping URL. Returns True on any <400 response.

    The external monitor (healthchecks.io-style) treats a *missed* ping as
    the alarm condition, so this endpoint is deliberately a plain GET that
    carries no payload — the ping itself is the "I'm alive" signal.
    """
    if not url:
        return False
    try:
        resp = httpx.get(url, timeout=timeout)
        return resp.status_code < 400
    except Exception:  # noqa: BLE001 — best-effort
        return False


# ---------------------------------------------------------------------------
# Fan-out dispatcher
# ---------------------------------------------------------------------------


def dispatch_alert(
    subject: str,
    text: str,
    html: str = "",
    *,
    slack_url: str | None = None,
    email_to: str | None = None,
    resend_key: str | None = None,
    slack_sender: SlackSender | None = None,
    email_sender: EmailSender | None = None,
) -> AlertDispatch:
    """Send *subject/text/html* to every configured channel. Best-effort.

    Channel resolution order (explicit argument → environment):
      * Slack  — ``slack_url`` → ``$SLACK_WEBHOOK_URL`` → ``$WEBHOOK_URL``
      * Email  — (``email_to`` → ``$ALERT_EMAIL_URL``→ default) *and*
                 (``resend_key`` → ``$RESEND_API_KEY``); email only fires
                 when a Resend key is present.

    ``slack_sender`` / ``email_sender`` are injectable for tests. Never
    raises: per-channel failures are recorded in ``errors``.
    """
    out = AlertDispatch()
    slack_url = slack_url or os.environ.get(SLACK_WEBHOOK_ENV) or os.environ.get(SLACK_WEBHOOK_FALLBACK_ENV)
    resend_key = resend_key or os.environ.get(RESEND_API_KEY_ENV)
    email_to = email_to or os.environ.get(ALERT_EMAIL_ENV, DEFAULT_ALERT_EMAIL)

    send_slack_fn: SlackSender = slack_sender or (lambda url, msg: send_slack(url, msg))
    send_email_fn: EmailSender = email_sender or (
        lambda subj, h, t: send_email_alert(subj, h, t, api_key=resend_key, to_addr=email_to)
    )

    if slack_url:
        out.attempted.append("slack")
        try:
            if send_slack_fn(slack_url, text):
                out.channels.append("slack")
            else:
                out.errors["slack"] = "non-2xx response"
        except Exception as e:  # noqa: BLE001
            out.errors["slack"] = f"{type(e).__name__}: {e}"

    if resend_key and email_to:
        out.attempted.append("email")
        try:
            send_email_fn(subject, html or _text_to_html(text), text)
            out.channels.append("email")
        except Exception as e:  # noqa: BLE001
            out.errors["email"] = f"{type(e).__name__}: {e}"

    return out


def _text_to_html(text: str) -> str:
    body = "<br/>".join(line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                        for line in text.splitlines())
    return (f"<div style=\"font-family:system-ui,-apple-system,sans-serif;"
            f"max-width:560px;margin:auto;color:#222\">{body}</div>")
