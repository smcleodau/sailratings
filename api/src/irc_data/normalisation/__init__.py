"""Units, names, dates and rating normalisation library (DP-03-03).

Normalise common values **consistently and transparently**:

* :class:`NormalisedValueV1` — the handoff / output contract.  Every
  result retains the original representation and the rule + rule-set
  version that produced it.
* **Quarantine, never guess** — ambiguous conversions
  (:class:`AmbiguousNormalisationError` / ``kind="quarantined"``) rather
  than silent guesses.
* Unit conversion (lengths → m, weights → kg, elapsed times → s,
  ratings → 3-dp decimal).
* Unicode / name cleanup, sail numbers, country codes (ISO alpha-3).
* Dates & timezones with explicit race-level granularity (absorbs the
  old-tracker *Race Level Granularity and Timezones* epic).
* Rating-system version normalisation (IRC/ORC/… rule years).
* Explicit missing-value semantics (:data:`MISSING_TOKENS`).
"""

from irc_data.normalisation.normalise import (
    AMBIGUOUS_POLICIES,
    MISSING_TOKENS,
    RULES_VERSION,
    AmbiguousNormalisationError,
    NormalisationError,
    NormalisationKind,
    NormalisedValueV1,
    Rule,
    is_missing_token,
)
from irc_data.normalisation.units import (
    UnitAssumption,
    normalise_dimensionless,
    normalise_elapsed_time,
    normalise_length,
    normalise_rating,
    normalise_weight,
    parse_decimal,
    parse_elapsed_seconds,
)
from irc_data.normalisation.names import (
    clean_name,
    country_code_or_none,
    normalise_country_code,
    normalise_name,
    normalise_sail_number,
    split_sail_number,
)
from irc_data.normalisation.dates import (
    Granularity,
    NAMED_TIMEZONES,
    load_timezone,
    normalise_date,
    normalise_datetime,
    race_start_instant,
)
from irc_data.normalisation.ratings import normalise_rating_system_version

__all__ = [
    # contract
    "RULES_VERSION",
    "MISSING_TOKENS",
    "AMBIGUOUS_POLICIES",
    "NormalisationKind",
    "NormalisedValueV1",
    "Rule",
    "NormalisationError",
    "AmbiguousNormalisationError",
    "is_missing_token",
    # units
    "UnitAssumption",
    "parse_decimal",
    "parse_elapsed_seconds",
    "normalise_length",
    "normalise_weight",
    "normalise_elapsed_time",
    "normalise_rating",
    "normalise_dimensionless",
    # names / codes
    "clean_name",
    "normalise_name",
    "normalise_sail_number",
    "split_sail_number",
    "normalise_country_code",
    "country_code_or_none",
    # dates / timezones
    "Granularity",
    "NAMED_TIMEZONES",
    "load_timezone",
    "normalise_date",
    "normalise_datetime",
    "race_start_instant",
    # rating systems
    "normalise_rating_system_version",
]
