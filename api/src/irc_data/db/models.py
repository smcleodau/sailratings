"""SQLAlchemy ORM models for IRC/ORC sailing data."""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Interval,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Core boat table
# ---------------------------------------------------------------------------


class Boat(Base):
    __tablename__ = "boats"
    __table_args__ = (UniqueConstraint("sail_number", "cert_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    boat_name: Mapped[str] = mapped_column(Text, nullable=False)
    sail_number: Mapped[str] = mapped_column(Text, nullable=False)
    cert_number: Mapped[str | None] = mapped_column(Text)
    design: Mapped[str | None] = mapped_column(Text)
    country: Mapped[str | None] = mapped_column(Text)
    year_built: Mapped[int | None] = mapped_column(Integer)

    # New columns for richer boat data
    hull_id: Mapped[str | None] = mapped_column(Text)
    builder: Mapped[str | None] = mapped_column(Text)
    designer: Mapped[str | None] = mapped_column(Text)
    design_canonical: Mapped[str | None] = mapped_column(Text)
    # current_name / current_sail_number / current_flag dropped in migration
    # 0014 — never written, 100% NULL across all rows. boat_identities is
    # the source of truth for historical name/sail/owner observations.
    loa: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    lwl: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    beam_max: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    displacement_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    snapshots: Mapped[list["TCCSnapshotModel"]] = relationship(back_populates="boat")
    certificates: Mapped[list["Certificate"]] = relationship(back_populates="boat")
    race_results: Mapped[list["RaceResultModel"]] = relationship(
        secondary="event_entries",
        primaryjoin="Boat.id == EventEntry.boat_id",
        secondaryjoin="EventEntry.id == RaceResultModel.event_entry_id",
        viewonly=True,
    )
    orc_certificates: Mapped[list["ORCCertificate"]] = relationship(back_populates="boat")
    identities: Mapped[list["BoatIdentity"]] = relationship(back_populates="boat")


# ---------------------------------------------------------------------------
# IRC TCC snapshots
# ---------------------------------------------------------------------------


class TCCSnapshotModel(Base):
    __tablename__ = "tcc_snapshots"
    __table_args__ = (UniqueConstraint("boat_id", "snapshot_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    boat_id: Mapped[int] = mapped_column(ForeignKey("boats.id"))
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    cert_year: Mapped[int | None] = mapped_column(Integer)
    tcc: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    non_spi_tcc: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    endorsed: Mapped[str | None] = mapped_column(Text)
    secondary: Mapped[str | None] = mapped_column(Text)
    crew: Mapped[int | None] = mapped_column(Integer)
    dlr: Mapped[int | None] = mapped_column(Integer)
    lh: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    beam: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    draft: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    single_furling_headsail: Mapped[str | None] = mapped_column(Text)
    headsails: Mapped[int | None] = mapped_column(Integer)
    flying_headsails: Mapped[int | None] = mapped_column(Integer)
    spinnakers: Mapped[int | None] = mapped_column(Integer)
    series_date: Mapped[int | None] = mapped_column(Integer)
    age_date: Mapped[int | None] = mapped_column(Integer)
    racing_area: Mapped[int | None] = mapped_column(Integer)
    ssb_base_value: Mapped[int | None] = mapped_column(Integer)
    stix: Mapped[int | None] = mapped_column(Integer)
    avs: Mapped[int | None] = mapped_column(Integer)
    category: Mapped[str | None] = mapped_column(Text)

    boat: Mapped["Boat"] = relationship(back_populates="snapshots")


# ---------------------------------------------------------------------------
# IRC certificates (parsed from PDF)
# ---------------------------------------------------------------------------


class Certificate(Base):
    # Renamed in migration 0012 — IRC-specific certs sit in `irc_certificates`
    # alongside `orc_certificates`. The Python class name stays `Certificate` to
    # minimise import churn; the table is what's renamed.
    __tablename__ = "irc_certificates"
    __table_args__ = (UniqueConstraint("cert_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    boat_id: Mapped[int | None] = mapped_column(ForeignKey("boats.id"))
    cert_number: Mapped[str | None] = mapped_column(Text)
    issue_date: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    lh: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    beam: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    draft: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    # Renamed + widened in migration 0014. The bare `displacement` name was
    # ambiguous next to `orc_certificates.displacement`; the `_kg` suffix
    # matches `boats.displacement_kg` and the other displacement columns.
    displacement_kg: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    bo: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    so: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    p: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    e: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    j: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    fl: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    stl: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    spl: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    rig_type: Mapped[str | None] = mapped_column(Text)
    mast_material: Mapped[str | None] = mapped_column(Text)
    spreaders: Mapped[int | None] = mapped_column(Integer)
    muw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    mtw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    mhw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    hlu: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    hlp: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    hhw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    htw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    huw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    sym_slu: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    sym_sle: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    sym_sf: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    sym_shw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    asym_slu: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    asym_sle: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    asym_sf: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    asym_shw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    water_ballast: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    stix_val: Mapped[Decimal | None] = mapped_column("stix", Numeric(6, 1))
    avs_val: Mapped[Decimal | None] = mapped_column("avs", Numeric(6, 1))
    design_category: Mapped[str | None] = mapped_column(Text)

    # Extended measurements (0007 migration)
    lwp: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    dlr: Mapped[int | None] = mapped_column(Integer)
    x: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    y: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    internal_ballast: Mapped[Decimal | None] = mapped_column(Numeric(8, 1))
    hsa: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    headsails_max: Mapped[int | None] = mapped_column(Integer)
    flying_headsails_max: Mapped[int | None] = mapped_column(Integer)
    fsa: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    flu: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    flp: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    fuw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    ftw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    fhw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    fsfl: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    fshw: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    spa: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    spinnakers_max: Mapped[int | None] = mapped_column(Integer)
    stl_fh_max: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    aft_rigging: Mapped[int | None] = mapped_column(Integer)

    raw_data: Mapped[dict | None] = mapped_column(JSON)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    boat: Mapped["Boat | None"] = relationship(back_populates="certificates")


# ---------------------------------------------------------------------------
# Race results (evolved)
# ---------------------------------------------------------------------------


class RaceResultModel(Base):
    __tablename__ = "race_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Legacy identity columns (match the live DB and every scraper caller).
    # OPS-02-02: HEAD had dropped these in favour of an event_entry-only shape,
    # which silently broke every race-result upsert (KeyError 'organizing_club')
    # because the live table still carries these columns and callers pass them.
    boat_id: Mapped[int | None] = mapped_column(ForeignKey("boats.id"))
    event_name: Mapped[str] = mapped_column(Text, nullable=False)
    event_date: Mapped[date | None] = mapped_column(Date)
    event_series: Mapped[str | None] = mapped_column(Text)
    organizing_club: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str | None] = mapped_column(Text)
    # Optional link into the normalised event model. The live column is
    # nullable in practice (legacy rows); keep the ORM optional so scraper
    # upserts that don't mint an EventEntry still persist.
    event_entry_id: Mapped[int | None] = mapped_column(ForeignKey("event_entries.id"))
    # Race info
    race_name: Mapped[str | None] = mapped_column(Text)
    race_date_specific: Mapped[date | None] = mapped_column(Date)
    race_number: Mapped[int | None] = mapped_column(Integer)
    course_distance_nm: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    # Result
    place: Mapped[int | None] = mapped_column(Integer)
    fleet_size: Mapped[int | None] = mapped_column(Integer)
    class_name: Mapped[str | None] = mapped_column(Text)
    class_place: Mapped[int | None] = mapped_column(Integer)
    class_fleet_size: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(Text, server_default="finished")
    # Rating at race
    rating_type: Mapped[str | None] = mapped_column(Text)
    rating_value: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    # Legacy column kept for backward compat with existing data
    tcc_at_race: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    division: Mapped[str | None] = mapped_column(Text)
    # Times
    elapsed_time: Mapped[str | None] = mapped_column(Interval)
    corrected_time: Mapped[str | None] = mapped_column(Interval)
    time_behind_winner: Mapped[str | None] = mapped_column(Interval)
    # Source
    source: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    # Which ingestion path produced this row. 'legacy' = bespoke per-source
    # scraper; 'firecrawl' = Firecrawl + Claude extractor pipeline. NULL on
    # pre-migration rows. See alembic 0019.
    transport: Mapped[str | None] = mapped_column(String(32))
    raw_data: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    event_entry: Mapped["EventEntry"] = relationship(back_populates="race_results")


# ---------------------------------------------------------------------------
# ORC certificates
# ---------------------------------------------------------------------------


class ORCCertificate(Base):
    __tablename__ = "orc_certificates"
    __table_args__ = (
        UniqueConstraint("ref_no", "country_id", "snapshot_date"),
        Index("idx_orc_boat", "boat_id"),
        Index("idx_orc_country", "country_id"),
        Index("idx_orc_snapshot_date", "snapshot_date"),
        Index("idx_orc_sail_number", "sail_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    boat_id: Mapped[int | None] = mapped_column(ForeignKey("boats.id"))
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)

    # ORC identifiers
    ref_no: Mapped[str] = mapped_column(Text, nullable=False)
    country_id: Mapped[str] = mapped_column(Text, nullable=False)
    yacht_name: Mapped[str | None] = mapped_column(Text)
    sail_no: Mapped[str | None] = mapped_column(Text)
    owner_name: Mapped[str | None] = mapped_column(Text)
    class_name: Mapped[str | None] = mapped_column(Text)
    builder: Mapped[str | None] = mapped_column(Text)
    designer: Mapped[str | None] = mapped_column(Text)
    year_built: Mapped[int | None] = mapped_column(Integer)

    # Key ratings
    gph: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    osn: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    cdl: Mapped[Decimal | None] = mapped_column(Numeric(8, 3))
    triple_low: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    triple_med: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    triple_high: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))

    # Key dimensions
    loa: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    displacement: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    draft: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    sail_area_upwind: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    sail_area_downwind: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    stability_index: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))

    # VPP polars + key dimensions promoted from raw_data in migration 0013.
    # `allowances` is the full polar table (beat/reach/run at multiple wind
    # speeds, CR + WL courses); the rest are scalar performance fields needed
    # for design-compare, no-spin scoring, and IRC<->ORC cross-rating.
    allowances: Mapped[dict | None] = mapped_column(JSONB)
    dynamic_allowance: Mapped[Decimal | None] = mapped_column(Numeric(5, 3))
    dspl_sailing: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    imsl: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    mb: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    aphd: Mapped[Decimal | None] = mapped_column(Numeric(6, 3))
    apht: Mapped[str | None] = mapped_column(Text)
    wss: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    tmf_offshore: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    tmf_inshore: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))

    # Full JSON blob for everything we don't extract
    raw_data: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    boat: Mapped["Boat | None"] = relationship(back_populates="orc_certificates")


# ---------------------------------------------------------------------------
# ORC snapshot tracking (per-country download metadata)
# ---------------------------------------------------------------------------


class ORCSnapshot(Base):
    __tablename__ = "orc_snapshots"
    __table_args__ = (
        UniqueConstraint("country_id", "snapshot_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    country_id: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    records_found: Mapped[int | None] = mapped_column(Integer)
    raw_json_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Boat identity tracking (name/sail/owner changes over time)
# ---------------------------------------------------------------------------


class BoatIdentity(Base):
    __tablename__ = "boat_identities"
    __table_args__ = (
        Index("idx_identity_boat", "boat_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    boat_id: Mapped[int] = mapped_column(ForeignKey("boats.id"), nullable=False)
    boat_name: Mapped[str | None] = mapped_column(Text)
    sail_number: Mapped[str | None] = mapped_column(Text)
    owner: Mapped[str | None] = mapped_column(Text)
    flag: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    observed_date: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    boat: Mapped["Boat"] = relationship(back_populates="identities")


# ---------------------------------------------------------------------------
# Design classes (canonical designs with aliases)
# ---------------------------------------------------------------------------


class DesignClass(Base):
    __tablename__ = "design_classes"
    __table_args__ = (
        UniqueConstraint("name_canonical"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name_canonical: Mapped[str] = mapped_column(Text, nullable=False)
    aliases: Mapped[dict | None] = mapped_column(JSON)  # ["Sun Fast 3300", "SF3300"]
    builder: Mapped[str | None] = mapped_column(Text)
    designer: Mapped[str | None] = mapped_column(Text)
    nominal_loa: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    nominal_lwl: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    nominal_beam: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    nominal_draft: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    nominal_displacement: Mapped[Decimal | None] = mapped_column(Numeric(10, 1))
    year_first: Mapped[int | None] = mapped_column(Integer)
    year_last: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Ingestion log (scraper run tracking)
# ---------------------------------------------------------------------------


class IngestionLog(Base):
    __tablename__ = "ingestion_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, server_default="running")
    records_found: Mapped[int | None] = mapped_column(Integer)
    records_new: Mapped[int | None] = mapped_column(Integer)
    records_updated: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON)


# ---------------------------------------------------------------------------
# Insight cache (LLM response caching)
# ---------------------------------------------------------------------------


class InsightCache(Base):
    __tablename__ = "insight_cache"

    id: Mapped[int] = mapped_column(primary_key=True)
    boat_id: Mapped[int | None] = mapped_column(ForeignKey("boats.id"))
    query: Mapped[str] = mapped_column(Text, nullable=False)
    rendered_context: Mapped[str | None] = mapped_column(Text)
    response: Mapped[str | None] = mapped_column(Text)
    model: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------------------
# Cert probe attempts (existing helper table)
# ---------------------------------------------------------------------------


class CertProbeAttempt(Base):
    __tablename__ = "cert_probe_attempts"
    __table_args__ = (
        UniqueConstraint("cert_number_tried", "sail_number"),
        Index("idx_probe_boat", "boat_name", "sail_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    boat_name: Mapped[str] = mapped_column(Text, nullable=False)
    sail_number: Mapped[str] = mapped_column(Text, nullable=False)
    cert_number_tried: Mapped[str] = mapped_column(Text, nullable=False)
    found: Mapped[bool | None] = mapped_column(server_default="false")
    probed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


# ---------------------------------------------------------------------------
# Orders (report purchases)
# ---------------------------------------------------------------------------


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("idx_orders_boat", "boat_id"),
        Index("idx_orders_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    order_token: Mapped[uuid.UUID] = mapped_column(
        Uuid, unique=True, nullable=False, default=uuid.uuid4
    )
    boat_id: Mapped[int] = mapped_column(ForeignKey("boats.id"), nullable=False)
    email: Mapped[str | None] = mapped_column(Text)
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="usd")
    stripe_session_id: Mapped[str | None] = mapped_column(Text, unique=True)
    stripe_payment_intent: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    stripe_payment_status: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="pending")
    report_markdown: Mapped[str | None] = mapped_column(Text)
    report_analytics: Mapped[dict | None] = mapped_column(JSON)
    pdf_path: Mapped[str | None] = mapped_column(Text)
    search_query: Mapped[str | None] = mapped_column(Text)
    teaser_text: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    report_generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    email_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    boat: Mapped["Boat"] = relationship()


# ---------------------------------------------------------------------------
# Payments: users, subscriptions, stripe webhook idempotency (PAY-01-09)
# ---------------------------------------------------------------------------


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        Index("idx_users_email", "email"),
        Index("idx_users_stripe_customer", "stripe_customer_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    clerk_id: Mapped[str | None] = mapped_column(Text, unique=True)
    email: Mapped[str | None] = mapped_column(Text, unique=True)
    full_name: Mapped[str | None] = mapped_column(Text)
    subscription_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="none"
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("idx_subscriptions_user", "user_id"),
        Index("idx_subscriptions_customer", "stripe_customer_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    stripe_subscription_id: Mapped[str] = mapped_column(
        Text, unique=True, nullable=False
    )
    stripe_customer_id: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str | None] = mapped_column(Text)
    plan: Mapped[str | None] = mapped_column(Text)
    lookup_key: Mapped[str | None] = mapped_column(Text)
    price_id: Mapped[str | None] = mapped_column(Text)
    current_period_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    current_period_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    cancel_at_period_end: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    canceled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped["User | None"] = relationship()


class StripeEvent(Base):
    __tablename__ = "stripe_events"
    __table_args__ = (
        Index("idx_stripe_events_type", "type"),
        Index("idx_stripe_events_error", "error"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    type: Mapped[str | None] = mapped_column(Text)
    api_version: Mapped[str | None] = mapped_column(Text)
    livemode: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    venue: Mapped[str | None] = mapped_column(Text)
    course_type: Mapped[str | None] = mapped_column(Text)
    organiser: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    entries: Mapped[list["EventEntry"]] = relationship(back_populates="event")


class EventEntry(Base):
    __tablename__ = "event_entries"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("events.id"), nullable=False)
    boat_id: Mapped[int | None] = mapped_column(ForeignKey("boats.id"))
    sail_number: Mapped[str | None] = mapped_column(Text)
    boat_name: Mapped[str | None] = mapped_column(Text)
    tcc: Mapped[Decimal | None] = mapped_column(Numeric(5, 3))
    design: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    event: Mapped["Event"] = relationship(back_populates="entries")
    boat: Mapped["Boat"] = relationship()
    race_results: Mapped[list["RaceResultModel"]] = relationship(back_populates="event_entry")


class BoatEvent(Base):
    __tablename__ = "boat_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    boat_id: Mapped[int] = mapped_column(ForeignKey("boats.id"), nullable=False)
    event_type: Mapped[str | None] = mapped_column(Text)
    event_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    boat: Mapped["Boat"] = relationship()


# ---------------------------------------------------------------------------
# Source monitor — change & breakage detection (DP-01-05 / SPEC-012 §6)
#
# Baselines store the last-known-good fingerprint per (source_id, url).
# Health events record the outcome of every comparison check_source() runs.
# Incidents are opened on material deviations and carry representative
# artifacts. Publication quarantines block downstream publishing while an
# incident is open.
# ---------------------------------------------------------------------------


class SourceBaseline(Base):
    __tablename__ = "source_baselines"
    __table_args__ = (
        UniqueConstraint("source_id", "url"),
        Index("ix_source_baselines_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    fetch_success: Mapped[bool] = mapped_column(server_default="true")
    http_status: Mapped[int | None] = mapped_column(Integer)
    content_type: Mapped[str | None] = mapped_column(Text)
    content_hash: Mapped[str | None] = mapped_column(Text)
    structure_signature: Mapped[str | None] = mapped_column(Text)
    record_count: Mapped[int | None] = mapped_column(Integer)
    parser_yield: Mapped[int | None] = mapped_column(Integer)
    content_length: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SourceIncident(Base):
    __tablename__ = "source_incidents"
    __table_args__ = (
        Index("ix_source_incidents_source_id", "source_id"),
        Index("ix_source_incidents_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    incident_type: Mapped[str] = mapped_column(Text, nullable=False)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="open")
    deviations: Mapped[list | None] = mapped_column(JSON)
    sample_records: Mapped[list | None] = mapped_column(JSON)
    content_excerpt: Mapped[str | None] = mapped_column(Text)
    previous_hash: Mapped[str | None] = mapped_column(Text)
    current_hash: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class SourceHealthEvent(Base):
    __tablename__ = "source_health_events"
    __table_args__ = (
        Index("ix_source_health_events_source_id", "source_id"),
        Index("ix_source_health_events_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    material: Mapped[bool] = mapped_column(server_default="false")
    deviations: Mapped[list | None] = mapped_column(JSON)
    diff_ratio: Mapped[float | None] = mapped_column(Numeric(8, 4))
    baseline_hash: Mapped[str | None] = mapped_column(Text)
    current_hash: Mapped[str | None] = mapped_column(Text)
    incident_id: Mapped[int | None] = mapped_column(Integer)
    quarantined: Mapped[bool] = mapped_column(server_default="false")
    event_payload: Mapped[dict | None] = mapped_column(JSON)


class PublicationQuarantine(Base):
    __tablename__ = "publication_quarantine"
    __table_args__ = (
        UniqueConstraint("source_id"),
        Index("ix_publication_quarantine_source_id", "source_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    incident_id: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default="active")


# ---------------------------------------------------------------------------
# Replay / backfill — isolated batch, comparison, promotion (DP-02-04)
#
# replay_batches: one row per replay plan, keyed by plan_id (idempotency).
# replay_artifacts: one row per parsed artifact within a batch.  Stores
#   both the new parsed output and the old published output for
#   comparison.  Separate from the published store — no in-place rewrite.
# publication_receipts: one row per explicit promotion.  Records the
#   promoted batch, the old batch (retained), and a receipt_id for audit.
# ---------------------------------------------------------------------------


class ReplayBatch(Base):
    __tablename__ = "replay_batches"
    __table_args__ = (
        UniqueConstraint("plan_id"),
        Index("ix_replay_batches_plan_id", "plan_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    plan_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_slug: Mapped[str] = mapped_column(Text, nullable=False)
    parser_version: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pending"
    )
    artifact_filter: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    promoted_by: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class ReplayArtifact(Base):
    __tablename__ = "replay_artifacts"
    __table_args__ = (
        Index("ix_replay_artifacts_batch_id", "batch_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("replay_batches.id"))
    artifact_url: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(Text)
    parsed_output: Mapped[dict | None] = mapped_column(JSON)
    old_parsed_output: Mapped[dict | None] = mapped_column(JSON)
    parse_status: Mapped[str] = mapped_column(
        Text, server_default="pending"
    )
    parse_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class PublicationReceipt(Base):
    __tablename__ = "publication_receipts"
    __table_args__ = (
        UniqueConstraint("receipt_id"),
        Index("ix_publication_receipts_batch_id", "batch_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[str] = mapped_column(Text, nullable=False)
    batch_id: Mapped[int] = mapped_column(Integer, nullable=False)
    plan_id: Mapped[str] = mapped_column(Text, nullable=False)
    source_slug: Mapped[str] = mapped_column(Text, nullable=False)
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    old_batch_id: Mapped[int | None] = mapped_column(Integer)
    old_retained: Mapped[bool] = mapped_column(server_default="true")
    artifact_count: Mapped[int] = mapped_column(
        Integer, server_default="0"
    )
    promoted_by: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(
        Text, server_default="v1"
    )


# ---------------------------------------------------------------------------
# Reconciliation & silent-loss detection (DP-05-03)
#
# pipeline_count_baseline: one row per pipeline run per source — the
#   trailing yield series used to detect abrupt yield change.
# reconciliation_reports: one row per reconcile_run() verdict — variance,
#   yield, decision, block reason.  ``decision = 'block'`` rows are the
#   promotion-blocking signal.
# ---------------------------------------------------------------------------


class PipelineCountBaseline(Base):
    __tablename__ = "pipeline_count_baseline"
    __table_args__ = (
        Index("ix_pcb_source_recorded", "source_id", "recorded_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    discovered: Mapped[int] = mapped_column(Integer, server_default="0")
    fetched: Mapped[int] = mapped_column(Integer, server_default="0")
    parsed: Mapped[int] = mapped_column(Integer, server_default="0")
    transformed: Mapped[int] = mapped_column(Integer, server_default="0")
    rejected: Mapped[int] = mapped_column(Integer, server_default="0")
    quarantined: Mapped[int] = mapped_column(Integer, server_default="0")
    published: Mapped[int] = mapped_column(Integer, server_default="0")
    duplicate_suppressed: Mapped[int] = mapped_column(Integer, server_default="0")
    yield_ratio: Mapped[float | None] = mapped_column(Numeric(8, 4))
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ReconciliationReport(Base):
    __tablename__ = "reconciliation_reports"
    __table_args__ = (
        UniqueConstraint("report_id"),
        Index("ix_recon_reports_source", "source_id", "checked_at"),
        Index("ix_recon_reports_decision", "decision"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[str] = mapped_column(Text, nullable=False)
    run_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[str] = mapped_column(Text, nullable=False)
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    counts: Mapped[dict | None] = mapped_column(JSON)
    variance: Mapped[int] = mapped_column(Integer, server_default="0")
    variance_explained: Mapped[bool] = mapped_column(server_default="true")
    unexplained_reasons: Mapped[dict | None] = mapped_column(JSON)
    yield_ratio: Mapped[float | None] = mapped_column(Numeric(8, 4))
    baseline_yield_p10: Mapped[float | None] = mapped_column(Numeric(8, 4))
    baseline_yield_p50: Mapped[float | None] = mapped_column(Numeric(8, 4))
    abrupt_yield_change: Mapped[bool] = mapped_column(server_default="false")
    decision: Mapped[str] = mapped_column(Text, nullable=False, server_default="allow")
    promotion_allowed: Mapped[bool] = mapped_column(server_default="true")
    block_reason: Mapped[str | None] = mapped_column(Text)
    schema_version: Mapped[str] = mapped_column(Text, server_default="v1")


# ---------------------------------------------------------------------------
# Validation, quarantine and promotion gates (DP-05-02)
#
# quality_batches:      one row per batch version; (pipeline, source_slug,
#                       version) unique so a retry/replay always lands in a
#                       fresh version.
# quality_batch_rows:   staged payload rows for a batch — never read by
#                       consumers directly; the consumer view joins on
#                       promoted batches only.
# quality_quarantine:   one row per quarantined batch — rule failures (with
#                       samples) + a bounded sample of staged rows.
# quality_verdicts:     one row per validation run (full report).
# quality_promotions:   one row per explicit promotion — the only transition
#                       that changes consumer-visible state, applied in a
#                       single transaction (partial publication cannot occur).
# ---------------------------------------------------------------------------


class QualityBatch(Base):
    __tablename__ = "quality_batches"
    __table_args__ = (
        UniqueConstraint(
            "pipeline", "source_slug", "version",
            name="uq_quality_batches_pipeline_source_version",
        ),
        Index("ix_quality_batches_pipeline_source", "pipeline", "source_slug"),
        Index("ix_quality_batches_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    pipeline: Mapped[str] = mapped_column(Text, nullable=False)
    source_slug: Mapped[str] = mapped_column(Text, nullable=False)
    gate: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="pending"
    )
    record_count: Mapped[int] = mapped_column(Integer, server_default="0")
    content_hash: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict | None] = mapped_column("metadata", JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    promoted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    promoted_by: Mapped[str | None] = mapped_column(Text)


class QualityBatchRow(Base):
    __tablename__ = "quality_batch_rows"
    __table_args__ = (
        Index("ix_quality_batch_rows_batch_key", "batch_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    batch_key: Mapped[str] = mapped_column(
        ForeignKey("quality_batches.batch_key"), nullable=False
    )
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)
    row_kind: Mapped[str] = mapped_column(Text, nullable=False)
    row_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class QualityQuarantine(Base):
    __tablename__ = "quality_quarantine"
    __table_args__ = (
        Index("ix_quality_quarantine_batch_key", "batch_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    quarantine_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    batch_key: Mapped[str] = mapped_column(Text, nullable=False)
    pipeline: Mapped[str] = mapped_column(Text, nullable=False)
    source_slug: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    gate: Mapped[str] = mapped_column(Text, nullable=False)
    rule_classes: Mapped[list | None] = mapped_column(JSON)
    failures: Mapped[list | None] = mapped_column(JSON)
    sample_rows: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default="open"
    )
    quarantined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    resolution: Mapped[str | None] = mapped_column(Text)


class QualityVerdict(Base):
    __tablename__ = "quality_verdicts"
    __table_args__ = (
        Index("ix_quality_verdicts_batch_key", "batch_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    verdict_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    batch_key: Mapped[str] = mapped_column(Text, nullable=False)
    pipeline: Mapped[str] = mapped_column(Text, nullable=False)
    source_slug: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    gate: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    rules_evaluated: Mapped[int] = mapped_column(Integer, server_default="0")
    rules_failed: Mapped[int] = mapped_column(Integer, server_default="0")
    failures: Mapped[list | None] = mapped_column(JSON)
    record_count: Mapped[int] = mapped_column(Integer, server_default="0")
    evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class QualityPromotion(Base):
    __tablename__ = "quality_promotions"
    __table_args__ = (
        Index("ix_quality_promotions_batch_key", "batch_key"),
        Index(
            "ix_quality_promotions_pipeline_source", "pipeline", "source_slug"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    receipt_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    batch_key: Mapped[str] = mapped_column(Text, nullable=False)
    pipeline: Mapped[str] = mapped_column(Text, nullable=False)
    source_slug: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    record_count: Mapped[int] = mapped_column(Integer, server_default="0")
    superseded_batch_key: Mapped[str | None] = mapped_column(Text)
    superseded_version: Mapped[int | None] = mapped_column(Integer)
    promoted_by: Mapped[str | None] = mapped_column(Text)
    auto: Mapped[bool] = mapped_column(server_default="false")
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    schema_version: Mapped[str] = mapped_column(Text, server_default="v1")

