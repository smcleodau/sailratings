"""Source framework — governed, observable data acquisition.

This package replaces the collection of bespoke scrapers with a unified
source registry, a shared adapter SDK, common acquisition primitives,
and policy enforcement.

Public API::

    from irc_data.sources import (
        DataSource,
        FetchResult,
        RawArtifactV1,
        SourceAdapter,
        assert_policy_current,
        fetch_html,
        fetch_pdf,
        fetch_json,
        fetch_file,
        paginate,
        render_page,
    )
"""

from irc_data.sources.models import DataSource, FetchResult, RawArtifactV1
from irc_data.sources.policy import (
    CURRENT_POLICY_VERSION,
    PolicyVersionMismatchError,
    SourceNotApprovedError,
    assert_policy_current,
)
from irc_data.sources.adapter import SourceAdapter
from irc_data.sources.primitives import (
    fetch_file,
    fetch_html,
    fetch_json,
    fetch_pdf,
    paginate,
    render_page,
)

__all__ = [
    "CURRENT_POLICY_VERSION",
    "DataSource",
    "FetchResult",
    "PolicyVersionMismatchError",
    "RawArtifactV1",
    "SourceAdapter",
    "SourceNotApprovedError",
    "assert_policy_current",
    "fetch_file",
    "fetch_html",
    "fetch_json",
    "fetch_pdf",
    "paginate",
    "render_page",
]
