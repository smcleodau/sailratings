"""OPS-01-02 — register-driven Temporal schedule registry.

The source of truth for "what runs when" is the ``data_sources`` register.
Nothing runs that isn't registered and enabled.

Public surface:

  * :mod:`irc_data.temporal.schedules.cadence` — cadence parsing + caps
  * :mod:`irc_data.temporal.schedules.registry` — ScheduleRegistry +
    ScheduleSyncLoopWorkflow (the reconciliation loop)
"""

from irc_data.temporal.schedules.cadence import (
    DOMAIN_CONCURRENCY_CAPS,
    DEFAULT_DOMAIN_CONCURRENCY,
    cadence_to_timedelta,
    domain_for_url,
    max_concurrency_for_domain,
    schedule_id_for_slug,
    workflow_id_for_run,
)

__all__ = [
    "DOMAIN_CONCURRENCY_CAPS",
    "DEFAULT_DOMAIN_CONCURRENCY",
    "cadence_to_timedelta",
    "domain_for_url",
    "max_concurrency_for_domain",
    "schedule_id_for_slug",
    "workflow_id_for_run",
]
