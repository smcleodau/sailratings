"""Source framework: policy, gate, robots parser, and adapter SDK.

This package implements DP-01-02 — the responsible-collection policy and
enforcement gate.  Every byte the platform collects must pass through the
``CollectionGate`` which asserts an approved policy version, a valid source
record, robots.txt compliance, rate limits, and collection-window rules.
"""

from irc_data.sources.policy import (
    CURRENT_POLICY_VERSION,
    CollectionPolicyDecisionV1,
    LegalStatus,
    SourceClass,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    ProhibitedCollectionError,
)
from irc_data.sources.gate import CollectionGate, GateDecision
from irc_data.sources.robots import RobotsRules, parse_robots_txt

__all__ = [
    "CURRENT_POLICY_VERSION",
    "CollectionPolicyDecisionV1",
    "LegalStatus",
    "SourceClass",
    "PolicyVersionMismatchError",
    "SourceNotApprovedError",
    "ProhibitedCollectionError",
    "CollectionGate",
    "GateDecision",
    "RobotsRules",
    "parse_robots_txt",
]
