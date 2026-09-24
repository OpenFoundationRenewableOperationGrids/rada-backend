"""
seed_data.py
Drops and recreates all tables, then populates 30 days of realistic
grid-scale historical data.

Assets modelled:
  - 30 x Battery BESS units (real-world manufacturers)
  -  9 x Solar farms
  -  9 x Wind farms

History depth:
  - state_of_charge  : every 10 min × 30 days = 4,320 records per asset
  - dispatch_commands: 1–3 per day  × 30 days = ~60  records per active asset
  - grid_signals     : every 5 min  × 30 days = 8,640 records

Units: MW and MWh throughout (grid-scale standard).

Usage:
    python seed_data.py
"""

import sys
import os
import math
import random
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import Base, engine, SessionLocal
from models import (
    Asset, AssetType, GridConnectionStatus, AssetStatus,
    StateOfCharge, DispatchCommand, GridSignal,
)
from datetime import datetime, timezone, timedelta

random.seed(42)

def utcnow() -> datetime:
    return datetime.now(timezone.utc)

def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))

def power_factor_from_pq(p: float, q: float) -> float:
    apparent = math.sqrt(p**2 + q**2)
    return round(p / apparent, 4) if apparent > 0 else 1.0

SOLAR_PROFILE = [
    0.00, 0.00, 0.00, 0.00, 0.00, 0.00,
    0.04, 0.15, 0.37, 0.60, 0.80, 0.96,
    1.00, 0.98, 0.92, 0.83, 0.68, 0.48,
    0.26, 0.10, 0.02, 0.00, 0.00, 0.00,
]

# ── 1. Recreate all tables ────────────────────────────────────────────────────
print("Dropping and recreating all tables...")
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)
print("Tables created.\n")

# ── 2. Assets ─────────────────────────────────────────────────────────────────
batteries = [
    # Tesla Megapack 2 XL
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-TM2XL-001---X", name="Tesla Megapack 2 XL - Unit 01",         max_capacity_mwh=4.0,   max_charge_rate_mw=1.9,   max_discharge_rate_mw=1.9,   reactive_power_capacity_mvar=0.95,  efficiency=0.94),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-TM2XL-002---X", name="Tesla Megapack 2 XL - Unit 02",         max_capacity_mwh=4.0,   max_charge_rate_mw=1.9,   max_discharge_rate_mw=1.9,   reactive_power_capacity_mvar=0.95,  efficiency=0.94),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-TM2XL-003---X", name="Tesla Megapack 2 XL - Unit 03",         max_capacity_mwh=4.0,   max_charge_rate_mw=1.9,   max_discharge_rate_mw=1.9,   reactive_power_capacity_mvar=0.95,  efficiency=0.94),
    # Fluence Gridstack Pro
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-FLGSP-001---X", name="Fluence Gridstack Pro - Unit 01",       max_capacity_mwh=2.0,   max_charge_rate_mw=1.0,   max_discharge_rate_mw=1.0,   reactive_power_capacity_mvar=0.5,   efficiency=0.95),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-FLGSP-002---X", name="Fluence Gridstack Pro - Unit 02",       max_capacity_mwh=2.0,   max_charge_rate_mw=1.0,   max_discharge_rate_mw=1.0,   reactive_power_capacity_mvar=0.5,   efficiency=0.95),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-FLGSP-003---X", name="Fluence Gridstack Pro - Unit 03",       max_capacity_mwh=2.0,   max_charge_rate_mw=1.0,   max_discharge_rate_mw=1.0,   reactive_power_capacity_mvar=0.5,   efficiency=0.95),
    # Sungrow PowerTitan
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-SGPT2-001---X", name="Sungrow PowerTitan ST2236UX - Unit 01", max_capacity_mwh=2.236, max_charge_rate_mw=1.0,   max_discharge_rate_mw=1.0,   reactive_power_capacity_mvar=0.5,   efficiency=0.95),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-SGPT2-002---X", name="Sungrow PowerTitan ST2236UX - Unit 02", max_capacity_mwh=2.236, max_charge_rate_mw=1.0,   max_discharge_rate_mw=1.0,   reactive_power_capacity_mvar=0.5,   efficiency=0.95),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-SGPT2-003---X", name="Sungrow PowerTitan ST2236UX - Unit 03", max_capacity_mwh=2.236, max_charge_rate_mw=1.0,   max_discharge_rate_mw=1.0,   reactive_power_capacity_mvar=0.5,   efficiency=0.95),
    # BYD MC-Cube
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-BYDMC-001---X", name="BYD MC-Cube - Unit 01",                 max_capacity_mwh=1.672, max_charge_rate_mw=0.836, max_discharge_rate_mw=0.836, reactive_power_capacity_mvar=0.4,   efficiency=0.96),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-BYDMC-002---X", name="BYD MC-Cube - Unit 02",                 max_capacity_mwh=1.672, max_charge_rate_mw=0.836, max_discharge_rate_mw=0.836, reactive_power_capacity_mvar=0.4,   efficiency=0.96),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-BYDMC-003---X", name="BYD MC-Cube - Unit 03",                 max_capacity_mwh=1.672, max_charge_rate_mw=0.836, max_discharge_rate_mw=0.836, reactive_power_capacity_mvar=0.4,   efficiency=0.96),
    # Wartsila GridSolv Quantum
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-WGSQ1-001---X", name="Wartsila GridSolv Quantum - Unit 01",   max_capacity_mwh=1.12,  max_charge_rate_mw=0.56,  max_discharge_rate_mw=0.56,  reactive_power_capacity_mvar=0.28,  efficiency=0.96),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-WGSQ1-002---X", name="Wartsila GridSolv Quantum - Unit 02",   max_capacity_mwh=1.12,  max_charge_rate_mw=0.56,  max_discharge_rate_mw=0.56,  reactive_power_capacity_mvar=0.28,  efficiency=0.96),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-WGSQ1-003---X", name="Wartsila GridSolv Quantum - Unit 03",   max_capacity_mwh=1.12,  max_charge_rate_mw=0.56,  max_discharge_rate_mw=0.56,  reactive_power_capacity_mvar=0.28,  efficiency=0.94),
    # GE Reservoir
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-GERES-001---X", name="GE Reservoir - Unit 01",                max_capacity_mwh=4.0,   max_charge_rate_mw=2.0,   max_discharge_rate_mw=2.0,   reactive_power_capacity_mvar=1.0,   efficiency=0.94),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-GERES-002---X", name="GE Reservoir - Unit 02",                max_capacity_mwh=4.0,   max_charge_rate_mw=2.0,   max_discharge_rate_mw=2.0,   reactive_power_capacity_mvar=1.0,   efficiency=0.94),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-GERES-003---X", name="GE Reservoir - Unit 03",                max_capacity_mwh=4.0,   max_charge_rate_mw=2.0,   max_discharge_rate_mw=2.0,   reactive_power_capacity_mvar=1.0,   efficiency=0.94),
    # CATL EnerC
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-CATLC-001---X", name="CATL EnerC - Unit 01",                  max_capacity_mwh=1.5,   max_charge_rate_mw=0.75,  max_discharge_rate_mw=0.75,  reactive_power_capacity_mvar=0.375, efficiency=0.95),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-CATLC-002---X", name="CATL EnerC - Unit 02",                  max_capacity_mwh=1.5,   max_charge_rate_mw=0.75,  max_discharge_rate_mw=0.75,  reactive_power_capacity_mvar=0.375, efficiency=0.95),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-CATLC-003---X", name="CATL EnerC - Unit 03",                  max_capacity_mwh=1.5,   max_charge_rate_mw=0.75,  max_discharge_rate_mw=0.75,  reactive_power_capacity_mvar=0.375, efficiency=0.95),
    # Stem Athena
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-STATH-001---X", name="Stem Athena - Unit 01",                 max_capacity_mwh=1.0,   max_charge_rate_mw=0.5,   max_discharge_rate_mw=0.5,   reactive_power_capacity_mvar=0.25,  efficiency=0.95),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-STATH-002---X", name="Stem Athena - Unit 02",                 max_capacity_mwh=1.0,   max_charge_rate_mw=0.5,   max_discharge_rate_mw=0.5,   reactive_power_capacity_mvar=0.25,  efficiency=0.94),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-STATH-003---X", name="Stem Athena - Unit 03",                 max_capacity_mwh=1.0,   max_charge_rate_mw=0.5,   max_discharge_rate_mw=0.5,   reactive_power_capacity_mvar=0.25,  efficiency=0.94),
    # Hitachi Energy Gridscale G2
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-HEGG2-001---X", name="Hitachi Energy Gridscale G2 - Unit 01", max_capacity_mwh=1.2,   max_charge_rate_mw=0.6,   max_discharge_rate_mw=0.6,   reactive_power_capacity_mvar=0.3,   efficiency=0.94),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-HEGG2-002---X", name="Hitachi Energy Gridscale G2 - Unit 02", max_capacity_mwh=1.2,   max_charge_rate_mw=0.6,   max_discharge_rate_mw=0.6,   reactive_power_capacity_mvar=0.3,   efficiency=0.94),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-HEGG2-003---X", name="Hitachi Energy Gridscale G2 - Unit 03", max_capacity_mwh=1.2,   max_charge_rate_mw=0.6,   max_discharge_rate_mw=0.6,   reactive_power_capacity_mvar=0.3,   efficiency=0.93),
    # Powin Stack750
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-PWST7-001---X", name="Powin Stack750 - Unit 01",              max_capacity_mwh=3.0,   max_charge_rate_mw=1.5,   max_discharge_rate_mw=1.5,   reactive_power_capacity_mvar=0.75,  efficiency=0.95),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-PWST7-002---X", name="Powin Stack750 - Unit 02",              max_capacity_mwh=3.0,   max_charge_rate_mw=1.5,   max_discharge_rate_mw=1.5,   reactive_power_capacity_mvar=0.75,  efficiency=0.95),
    Asset(asset_type=AssetType.BATTERY, eic_code="17W-PWST7-003---X", name="Powin Stack750 - Unit 03",              max_capacity_mwh=3.0,   max_charge_rate_mw=1.5,   max_discharge_rate_mw=1.5,   reactive_power_capacity_mvar=0.75,  efficiency=0.95),
]

solar_assets = [
    Asset(asset_type=AssetType.SOLAR, eic_code="17W-SPVPA-001---X", name="Parc Solaire Provence — Site A",     max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=50.0,  reactive_power_capacity_mvar=25.0, efficiency=0.97),
    Asset(asset_type=AssetType.SOLAR, eic_code="17W-SPVPA-002---X", name="Parc Solaire Provence — Site B",     max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=45.0,  reactive_power_capacity_mvar=22.5, efficiency=0.97),
    Asset(asset_type=AssetType.SOLAR, eic_code="17W-SPVPA-003---X", name="Parc Solaire Provence — Site C",     max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=38.0,  reactive_power_capacity_mvar=19.0, efficiency=0.96),
    Asset(asset_type=AssetType.SOLAR, eic_code="17W-SPVAQ-001---X", name="Parc Solaire Aquitaine — Site A",    max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=32.0,  reactive_power_capacity_mvar=16.0, efficiency=0.97),
    Asset(asset_type=AssetType.SOLAR, eic_code="17W-SPVAQ-002---X", name="Parc Solaire Aquitaine — Site B",    max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=28.0,  reactive_power_capacity_mvar=14.0, efficiency=0.97),
    Asset(asset_type=AssetType.SOLAR, eic_code="17W-SPVAQ-003---X", name="Parc Solaire Aquitaine — Site C",    max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=22.0,  reactive_power_capacity_mvar=11.0, efficiency=0.96),
    Asset(asset_type=AssetType.SOLAR, eic_code="17W-TILYA-001---X", name="Toiture Industrielle Lyon — Zone A", max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=8.5,   reactive_power_capacity_mvar=4.25, efficiency=0.96),
    Asset(asset_type=AssetType.SOLAR, eic_code="17W-TILYA-002---X", name="Toiture Industrielle Lyon — Zone B", max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=7.2,   reactive_power_capacity_mvar=3.6,  efficiency=0.96),
    Asset(asset_type=AssetType.SOLAR, eic_code="17W-TIMRS-001---X", name="Toiture Industrielle Marseille",     max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=12.0,  reactive_power_capacity_mvar=6.0,  efficiency=0.97),
]

wind_assets = [
    Asset(asset_type=AssetType.WIND, eic_code="17W-EWPNC-001---X", name="Parc Éolien Normandie — Cluster 1",  max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=120.0, reactive_power_capacity_mvar=60.0,  efficiency=0.96),
    Asset(asset_type=AssetType.WIND, eic_code="17W-EWPNC-002---X", name="Parc Éolien Normandie — Cluster 2",  max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=95.0,  reactive_power_capacity_mvar=47.5,  efficiency=0.96),
    Asset(asset_type=AssetType.WIND, eic_code="17W-EWPNC-003---X", name="Parc Éolien Normandie — Cluster 3",  max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=80.0,  reactive_power_capacity_mvar=40.0,  efficiency=0.95),
    Asset(asset_type=AssetType.WIND, eic_code="17W-EWPBC-001---X", name="Parc Éolien Bretagne — Cluster 1",   max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=80.0,  reactive_power_capacity_mvar=40.0,  efficiency=0.96),
    Asset(asset_type=AssetType.WIND, eic_code="17W-EWPBC-002---X", name="Parc Éolien Bretagne — Cluster 2",   max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=65.0,  reactive_power_capacity_mvar=32.5,  efficiency=0.96),
    Asset(asset_type=AssetType.WIND, eic_code="17W-EWPBC-003---X", name="Parc Éolien Bretagne — Cluster 3",   max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=55.0,  reactive_power_capacity_mvar=27.5,  efficiency=0.95),
    Asset(asset_type=AssetType.WIND, eic_code="17W-EWPHF-001---X", name="Parc Éolien Hauts-de-France — Nord", max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=200.0, reactive_power_capacity_mvar=100.0, efficiency=0.97),
    Asset(asset_type=AssetType.WIND, eic_code="17W-EWPHF-002---X", name="Parc Éolien Hauts-de-France — Sud",  max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=160.0, reactive_power_capacity_mvar=80.0,  efficiency=0.97),
    Asset(asset_type=AssetType.WIND, eic_code="17W-EWPHF-003---X", name="Parc Éolien Hauts-de-France — Est",  max_capacity_mwh=0.0, max_charge_rate_mw=0.0, max_discharge_rate_mw=140.0, reactive_power_capacity_mvar=70.0,  efficiency=0.96),
]

all_assets = batteries + solar_assets + wind_assets


# ── 3. StateOfCharge ──────────────────────────────────────────────────────────

def make_soc_records(asset: Asset) -> list:
    records = []
    now = utcnow()
    total_minutes = 30 * 24 * 60
    interval = 10
    steps = total_minutes // interval

    for i in range(steps):
        ts = now - timedelta(minutes=total_minutes - i * interval)
        hour = ts.hour

        if asset.asset_type == AssetType.BATTERY:
            phase = (hour - 7) / 24
            base_fraction = 0.575 + 0.375 * math.cos(2 * math.pi * phase)
            noise = (random.random() - 0.5) * 0.08
            energy_fraction = clamp(base_fraction + noise, 0.20, 0.95)
            energy = round(energy_fraction * asset.max_capacity_mwh, 4)

            if 0 <= hour < 7:
                power_mw = round(random.uniform(asset.max_charge_rate_mw * 0.5, asset.max_charge_rate_mw * 0.95), 4)
            elif 7 <= hour < 10:
                power_mw = round(random.uniform(asset.max_discharge_rate_mw * 0.5, asset.max_discharge_rate_mw * 0.95), 4) * -1
            elif 10 <= hour < 14:
                power_mw = round(random.uniform(asset.max_charge_rate_mw * 0.2, asset.max_charge_rate_mw * 0.6), 4)
            elif 14 <= hour < 16:
                power_mw = round(random.uniform(asset.max_discharge_rate_mw * 0.1, asset.max_discharge_rate_mw * 0.4), 4) * -1
            elif 16 <= hour < 21:
                power_mw = round(random.uniform(asset.max_discharge_rate_mw * 0.6, asset.max_discharge_rate_mw), 4) * -1
            else:
                power_mw = round(random.uniform(asset.max_charge_rate_mw * 0.1, asset.max_charge_rate_mw * 0.4), 4)

        elif asset.asset_type == AssetType.SOLAR:
            day = i // 144
            cloud = 0.85 + 0.15 * math.sin(day * math.pi / 7)
            fraction = SOLAR_PROFILE[hour] * cloud + random.uniform(-0.03, 0.03)
            power_mw = round(max(0.0, fraction * asset.max_discharge_rate_mw), 4)
            energy = 0.0

        else:  # WIND
            day_fraction = 0.45 + 0.35 * math.sin(i * math.pi / (144 * 3.5))
            power_mw = round(clamp(
                (day_fraction + random.uniform(-0.12, 0.12)) * asset.max_discharge_rate_mw,
                0.0, asset.max_discharge_rate_mw), 4)
            energy = 0.0

        mvar_cap = asset.reactive_power_capacity_mvar or 0.0
        reactive_power_mvar = round(random.uniform(-mvar_cap * 0.3, mvar_cap * 0.3), 4)
        pf = power_factor_from_pq(abs(power_mw), abs(reactive_power_mvar))

        # 1-in-100 chance of fault on any record
        if random.randint(1, 100) == 1:
            mode         = GridConnectionStatus.FAULT
            power_mw     = 0.0
            asset_status = AssetStatus.UNREACHABLE
        else:
            if abs(power_mw) < 0.01:
                mode = GridConnectionStatus.CURTAILED
            else:
                mode = GridConnectionStatus.ACTIVE
            if asset.asset_type in (AssetType.SOLAR, AssetType.WIND):
                if random.random() < 0.03:
                    mode     = GridConnectionStatus.CURTAILED
                    power_mw = round(power_mw * random.uniform(0.1, 0.5), 4)
            asset_status = AssetStatus.COMMUNICATING

        # Temperature — ambient drift + load correlation + noise (batteries only)
        if asset.asset_type == AssetType.BATTERY:
            load_ratio   = abs(power_mw) / asset.max_discharge_rate_mw if asset.max_discharge_rate_mw > 0 else 0
            base_temp    = 22.0 + 6.0 * math.sin(i * math.pi / (6 * 144))
            temp_celsius = round(base_temp + load_ratio * 12.0 + random.uniform(-1.5, 1.5), 1)
        else:
            temp_celsius = None

        # current_amps — abs() to avoid negative values from charging power_mw
        current_amps = round(abs(power_mw) * 1000 / 1400, 2) if asset.asset_type == AssetType.BATTERY else None

        records.append(StateOfCharge(
            asset_id=asset.id,
            timestamp=ts,
            asset_status=asset_status,
            operational_mode=mode,
            energy_mwh=energy if asset.asset_type == AssetType.BATTERY else 0.0,
            voltage=round(random.uniform(1380.0, 1420.0), 1) if asset.asset_type == AssetType.BATTERY else None,
            current_amps=current_amps,
            temperature_celsius=temp_celsius,
            power_mw=power_mw,
            reactive_power_mvar=reactive_power_mvar,
            power_factor=pf,
        ))

    return records


# ── 4. Dispatch Commands ──────────────────────────────────────────────────────

COMMAND_TYPES = {
    AssetType.BATTERY: ["charge", "discharge", "frequency_response", "standby", "peak_shave"],
    AssetType.SOLAR:   ["curtail", "frequency_response", "standby"],
    AssetType.WIND:    ["curtail", "frequency_response", "standby"],
}

def make_dispatch_commands(asset: Asset) -> list:
    records = []
    now = utcnow()

    for day in range(30):
        for _ in range(random.randint(1, 3)):
            cmd = random.choice(COMMAND_TYPES[asset.asset_type])
            power = 0.0 if cmd == "standby" else round(
                random.uniform(asset.max_discharge_rate_mw * 0.3,
                               asset.max_discharge_rate_mw), 3)
            cmd_ts = now - timedelta(days=30 - day, hours=random.uniform(0, 23))
            status = "executed" if day < 29 else random.choice(["executed", "pending"])

            records.append(DispatchCommand(
                asset_id=asset.id,
                command_type=cmd,
                power_target_mw=power,
                duration_seconds=random.choice([300, 600, 900, 1800, 3600]),
                timestamp=cmd_ts,
                status=status,
            ))

    return records


# ── 5. Grid Signals ───────────────────────────────────────────────────────────

def make_grid_signals() -> list:
    records = []
    now = utcnow()
    total_minutes = 30 * 24 * 60
    steps = total_minutes // 5

    for i in range(steps):
        ts = now - timedelta(minutes=total_minutes - i * 5)
        hour = ts.hour
        solar_boost = SOLAR_PROFILE[hour] * 8000
        total_mw = round(random.uniform(40000.0, 65000.0), 1)
        renewable_mw = round(clamp(
            8000.0 + solar_boost + random.uniform(-500, 500) +
            4000 * (0.5 + 0.3 * math.sin(i * math.pi / (24 * 12))),
            6000.0, 28000.0), 1)
        renewable_pct = round((renewable_mw / total_mw) * 100, 2)
        imbalance_mw = round(random.gauss(0, 150), 1)
        fcr_activated_mw = round(abs(imbalance_mw) * 0.15, 1)
        calculated_frequency_hz = round(50.0 + (imbalance_mw * 0.00025), 4)

        if abs(calculated_frequency_hz - 50.0) > 0.05:
            status = "frequency_alert"
        elif abs(calculated_frequency_hz - 50.0) > 0.02:
            status = "frequency_warning"
        else:
            status = "nominal"

        records.append(GridSignal(
            total_generation_mw=total_mw,
            renewable_mw=renewable_mw,
            renewable_pct=renewable_pct,
            imbalance_mw=imbalance_mw,
            imbalance_trend="upward" if imbalance_mw <= 0 else "downward",
            fcr_activated_mw=fcr_activated_mw,
            calculated_frequency_hz=calculated_frequency_hz,
            status=status,
            timestamp=ts,
        ))

    return records


# ── Seed ──────────────────────────────────────────────────────────────────────

def seed():
    db = SessionLocal()
    try:
        db.add_all(all_assets)
        db.flush()
        print(f"✅ {len(batteries)} battery assets")
        print(f"✅ {len(solar_assets)} solar assets")
        print(f"✅ {len(wind_assets)} wind assets")

        print("Building state_of_charge records...")
        soc_records = []
        for asset in all_assets:
            soc_records.extend(make_soc_records(asset))
        db.add_all(soc_records)
        db.flush()
        print(f"✅ {len(soc_records)} state_of_charge records")

        print("Building dispatch_command records...")
        dispatch_records = []
        for asset in all_assets:
            dispatch_records.extend(make_dispatch_commands(asset))
        db.add_all(dispatch_records)
        db.flush()
        print(f"✅ {len(dispatch_records)} dispatch_command records")

        print("Building grid_signal records...")
        grid_signals = make_grid_signals()
        db.add_all(grid_signals)
        db.flush()
        print(f"✅ {len(grid_signals)} grid_signal records")

        db.commit()
        total = len(all_assets) + len(soc_records) + len(dispatch_records) + len(grid_signals)
        print(f"\n🎉 Database seeded. Total rows: {total}")

    except Exception as e:
        db.rollback()
        print(f"❌ Error: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    seed()
