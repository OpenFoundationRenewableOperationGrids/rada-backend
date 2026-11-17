"""
models.py

SQLAlchemy ORM models — the database schema, kept in step with the Alembic
migrations (docs/MIGRATIONS.md). Defines the V1.0 site hierarchy (Portfolio,
SourceConnection, Site, DiscoveredComponent; docs/DATA_MODEL.md §2), the
metric catalogue (Language, Metric, MetricTranslation, MetricMapping; §2-§3),
the finance tables (Tariff, FixedAsset, FixedAssetLink; §5), the measurement
hypertable (§4) and the MVP tables: Asset (batteries, solar and wind farms,
now attached to a site), their StateOfCharge/telemetry history, grid-wide
GridSignal readings, and DispatchCommand records sent to assets.
"""

from sqlalchemy import (
    CHAR, Boolean, CheckConstraint, Column, Date, DateTime, Double, Float, ForeignKey,
    ForeignKeyConstraint, Index, Integer, Numeric, String, Table, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY, ExcludeConstraint
from sqlalchemy.orm import mapped_column, relationship
from datetime import datetime, timezone

from database import Base
from enum import Enum as PyEnum
from sqlalchemy import Enum

# MVP exception: assettype stores the member NAMES ('BATTERY'), unlike the
# V1.0 enums below, which store their lowercase values.
class AssetType(PyEnum):
    BATTERY = "battery"
    SOLAR = "solar"
    WIND = "wind"
    METER = "meter"         # grid meter
    INVERTER = "inverter"

class GridConnectionStatus(PyEnum):
    ACTIVE   = "active"      # Operating normally — direction read from power_mw sign
    CURTAILED = "curtailed"  # Output restricted by grid operator instruction
    HOLDING  = "holding"     # Standing by — reserved for frequency response
    FAULT    = "fault"       # Asset is in a fault state
  

class AssetStatus(PyEnum):
    COMMUNICATING = "communicating"
    UNREACHABLE = "unreachable"


class SourceKind(PyEnum):
    OPENEMS = "openems"


class DiscoveryStatus(PyEnum):
    PENDING = "pending"
    CONFIRMED = "confirmed"
    IGNORED = "ignored"


class MetricKind(PyEnum):
    INSTANTANEOUS = "instantaneous"
    CUMULATIVE = "cumulative"
    STATE = "state"


class MetricLevel(PyEnum):
    SITE = "site"    # posted and mapped with no asset
    ASSET = "asset"  # requires an asset
    BOTH = "both"


class MetricCategory(PyEnum):
    CORE = "core"          # has KPIs
    OPTIONAL = "optional"  # charted and exported, no KPI
    CUSTOM = "custom"      # created by an administrator


def _values(enum_cls):
    """Store an enum's lowercase values, not its member names (V1.0 enums)."""
    return [member.value for member in enum_cls]


def _currency_check(table, column):
    return CheckConstraint(f"{column} ~ '^[A-Z]{{3}}$'", name=f"ck_{table}_{column}_iso4217")


class TimestampMixin:
    """created_at and updated_at, for every V1.0 table (DATA_MODEL.md §1)."""
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class ReferenceMixin(TimestampMixin):
    """Plus the integer id: the key of every V1.0 table with no natural key of its own."""
    id = mapped_column(Integer, primary_key=True, sort_order=-1)  # first column of the table


class Portfolio(ReferenceMixin, Base):
    """The owner or client that sites are grouped and reported by."""
    __tablename__ = "portfolio"

    name = Column(Text, nullable=False, unique=True)
    external_ref = Column(Text, nullable=True)  # customer or company code in the finance system
    currency = Column(CHAR(3), nullable=False)  # ISO 4217; default for the portfolio's sites
    is_active = Column(Boolean, nullable=False, server_default=text("true"))

    sites = relationship("Site", back_populates="portfolio")

    __table_args__ = (_currency_check("portfolio", "currency"),)


class SourceConnection(ReferenceMixin, Base):
    """One data source; in 1.0 one OpenEMS Backend. Secrets stay in environment variables."""
    __tablename__ = "source_connection"

    name = Column(Text, nullable=False, unique=True)  # e.g. "openems-vps"
    kind = Column(Enum(SourceKind, name="sourcekind", values_callable=_values), nullable=False)
    url = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))


class Site(ReferenceMixin, Base):
    """A physical location with one grid connection, mapped to at most one OpenEMS edge."""
    __tablename__ = "site"

    portfolio_id = Column(Integer, ForeignKey("portfolio.id"), nullable=False, index=True)
    name = Column(Text, nullable=False)
    source_connection_id = Column(Integer, ForeignKey("source_connection.id"), nullable=True)
    edge_id = Column(Text, nullable=True)  # OpenEMS edge, e.g. "edge0"; unique within its connection
    timezone = Column(Text, nullable=False)  # IANA name, e.g. "Europe/Paris"
    currency = Column(CHAR(3), nullable=True)  # overrides the portfolio's currency
    latitude = Column(Numeric, nullable=True)
    longitude = Column(Numeric, nullable=True)
    export_limit_w = Column(Integer, nullable=True)
    external_ref = Column(Text, nullable=True)  # cost centre or business unit
    last_seen_at = Column(DateTime(timezone=True), nullable=True)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))

    portfolio = relationship("Portfolio", back_populates="sites")
    source_connection = relationship("SourceConnection")
    assets = relationship("Asset", back_populates="site")

    __table_args__ = (
        UniqueConstraint("source_connection_id", "edge_id", name="uq_site_source_connection_edge"),
        # The constraint above lets duplicates through while the connection is empty (NULLs are
        # distinct). NULLS NOT DISTINCT would also refuse a second site with no edge at all.
        Index(
            "uq_site_edge_without_connection", "edge_id",
            unique=True, postgresql_where=text("source_connection_id IS NULL"),
        ),
        _currency_check("site", "currency"),
    )


class DiscoveredComponent(ReferenceMixin, Base):
    """What discovery (BE-05) found on an edge, waiting for an administrator."""
    __tablename__ = "discovered_component"

    site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    source_component = Column(Text, nullable=False)  # e.g. "ess1"
    alias = Column(Text, nullable=True)
    factory_id = Column(Text, nullable=True)
    natures = Column(ARRAY(Text), nullable=False, server_default=text("'{}'"))  # e.g. {SymmetricEss}
    suggested_asset_type = Column(Enum(AssetType, name="assettype"), nullable=True)
    status = Column(
        Enum(DiscoveryStatus, name="discoverystatus", values_callable=_values),
        nullable=False, server_default="pending",
    )
    first_seen_at = Column(DateTime(timezone=True), nullable=False)
    last_seen_at = Column(DateTime(timezone=True), nullable=False)
    asset_id = Column(Integer, nullable=True)  # set on confirmation

    __table_args__ = (
        UniqueConstraint("site_id", "source_component", name="uq_discovered_component_site_component"),
        # The confirmed asset must belong to the same site
        ForeignKeyConstraint(
            ["asset_id", "site_id"], ["assets.id", "assets.site_id"],
            name="fk_discovered_component_asset_site",
        ),
    )


class Language(TimestampMixin, Base):
    """A language the install supports. Its BCP 47 code is the key (DATA_MODEL.md §2)."""
    __tablename__ = "language"

    code = Column(Text, primary_key=True)  # "en", "fr", "de"
    name = Column(Text, nullable=False)  # the language's own name: English, Français, Deutsch
    is_default = Column(Boolean, nullable=False, server_default=text("false"))  # fallback for missing names
    is_active = Column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        # At most one default; the catalogue loader makes sure there is one
        Index("uq_language_single_default", "is_default", unique=True, postgresql_where=text("is_default")),
    )


class Metric(ReferenceMixin, Base):
    """The catalogue of what can be measured. Names live in MetricTranslation."""
    __tablename__ = "metric"

    key = Column(Text, nullable=False, unique=True)  # e.g. "battery_power"; language-neutral
    unit = Column(Text, nullable=False)  # storage unit: W, Wh, var, %, V, A, °C, or any for a custom metric
    kind = Column(Enum(MetricKind, name="metrickind", values_callable=_values), nullable=False)
    level = Column(Enum(MetricLevel, name="metriclevel", values_callable=_values), nullable=False)
    category = Column(Enum(MetricCategory, name="metriccategory", values_callable=_values), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))

    translations = relationship("MetricTranslation", back_populates="metric")

    # Revision 0003 also guards level changes with a trigger (see MetricMapping)
    __table_args__ = (CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name="ck_metric_key_format"),)


class MetricTranslation(TimestampMixin, Base):
    """A metric's display name and sign note in one language."""
    __tablename__ = "metric_translation"

    metric_id = Column(Integer, ForeignKey("metric.id"), primary_key=True)
    language_code = Column(Text, ForeignKey("language.code"), primary_key=True)
    name = Column(Text, nullable=False)  # e.g. "Netzleistung"
    sign_note = Column(Text, nullable=True)  # e.g. "positiv = Netzbezug"

    metric = relationship("Metric", back_populates="translations")


class MetricMapping(ReferenceMixin, Base):
    """Which source channel feeds which metric, read and applied by the bridge.

    Revision 0003 adds a trigger the models cannot express: the metric's level
    must allow asset_id to be filled in (asset, both) or empty (site, both).
    """
    __tablename__ = "metric_mapping"

    site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    asset_id = Column(Integer, nullable=True)  # empty for a site-wide value
    metric_id = Column(Integer, ForeignKey("metric.id"), nullable=False)
    source = Column(Text, nullable=False)  # "openems" in 1.0
    source_address = Column(Text, nullable=False)  # component/channel, e.g. "ess0/ActivePower"
    scale = Column(Numeric, nullable=False, server_default=text("1"))  # multiplier on the raw value
    is_enabled = Column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        UniqueConstraint("site_id", "source", "source_address", name="uq_metric_mapping_site_source_address"),
        # The asset must belong to the site (not checked while asset_id is empty)
        ForeignKeyConstraint(
            ["asset_id", "site_id"], ["assets.id", "assets.site_id"],
            name="fk_metric_mapping_asset_site",
        ),
    )


class Tariff(ReferenceMixin, Base):
    """Import and export price of a site over a period. valid_to is inclusive; empty means open-ended."""
    __tablename__ = "tariff"

    site_id = Column(Integer, ForeignKey("site.id"), nullable=False)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    # Prices are per kWh, while energy is stored in Wh: divide Wh by 1000 before pricing
    import_price_per_kwh = Column(
        Numeric(10, 6), nullable=False, comment="Price per kWh imported, in currency. Energy is stored in Wh.",
    )
    export_price_per_kwh = Column(
        Numeric(10, 6), nullable=False, comment="Price per kWh exported, in currency. Energy is stored in Wh.",
    )
    currency = Column(CHAR(3), nullable=False)
    label = Column(Text, nullable=True)  # e.g. the supplier and contract name

    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="ck_tariff_valid_period"),
        _currency_check("tariff", "currency"),
        # Periods of one site cannot overlap. '[]': both ends inclusive; a NULL valid_to is unbounded.
        # Needs the btree_gist extension for the "site_id WITH =" part (revision 0003).
        ExcludeConstraint(
            ("site_id", "="),
            (func.daterange(Column("valid_from", Date), Column("valid_to", Date), "[]"), "&&"),
            using="gist", name="ex_tariff_no_overlap",
        ),
    )


class FixedAsset(ReferenceMixin, Base):
    """The finance view of an asset; only the external reference when an ERP is the master."""
    __tablename__ = "fixed_asset"

    portfolio_id = Column(Integer, ForeignKey("portfolio.id"), nullable=False)
    reference = Column(Text, nullable=False)  # unique within the portfolio
    description = Column(Text, nullable=False)
    acquisition_date = Column(Date, nullable=True)
    acquisition_cost = Column(Numeric(14, 2), nullable=True)  # in currency
    currency = Column(CHAR(3), nullable=False)
    expected_life_months = Column(Integer, nullable=True)
    external_system = Column(Text, nullable=True)  # the ERP's name
    external_id = Column(Text, nullable=True)  # its asset number

    __table_args__ = (
        UniqueConstraint("portfolio_id", "reference", name="uq_fixed_asset_portfolio_reference"),
        _currency_check("fixed_asset", "currency"),
    )


class FixedAssetLink(ReferenceMixin, Base):
    """Links operational assets to a fixed asset. Whether the two may sit in different
    portfolios is an open item (DATA_MODEL.md §5), so nothing enforces it yet."""
    __tablename__ = "fixed_asset_link"

    fixed_asset_id = Column(Integer, ForeignKey("fixed_asset.id"), nullable=False)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint("fixed_asset_id", "asset_id", name="uq_fixed_asset_link_pair"),
    )


# A plain Table, not an ORM class: the ORM needs a primary key, and none fits
# (asset_id is nullable). Rows are written with Core
# INSERT ... ON CONFLICT DO NOTHING, so a value sent twice is stored once.
#
# Revision 0004 also makes it a TimescaleDB hypertable on time (without
# TimescaleDB's default index: ix_measurement_time below replaces it), adds the
# level-rule trigger (as on metric_mapping), and builds the measurement_hourly
# continuous aggregate on it. The aggregate is a view: it is not in the models.
measurement = Table(
    "measurement", Base.metadata,
    Column("time", DateTime(timezone=True), nullable=False),  # partitioning column
    Column("site_id", Integer, ForeignKey("site.id"), nullable=False),  # also on asset rows: no join for site queries
    Column("asset_id", Integer, nullable=True),  # empty means a site-wide value
    Column("metric_id", Integer, ForeignKey("metric.id"), nullable=False),
    Column("value", Double, nullable=False),  # in the metric's unit
    Column("source", Text, nullable=False),  # who wrote it: bridge-openems, simulator, seed, csv
    # The asset must belong to the site (not checked while asset_id is empty)
    ForeignKeyConstraint(["asset_id", "site_id"], ["assets.id", "assets.site_id"], name="fk_measurement_asset_site"),
    # One row per time, site, asset and metric; an empty asset counts as a value.
    # TimescaleDB requires the time column in every unique index of a hypertable.
    Index(
        "uq_measurement_point", "metric_id", "site_id", "asset_id", "time",
        unique=True, postgresql_nulls_not_distinct=True,
    ),
)
# For scans by time alone, such as the hourly aggregate's refresh
Index("ix_measurement_time", measurement.c.time.desc())


class Asset(Base):
    __tablename__ = "assets"

    id = Column(Integer, primary_key=True, index=True)
    asset_type = Column(Enum(AssetType), nullable=False)
    eic_code = Column(String(20), nullable=True, unique=True)  # ENTSO-E EIC code
    name = Column(String(100), nullable=False)
    max_charge_rate_mw = Column(Float, nullable=False)
    max_discharge_rate_mw = Column(Float, nullable=False)
    reactive_power_capacity_mvar = Column(Float, nullable=True)   # Nameplate MVAR rating
    max_capacity_mwh = Column(Float, nullable=False) 
    efficiency = Column(Float, default=0.95)
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    edge_id = Column(String, nullable=True)  # OpenEMS edge identifier, e.g. "edge0"
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    # V1.0 columns (DATA_MODEL.md §2). The MVP columns above stay until the front end has switched.
    site_id = Column(Integer, ForeignKey("site.id"), nullable=False, index=True)
    source_component = Column(Text, nullable=True)  # OpenEMS component, e.g. "ess0"
    serial_number = Column(Text, nullable=True)
    manufacturer = Column(Text, nullable=True)
    model = Column(Text, nullable=True)
    commissioning_date = Column(Date, nullable=True)
    warranty_end = Column(Date, nullable=True)
    rated_power_w = Column(Integer, nullable=True)  # installed power; PV peak power
    capacity_wh = Column(Integer, nullable=True)  # usable battery capacity
    is_active = Column(Boolean, nullable=False, server_default=text("true"))

    site                    = relationship("Site", back_populates="assets")
    state_of_charge_records = relationship("StateOfCharge", back_populates="asset")
    dispatch_commands       = relationship("DispatchCommand", back_populates="asset")

    __table_args__ = (
        # Target of the composite foreign keys that make "the asset belongs to the site" a rule
        UniqueConstraint("id", "site_id", name="uq_assets_id_site"),
        Index(
            "uq_assets_site_source_component", "site_id", "source_component",
            unique=True, postgresql_where=text("source_component IS NOT NULL"),
        ),
    )


class StateOfCharge(Base):
    __tablename__ = "state_of_charge"
    id = Column(Integer, primary_key=True, autoincrement=True)
    timestamp = Column(DateTime(timezone=True), primary_key=True, default=lambda: datetime.now(timezone.utc), index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    operational_mode = Column(Enum(GridConnectionStatus), nullable=True)
    asset_status = Column(Enum(AssetStatus), nullable=True)
    energy_mwh = Column(Float, nullable=False)
    voltage = Column(Float, nullable=True)
    current_amps = Column(Float, nullable=True)
    temperature_celsius = Column(Float, nullable=True)
    power_mw = Column(Float, nullable=True)
    reactive_power_mvar = Column(Float, nullable=True)
    power_factor = Column(Float, nullable=True)
    asset = relationship("Asset", back_populates="state_of_charge_records")

class GridSignal(Base):
    __tablename__ = "grid_signals"
    id                  = Column(Integer, primary_key=True, index=True)
    timestamp           = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    # From Actual Generation API
    total_generation_mw = Column(Float, nullable=True)
    renewable_mw        = Column(Float, nullable=True)
    renewable_pct       = Column(Float, nullable=True)
    # From Balancing API
    imbalance_mw        = Column(Float, nullable=True)
    imbalance_trend     = Column(String(20), nullable=True)  # "upward" or "downward"
    fcr_activated_mw    = Column(Float, nullable=True)
    # Calculated — derived from imbalance, not a real measurement
    calculated_frequency_hz = Column(Float, nullable=True)
    status              = Column(String(50), nullable=False)
    
class DispatchCommand (Base):
    __tablename__ = "dispatch_commands"
    
    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    asset_id = Column(Integer, ForeignKey("assets.id"), nullable=False)
    command_type = Column(String(50), nullable=False)
    power_target_mw = Column(Float, nullable=False)
    duration_seconds = Column(Integer, nullable=False)
    status = Column(String(20), nullable=False, default="pending")
    asset  = relationship("Asset", back_populates="dispatch_commands")

