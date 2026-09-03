"""Tests for the human-reachable alerting transports (OPS-02-03).

Under test:

* :func:`send_slack` builds the right payload for Slack vs Discord webhooks
  and is best-effort (returns False, never raises, on transport failure).
* :func:`dispatch_alert` fans one message out to every configured channel
  and keeps going when one channel is down (a single dead transport must
  never silence the page).
* :func:`ping_deadman` GETs the dead-man URL and reports success/failure.

All HTTP is intercepted via an injected ``httpx`` transport so no real
network calls happen and no secrets are required.
"""

from __future__ import annotations

import httpx

from irc_data import alerting


# ---------------------------------------------------------------------------
# httpx mocking helpers
# ---------------------------------------------------------------------------


def _mock_httpx(monkeypatch, recorder, *, fail=False, status=200):
    """Patch httpx.post / httpx.get to record calls instead of networking."""
    def fake_post(url, json=None, timeout=None, **kw):
        recorder.append(("POST", url, json))
        if fail:
            raise httpx.ConnectError("simulated network down")
        return httpx.Response(status, json={"ok": True})

    def fake_get(url, timeout=None, **kw):
        recorder.append(("GET", url, None))
        if fail:
            raise httpx.ConnectError("simulated network down")
        return httpx.Response(status)

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setattr(httpx, "get", fake_get)


# ---------------------------------------------------------------------------
# send_slack
# ---------------------------------------------------------------------------


class TestSendSlack:
    def test_slack_payload_shape(self, monkeypatch):
        calls = []
        _mock_httpx(monkeypatch, calls)
        ok = alerting.send_slack("https://hooks.slack.com/services/T/B/xxx", "hello")
        assert ok is True
        method, url, payload = calls[0]
        assert method == "POST"
        assert payload == {"text": "hello"}  # Slack shape

    def test_discord_payload_shape(self, monkeypatch):
        calls = []
        _mock_httpx(monkeypatch, calls)
        ok = alerting.send_slack("https://discord.com/api/webhooks/1/abc", "hello")
        assert ok is True
        _, _, payload = calls[0]
        assert "embeds" in payload  # Discord shape
        assert payload["embeds"][0]["description"] == "hello"

    def test_empty_url_returns_false_without_network(self, monkeypatch):
        calls = []
        _mock_httpx(monkeypatch, calls)
        assert alerting.send_slack("", "hello") is False
        assert calls == []

    def test_network_failure_is_best_effort(self, monkeypatch):
        calls = []
        _mock_httpx(monkeypatch, calls, fail=True)
        # Must not raise — returns False so the caller can try other channels.
        assert alerting.send_slack("https://hooks.slack.com/x", "hi") is False

    def test_non_2xx_returns_false(self, monkeypatch):
        calls = []
        _mock_httpx(monkeypatch, calls, status=500)
        assert alerting.send_slack("https://hooks.slack.com/x", "hi") is False


# ---------------------------------------------------------------------------
# dispatch_alert — multi-channel fan-out
# ---------------------------------------------------------------------------


class TestDispatchAlert:
    def test_fans_out_to_slack_and_email(self, monkeypatch):
        monkeypatch.setenv(alerting.SLACK_WEBHOOK_ENV, "https://hooks.slack.com/x")
        monkeypatch.setenv(alerting.RESEND_API_KEY_ENV, "rk_test")
        monkeypatch.setenv(alerting.ALERT_EMAIL_ENV, "ops@example.com")

        slack_calls, email_calls = [], []
        out = alerting.dispatch_alert(
            "subject", "body text", "<b>html</b>",
            slack_sender=lambda url, msg: slack_calls.append((url, msg)) or True,
            email_sender=lambda subj, h, t: email_calls.append((subj, h, t)),
        )
        assert out.sent is True
        assert sorted(out.channels) == ["email", "slack"]
        assert slack_calls[0][0] == "https://hooks.slack.com/x"
        assert slack_calls[0][1] == "body text"
        assert email_calls[0][0] == "subject"

    def test_slack_down_still_sends_email(self, monkeypatch):
        """A single dead transport must NOT silence the other channel."""
        monkeypatch.setenv(alerting.SLACK_WEBHOOK_ENV, "https://hooks.slack.com/x")
        monkeypatch.setenv(alerting.RESEND_API_KEY_ENV, "rk_test")
        monkeypatch.setenv(alerting.ALERT_EMAIL_ENV, "ops@example.com")

        def boom(url, msg):
            raise RuntimeError("slack unreachable")

        email_calls = []
        out = alerting.dispatch_alert(
            "s", "t",
            slack_sender=boom,
            email_sender=lambda subj, h, t: email_calls.append(subj),
        )
        assert "slack" in out.errors          # failure recorded
        assert out.channels == ["email"]      # email still delivered
        assert out.sent is True

    def test_no_channels_configured_sends_nothing(self, monkeypatch):
        for v in (alerting.SLACK_WEBHOOK_ENV, alerting.SLACK_WEBHOOK_FALLBACK_ENV,
                  alerting.RESEND_API_KEY_ENV, alerting.ALERT_EMAIL_ENV):
            monkeypatch.delenv(v, raising=False)
        out = alerting.dispatch_alert("s", "t")
        assert out.sent is False
        assert out.channels == []
        assert out.attempted == []

    def test_email_skipped_without_resend_key(self, monkeypatch):
        monkeypatch.delenv(alerting.RESEND_API_KEY_ENV, raising=False)
        monkeypatch.setenv(alerting.ALERT_EMAIL_ENV, "ops@example.com")
        email_calls = []
        out = alerting.dispatch_alert(
            "s", "t", email_sender=lambda *a: email_calls.append(a))
        assert "email" not in out.attempted   # no key → don't even attempt
        assert email_calls == []


# ---------------------------------------------------------------------------
# ping_deadman
# ---------------------------------------------------------------------------


class TestPingDeadman:
    def test_successful_ping(self, monkeypatch):
        calls = []
        _mock_httpx(monkeypatch, calls)
        assert alerting.ping_deadman("https://hc-ping.com/abc-123") is True
        method, url, _ = calls[0]
        assert method == "GET"
        assert url == "https://hc-ping.com/abc-123"

    def test_empty_url_returns_false(self, monkeypatch):
        calls = []
        _mock_httpx(monkeypatch, calls)
        assert alerting.ping_deadman("") is False
        assert calls == []

    def test_network_failure_returns_false_not_raise(self, monkeypatch):
        calls = []
        _mock_httpx(monkeypatch, calls, fail=True)
        assert alerting.ping_deadman("https://hc-ping.com/abc") is False
