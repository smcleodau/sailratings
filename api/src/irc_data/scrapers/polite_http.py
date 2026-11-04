"""Small synchronous HTTP client for the per-club result scrapers.

Enforces the collection policy in one place:

* the policy User-Agent (``ACTIVE_POLICY.attribution.user_agent``), never a
  browser one;
* at least ``min_delay`` seconds (default 2.0) plus random jitter between
  requests to the same host;
* robots.txt, fetched once per host and checked before every request;
* a block (HTTP 403/429, or a captcha / bot-check page) raises
  :class:`SourceBlockedError` instead of being retried around.
"""

from __future__ import annotations

import logging
import random
import re
import time
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

_BOT_CHECK_RE = re.compile(
    r"cf-challenge|challenge-platform|g-recaptcha|hcaptcha|Attention Required! \| Cloudflare"
    r"|Just a moment\.\.\.|Access denied",
    re.IGNORECASE,
)


class SourceBlockedError(RuntimeError):
    """The site refused us (403/429/bot check). Do not work around it."""


class RobotsDisallowedError(RuntimeError):
    """robots.txt disallows the URL for our User-Agent."""


def policy_user_agent() -> str:
    from irc_data.sources.policy import ACTIVE_POLICY

    return ACTIVE_POLICY.attribution.user_agent


class PoliteClient:
    def __init__(self, min_delay: float = 2.0, jitter: float = 1.0, timeout: float = 30.0,
                 client: httpx.Client | None = None):
        from irc_data.scrapers.base import scraper_ssl_context

        self.min_delay = max(2.0, min_delay)
        self.jitter = jitter
        self._client = client or httpx.Client(
            headers={"User-Agent": policy_user_agent()},
            timeout=timeout,
            follow_redirects=True,
            verify=scraper_ssl_context(),
        )
        self._last: dict[str, float] = {}
        self._robots: dict[str, object] = {}
        self.requests = 0

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- politeness ---------------------------------------------------------

    def _wait(self, host: str) -> None:
        last = self._last.get(host)
        delay = self.min_delay + random.uniform(0, self.jitter)
        if last is not None:
            remaining = delay - (time.monotonic() - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last[host] = time.monotonic()

    def allowed(self, url: str) -> bool:
        from irc_data.scrapers.raw_capture import is_url_allowed
        from irc_data.sources.robots import RobotsRules, parse_robots_txt

        parts = urlsplit(url)
        host = parts.netloc.lower()
        if host not in self._robots:
            self._wait(host)
            try:
                resp = self._client.get(f"{parts.scheme}://{parts.netloc}/robots.txt")
                self.requests += 1
                if resp.status_code >= 400 or "html" in resp.headers.get("content-type", ""):
                    # 4xx / an HTML error page: no robots rules published.
                    self._robots[host] = RobotsRules(no_rules=True)
                else:
                    self._robots[host] = parse_robots_txt(resp.text)
            except httpx.HTTPError as e:
                logger.warning("robots.txt fetch failed for %s: %s", host, e)
                self._robots[host] = None  # unknown: fail closed below
        rules = self._robots[host]
        if rules is None:
            return False
        return is_url_allowed(url, rules)

    def get(self, url: str, *, method: str = "GET") -> httpx.Response:
        if not self.allowed(url):
            raise RobotsDisallowedError(f"robots.txt disallows {url}")
        host = urlsplit(url).netloc.lower()
        self._wait(host)
        resp = self._client.request(method, url)
        self.requests += 1
        if resp.status_code in (403, 429):
            raise SourceBlockedError(f"HTTP {resp.status_code} from {url}")
        if method == "GET" and resp.status_code == 200 and len(resp.text) < 20000 \
                and _BOT_CHECK_RE.search(resp.text) and "sailwave" not in resp.text.lower():
            raise SourceBlockedError(f"bot check / captcha page at {url}")
        return resp
