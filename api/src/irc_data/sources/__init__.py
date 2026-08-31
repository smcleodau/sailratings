"""Governed global Data Source Register (DP-01-01).

Every collection job must resolve an approved source record and a policy
decision before fetching. See SPEC-012 §2 and docs/INTERIM-POLICY.md.
"""

from irc_data.sources.models import DataSourceRecordV1
from irc_data.sources.registry import (
    CURRENT_POLICY_VERSION,
    LEGAL_STATUSES,
    DataSource,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    assert_approved,
    assert_policy_current,
    can_collect,
    can_discover,
    get_source,
    get_source_record,
    list_sources,
    resolve_and_assert_approved,
    seed_sources,
)

__all__ = [
    "CURRENT_POLICY_VERSION",
    "DataSource",
    "DataSourceRecordV1",
    "LEGAL_STATUSES",
    "PolicyVersionMismatchError",
    "SourceNotApprovedError",
    "assert_approved",
    "assert_policy_current",
    "can_collect",
    "can_discover",
    "get_source",
    "get_source_record",
    "list_sources",
    "resolve_and_assert_approved",
    "seed_sources",
]
