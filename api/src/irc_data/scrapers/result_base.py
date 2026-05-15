"""Abstract base class for race result scrapers.

All result sources implement this interface so they can be plugged into
the common discovery → scrape → normalize → match → store pipeline.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass
class EventRef:
    """A reference to a discovered event (not yet scraped)."""

    source: str
    event_name: str
    event_url: str
    event_date: date | None = None
    organizing_club: str | None = None
    event_type: str | None = None  # offshore, coastal, windward-leeward, passage
    metadata: dict | None = None


@dataclass
class NormalizedResult:
    """A single race result, normalized to common format."""

    boat_name: str
    sail_number: str | None = None

    # Event
    event_name: str = ""
    event_date: date | None = None
    event_series: str | None = None
    organizing_club: str | None = None
    event_type: str | None = None

    # Race
    race_name: str | None = None
    race_number: int | None = None
    course_distance_nm: Decimal | None = None

    # Result
    place: int | None = None
    fleet_size: int | None = None
    class_name: str | None = None
    class_place: int | None = None
    class_fleet_size: int | None = None
    status: str = "finished"

    # Rating
    rating_type: str | None = None
    rating_value: Decimal | None = None

    # Times
    elapsed_time: str | None = None
    corrected_time: str | None = None
    time_behind_winner: str | None = None

    # Source
    source_url: str | None = None
    raw_data: dict | None = None


class RaceResultSource(ABC):
    """Abstract base class for race result scrapers."""

    @abstractmethod
    async def discover_events(self, since: date | None = None) -> list[EventRef]:
        """Discover events available from this source.

        Args:
            since: Only return events after this date. If None, return all.

        Returns:
            List of EventRef objects for events that can be scraped.
        """
        ...

    @abstractmethod
    async def scrape_event(self, ref: EventRef) -> list[NormalizedResult]:
        """Scrape all race results from a single event.

        Args:
            ref: EventRef from discover_events().

        Returns:
            List of NormalizedResult objects.
        """
        ...

    @abstractmethod
    def source_name(self) -> str:
        """Return the canonical source name (e.g. 'rorc', 'yachtscoring')."""
        ...
