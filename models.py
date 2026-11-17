"""
models.py

SQLAlchemy ORM models — the database schema, kept in step with the Alembic
migrations (docs/MIGRATIONS.md). Defines the V1.0 site hierarchy (Portfolio,
SourceConnection, Site, DiscoveredComponent; docs/DATA_MODEL.md §2) and the
MVP tables: Asset (batteries, solar and wind farms, now attached to a site),
their StateOfCharge/telemetry history, grid-wide GridSignal readings, and
DispatchCommand records sent to assets.
"""

from sqlalchemy import (
    CHAR, Boolean, CheckConstraint, Column, Date, DateTime, Float, ForeignKey,
    ForeignKeyConstraint, Index, Integer, Numeric, String, Text,
    UniqueConstraint, func, text,
)
from sqlalchemy.dialects.postgresql import ARRAY
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


def _values(enum_cls):
    """Store an enum's lowercase values, not its member names (V1.0 enums)."""
    return [member.value for member in enum_cls]


def _currency_check(table, column):
    return CheckConstraint(f"{column} ~ '^[A-Z]{{3}}$'", name=f"ck_{table}_{column}_iso4217")


class ReferenceMixin:
    """id, created_at and updated_at: every V1.0 reference table has them (DATA_MODEL.md §1)."""
    id = mapped_column(Integer, primary_key=True, sort_order=-1)  # first column of the table
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


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

