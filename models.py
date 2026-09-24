from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from database import Base
from enum import Enum as PyEnum
from sqlalchemy import Enum

class AssetType(PyEnum):
    BATTERY = "battery"
    SOLAR = "solar"
    WIND = "wind"

class GridConnectionStatus(PyEnum):
    ACTIVE   = "active"      # Operating normally — direction read from power_mw sign
    CURTAILED = "curtailed"  # Output restricted by grid operator instruction
    HOLDING  = "holding"     # Standing by — reserved for frequency response
    FAULT    = "fault"       # Asset is in a fault state
  

class AssetStatus(PyEnum):
    COMMUNICATING = "communicating"
    UNREACHABLE = "unreachable"


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
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    state_of_charge_records = relationship("StateOfCharge", back_populates="asset")
    dispatch_commands       = relationship("DispatchCommand", back_populates="asset")


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

