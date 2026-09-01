"""robots.txt parser and compliance checker (DP-01-02).

A lightweight, dependency-free robots.txt parser that:

* Parses ``User-agent`` / ``Disallow`` / ``Allow`` groups.
* Supports ``Crawl-delay`` directives.
* Handles ``*`` (all user agents) and specific agents.
* Returns a ``RobotsRules`` object with ``is_allowed(url)``.

The parser is deliberately simple — it handles the directives we actually
encounter on sailing-data sites.  It does not implement the full RFC 9309
edge cases (e.g. ``$`` / ``*`` wildcards in path patterns), but it does
handle the common ``Disallow: /path`` and ``Allow: /path`` rules with
basic prefix matching and wildcard support.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence
from urllib.parse import urlparse


@dataclass
class _Rule:
    """A single allow/disallow rule with a path pattern."""

    allow: bool
    path: str


@dataclass
class RobotsRules:
    """Parsed robots.txt rules for one or more user-agents.

    Call ``is_allowed(path, user_agent)`` to check whether a URL path is
    permitted for the given user-agent.
    """

    # Mapping of user-agent (lower-case) → list of rules (in file order).
    _groups: dict[str, list[_Rule]] = field(default_factory=dict)

    # Crawl-delay per user-agent (seconds).
    crawl_delays: dict[str, float] = field(default_factory=dict)

    # True if robots.txt returned 404 (no rules → everything allowed).
    no_rules: bool = False

    # Raw text for debugging / audit.
    raw_text: str = ""

    def is_allowed(self, path: str, user_agent: str = "*") -> bool:
        """Return True if *path* may be fetched by *user_agent*.

        Resolution order (per RFC 9309 §2.2.2):
        1. Match the most specific group (exact UA, fall back to ``*``).
        2. Find the most specific matching rule (longest path prefix).
        3. If no rule matches, the path is allowed.
        """
        if self.no_rules:
            return True

        ua = user_agent.lower()
        rules = self._match_group(ua)
        if not rules:
            # No group for this UA and no * group → allowed
            return True

        # Normalise the path — ensure leading slash
        if not path.startswith("/"):
            path = "/" + path
        # Strip query string and fragment for matching
        path = path.split("?")[0].split("#")[0]

        # Find the longest-matching rule
        best_match: _Rule | None = None
        best_len = -1
        for rule in rules:
            if self._path_matches(path, rule.path):
                if len(rule.path) > best_len:
                    best_match = rule
                    best_len = len(rule.path)

        if best_match is None:
            return True  # no rule → allowed
        return best_match.allow

    def crawl_delay(self, user_agent: str = "*") -> float | None:
        """Return the crawl-delay for *user_agent*, falling back to ``*``."""
        ua = user_agent.lower()
        if ua in self.crawl_delays:
            return self.crawl_delays[ua]
        return self.crawl_delays.get("*")

    def disallow_paths(self, user_agent: str = "*") -> list[str]:
        """Return all disallowed paths for *user_agent* (for caching)."""
        ua = user_agent.lower()
        rules = self._match_group(ua)
        return [r.path for r in rules if not r.allow and r.path != ""]

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _match_group(self, user_agent: str) -> list[_Rule]:
        """Return rules for the most specific matching user-agent group."""
        if user_agent in self._groups:
            return self._groups[user_agent]
        if "*" in self._groups:
            return self._groups["*"]
        return []

    @staticmethod
    def _path_matches(path: str, pattern: str) -> bool:
        """Check if *path* matches a robots *pattern*.

        Supports:
        * Empty pattern → matches nothing (used for ``Disallow:`` with no value)
        * ``$`` end-of-path anchor
        * ``*`` wildcard (matches any sequence)
        * Plain prefix matching (default)
        """
        if not pattern:
            return False

        # Handle wildcard patterns
        if "*" in pattern or "$" in pattern:
            return RobotsRules._wildcard_match(path, pattern)

        # Plain prefix match
        return path.startswith(pattern)

    @staticmethod
    def _wildcard_match(path: str, pattern: str) -> bool:
        """Match *path* against a pattern containing ``*`` and ``$``."""
        import re

        # Convert robots pattern to regex
        # Escape regex special chars except * and $
        regex_parts = []
        for part in pattern.split("*"):
            if part.endswith("$"):
                regex_parts.append(re.escape(part[:-1]) + "$")
            else:
                regex_parts.append(re.escape(part))
        regex_str = ".*".join(regex_parts)
        return bool(re.match("^" + regex_str, path))


def parse_robots_txt(text: str, user_agent: str | None = None) -> RobotsRules:
    """Parse a robots.txt response body into a ``RobotsRules`` object.

    Parameters
    ----------
    text
        The raw robots.txt body.
    user_agent
        If provided, only rules for this agent (and ``*``) are kept.
        This reduces memory for single-agent checks.

    A 404 response (empty text or None) returns a ``RobotsRules`` with
    ``no_rules=True``, meaning everything is allowed.
    """
    if not text:
        return RobotsRules(no_rules=True)

    rules = RobotsRules(raw_text=text)

    current_agents: list[str] = []
    current_rules: list[_Rule] = []

    for line in text.splitlines():
        # Strip comments and whitespace
        line = line.split("#")[0].strip()
        if not line:
            continue

        if ":" not in line:
            continue

        field_name, _, value = line.partition(":")
        field_name = field_name.strip().lower()
        value = value.strip()

        if field_name == "user-agent":
            # If we were accumulating rules, flush them
            if current_rules and current_agents:
                for ua in current_agents:
                    rules._groups.setdefault(ua, []).extend(current_rules)
            current_agents = [value.lower()]
            current_rules = []
        elif field_name in ("disallow", "allow"):
            if not current_agents:
                continue
            current_rules.append(_Rule(allow=(field_name == "allow"), path=value))
        elif field_name == "crawl-delay":
            if not current_agents:
                continue
            try:
                delay = float(value)
                for ua in current_agents:
                    rules.crawl_delays[ua] = delay
            except ValueError:
                pass

    # Flush the last group
    if current_rules and current_agents:
        for ua in current_agents:
            rules._groups.setdefault(ua, []).extend(current_rules)

    # If user_agent filter is requested, prune
    if user_agent:
        ua_lower = user_agent.lower()
        pruned: dict[str, list[_Rule]] = {}
        if ua_lower in rules._groups:
            pruned[ua_lower] = rules._groups[ua_lower]
        if "*" in rules._groups:
            pruned["*"] = rules._groups["*"]
        rules._groups = pruned

    return rules


def check_url_against_robots(
    url: str,
    rules: RobotsRules,
    user_agent: str = "*",
) -> bool:
    """Convenience: return True if *url* is allowed by *rules*."""
    path = urlparse(url).path or "/"
    return rules.is_allowed(path, user_agent)
