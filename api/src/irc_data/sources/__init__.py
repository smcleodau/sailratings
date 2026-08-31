"""Source framework — policy, adapters, and acquisition primitives.

See SPEC-012 for the full specification.
"""

from irc_data.sources.policy import (
    CURRENT_POLICY_VERSION,
    POLICY_APPROVED_DATE,
    POLICY_AUTHORITY,
    POLICY_AUTHORITY_EMAIL,
    POLICY_USER_AGENT,
    CollectionPolicyDecisionV1,
    DataSource,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    assert_policy_current,
    classify_source,
    get_source,
    get_policy_summary,
    list_sources,
    resolve_source,
)

__all__ = [
    "CURRENT_POLICY_VERSION",
    "POLICY_APPROVED_DATE",
    "POLICY_AUTHORITY",
    "POLICY_AUTHORITY_EMAIL",
    "POLICY_USER_AGENT",
    "CollectionPolicyDecisionV1",
    "DataSource",
    "PolicyVersionMismatchError",
    "SourceNotApprovedError",
    "assert_policy_current",
    "classify_source",
    "get_source",
    "get_policy_summary",
    "list_sources",
    "resolve_source",
]
