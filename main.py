"""
main.py

FastAPI application entrypoint. Defines the REST API for managing grid
assets (batteries, solar, wind farms), their telemetry/state-of-charge
history, and the LLM-based grid Q&A endpoint. Protected by a shared
X-API-Key header (see verify_api_key).
"""

import os
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Query, Path, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime, timedelta
from math import ceil
from database import engine, SessionLocal
from models import Base, Asset, AssetType, StateOfCharge, GridConnectionStatus, AssetStatus
from llm_service import ask_grid_question_stream
from telemetry_simulator import run as run_simulator

RADA_BANNER = """
\033[36m╔═══════════════════════════╗
║  ▓▓▓▓   ▓▓▓  ▓▓▓▓   ▓▓▓   ║
║  ▓   ▓ ▓   ▓ ▓   ▓ ▓   ▓  ║
║  ▓▓▓▓  ▓▓▓▓▓ ▓   ▓ ▓▓▓▓▓  ║
║  ▓  ▓  ▓   ▓ ▓   ▓ ▓   ▓  ║
║  ▓   ▓ ▓   ▓ ▓▓▓▓  ▓   ▓  ║
╚═══════════════════════════╝\033[0m
\033[33m[R E N E W A B L E  A S S E T S]
[D A T A     A N A L Y T I C S]\033[0m
\033[32m   >> system online _\033[0m

\033[36m╔════════════════════╗
║\033[0m        \033[32m@..@\033[0m        \033[36m║
║\033[0m       \033[32m(----)\033[0m       \033[36m║
║\033[0m      \033[32m( >__< )\033[0m      \033[36m║
║\033[0m      \033[32m^^ ~~ ^^\033[0m      \033[36m║
╚════════════════════╝\033[0m
\033[37m  © OpenFROG 2026\033[0m
"""

print(RADA_BANNER)

# --- API Key authentication ---
API_KEY = os.getenv("API_KEY")
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)):
    if os.getenv("AUTH_ENABLED", "true").lower() == "false":
        return
    if api_key != API_KEY:
        raise HTTPException(status_code=403, detail="Invalid or missing API key")


Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    if os.getenv("TELEMETRY_SIMULATOR", "false").lower() == "true":
        thread = threading.Thread(target=run_simulator, daemon=True, name="telemetry-simulator")
        thread.start()
    yield


app = FastAPI(
    title="Grid Asset Manager API",
    description=(
        "REST API for RADA — manages grid assets (batteries, solar and wind farms), "
        "their telemetry/state-of-charge history, and grid Q&A via an LLM. "
        "Most endpoints require an `X-API-Key` header — use the **Authorize** button above."
    ),
    lifespan=lifespan,
    docs_url="/docs" if os.getenv("ENVIRONMENT") in ("development", "staging") else None,
    redoc_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dependency to get a DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# --- Pydantic schemas ---

class AssetCreate(BaseModel):
    eic_code: str = Field(
        ...,
        description="ENTSO-E EIC code identifying the asset. Used as the upsert key — "
                    "posting an existing eic_code updates that asset instead of creating a new one.",
        examples=["17W-TM2XL-001---X"],
    )
    name: str = Field(..., description="Human-readable asset name.", examples=["Tesla Megapack 2 XL - Unit 01"])
    asset_type: AssetType = Field(..., description="Type of grid asset.", examples=["battery"])
    max_capacity_mwh: float = Field(
        ..., description="Nameplate energy capacity in MWh. 0 for solar/wind (no storage).", examples=[4.0]
    )
    max_charge_rate_mw: float = Field(..., description="Maximum charge rate in MW. 0 for solar/wind.", examples=[1.9])
    max_discharge_rate_mw: float = Field(
        ..., description="Maximum discharge rate in MW (or max output for solar/wind).", examples=[1.9]
    )
    reactive_power_capacity_mvar: Optional[float] = Field(
        None, description="Nameplate reactive power rating in MVAR.", examples=[0.95]
    )
    efficiency: Optional[float] = Field(None, description="Round-trip efficiency, 0-1.", examples=[0.94])
    latitude: Optional[float] = Field(
        None, ge=-90, le=90, description="Latitude in decimal degrees (WGS84).", examples=[48.8566]
    )
    longitude: Optional[float] = Field(
        None, ge=-180, le=180, description="Longitude in decimal degrees (WGS84).", examples=[2.3522]
    )
    edge_id: Optional[str] = Field(
        None,
        description="OpenEMS Edge identifier feeding this asset's telemetry, if any. "
                    "Optional — not every asset is connected to an OpenEMS simulator/device.",
        examples=["edge0"],
    )

class TelemetryCreate(BaseModel):
    timestamp: datetime = Field(..., description="UTC timestamp of the reading.", examples=["2026-09-13T12:00:00"])
    energy_mwh: float = Field(..., description="State of charge in MWh (batteries) or 0 for solar/wind.", examples=[2.8])
    power_mw: float = Field(
        ..., description="Instantaneous power in MW. Negative = charging/absorbing, positive = discharging/producing.",
        examples=[-1.2],
    )
    operational_mode: Optional[GridConnectionStatus] = Field(None, description="Current grid connection state.")
    asset_status: Optional[AssetStatus] = Field(None, description="Whether the asset is currently reachable.")
    reactive_power_mvar: Optional[float] = Field(None, description="Reactive power in MVAR.", examples=[-0.05])
    power_factor: Optional[float] = Field(None, description="Power factor (cos φ).", examples=[0.98])
    voltage: Optional[float] = Field(None, description="Voltage in volts.", examples=[1400.0])
    current_amps: Optional[float] = Field(None, description="Current in amps.", examples=[0.85])
    temperature_celsius: Optional[float] = Field(None, description="Cell/ambient temperature in °C.", examples=[28.5])
    state_of_charge_percent: Optional[float] = Field(
        None,
        description="State of charge as a percentage — validated (BATTERY assets only) but not persisted.",
        examples=[70.0],
    )


# --- Endpoints ---

@app.get(
    "/",
    tags=["System"],
    summary="API info",
    description="Unauthenticated liveness/info endpoint.",
    responses={200: {"content": {"application/json": {"example": {"message": "Asset Grid Manager API", "status": "running"}}}}},
)
def read_root():
    return {"message": "Asset Grid Manager API", "status": "running"}

@app.get(
    "/health",
    tags=["System"],
    summary="Health check",
    description="Unauthenticated health check for uptime monitoring.",
    responses={200: {"content": {"application/json": {"example": {"status": "healthy"}}}}},
)
def health_check():
    return {"status": "healthy"}


@app.post(
    "/assets",
    status_code=201,
    dependencies=[Depends(verify_api_key)],
    tags=["Assets"],
    summary="Create or update an asset",
    description="Upsert on `eic_code`: if an asset with the same eic_code already exists, "
                "all its fields (including GPS coordinates and edge_id) are overwritten with "
                "the payload's values instead of creating a duplicate.",
    response_description="The created/updated asset's id and which action was taken.",
    responses={201: {"content": {"application/json": {"examples": {
        "created": {"summary": "New asset", "value": {"action": "created", "asset_id": 49}},
        "updated": {"summary": "Existing eic_code (upsert)", "value": {"action": "updated", "asset_id": 12}},
    }}}}},
)
def create_or_update_asset(payload: AssetCreate, db: Session = Depends(get_db)):
    asset = db.query(Asset).filter(Asset.eic_code == payload.eic_code).first()
    if asset:
        for field, value in payload.model_dump(exclude={"eic_code"}).items():
            setattr(asset, field, value)
        db.commit()
        db.refresh(asset)
        return {"action": "updated", "asset_id": asset.id}
    else:
        asset = Asset(**payload.model_dump())
        db.add(asset)
        db.commit()
        db.refresh(asset)
        return {"action": "created", "asset_id": asset.id}


@app.post(
    "/assets/{asset_id}/telemetry",
    status_code=201,
    dependencies=[Depends(verify_api_key)],
    tags=["Assets"],
    summary="Record a telemetry reading",
    description="Appends a new state-of-charge/telemetry record for the given asset. "
                "`state_of_charge_percent` is only accepted for BATTERY assets.",
    response_description="The id of the newly created record.",
    responses={
        201: {"content": {"application/json": {"example": {"record_id": 207361, "asset_id": 49}}}},
        404: {"description": "Asset not found"},
        422: {"description": "Invalid payload, e.g. state_of_charge_percent on a non-battery asset"},
    },
)
def add_telemetry(
    payload: TelemetryCreate,
    asset_id: int = Path(..., description="Id of the asset to record telemetry for.", examples=[1]),
    db: Session = Depends(get_db),
):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")

    if payload.state_of_charge_percent is not None and payload.state_of_charge_percent != 0.0 and asset.asset_type != AssetType.BATTERY:
        raise HTTPException(
            status_code=422,
            detail="state_of_charge_percent is only valid for BATTERY assets"
            )

    record = StateOfCharge(asset_id=asset_id, **payload.model_dump(exclude={"state_of_charge_percent"}))
    db.add(record)
    db.commit()
    db.refresh(record)
    return {"record_id": record.id, "asset_id": asset_id}


@app.get(
    "/assetslist",
    dependencies=[Depends(verify_api_key)],
    tags=["Assets"],
    summary="List all assets with their latest reading",
    description="Returns every asset that has at least one telemetry record, joined with its "
                "most recent state-of-charge reading. Assets with no telemetry yet are omitted.",
    response_description="One entry per asset, combining its static fields (incl. GPS/edge_id) with its latest reading.",
    responses={200: {"content": {"application/json": {"example": [
        {
            "id": 1,
            "asset_type": "battery",
            "eic_code": "17W-TM2XL-001---X",
            "name": "Tesla Megapack 2 XL - Unit 01",
            "max_capacity_mwh": 4.0,
            "max_charge_rate_mw": 1.9,
            "max_discharge_rate_mw": 1.9,
            "reactive_power_capacity_mvar": 0.95,
            "efficiency": 0.94,
            "latitude": 48.7115,
            "longitude": 2.171,
            "edge_id": None,
            "soc_id": 207360,
            "operational_mode": "active",
            "asset_status": "communicating",
            "energy_mwh": 3.8,
            "power_mw": -1.4494,
            "reactive_power_mvar": -0.2391,
            "power_factor": 0.9867,
            "last_updated": "2026-09-13T08:31:57.835236+00:00",
        }
    ]}}}},
)
def get_assets(db: Session = Depends(get_db)):

    latest_soc = (
        db.query(
            StateOfCharge.asset_id,
            func.max(StateOfCharge.timestamp).label("latest_ts")
        )
        .group_by(StateOfCharge.asset_id)
        .subquery()
    )

    results = (
        db.query(Asset, StateOfCharge)
        .join(latest_soc, Asset.id == latest_soc.c.asset_id)
        .join(StateOfCharge, (StateOfCharge.asset_id == Asset.id) &
                             (StateOfCharge.timestamp == latest_soc.c.latest_ts))
        .all()
    )

    return [
        {
            "id":                           asset.id,
            "asset_type":                   asset.asset_type,
            "eic_code":                     asset.eic_code,
            "name":                         asset.name,
            "max_capacity_mwh":             asset.max_capacity_mwh,
            "max_charge_rate_mw":           asset.max_charge_rate_mw,
            "max_discharge_rate_mw":        asset.max_discharge_rate_mw,
            "reactive_power_capacity_mvar": asset.reactive_power_capacity_mvar,
            "efficiency":                   asset.efficiency,
            "latitude":                     asset.latitude,
            "longitude":                    asset.longitude,
            "edge_id":                      asset.edge_id,
            "soc_id":                       soc.id,
            "operational_mode":             soc.operational_mode.value if soc.operational_mode else None,
            "asset_status":                 soc.asset_status.value if soc.asset_status else None,
            "energy_mwh":                   soc.energy_mwh,
            "power_mw":                     soc.power_mw,
            "reactive_power_mvar":          soc.reactive_power_mvar,
            "power_factor":                 soc.power_factor,
            "last_updated":                 soc.timestamp.isoformat(),
        }
        for asset, soc in results
    ]


@app.get(
    "/assets/summary",
    dependencies=[Depends(verify_api_key)],
    tags=["Assets"],
    summary="Fleet-wide power/energy summary",
    description="Aggregates the latest reading of every asset into fleet-wide totals, "
                "broken down by asset type (battery/solar/wind).",
    response_description="Total power/energy/reactive power, overall and per asset type.",
    responses={200: {"content": {"application/json": {"example": {
        "total_power_mw": 812.4,
        "total_energy_mwh": 58.2,
        "total_reactive_mvar": 12.7,
        "by_asset_type": {
            "all": {"power_mw": 812.4, "energy_mwh": 58.2, "asset_count": 48},
            "battery": {"power_mw": -14.2, "energy_mwh": 58.2, "asset_count": 30},
            "solar": {"power_mw": 320.1, "energy_mwh": 0.0, "asset_count": 9},
            "wind": {"power_mw": 506.5, "energy_mwh": 0.0, "asset_count": 9},
        },
    }}}}},
)
def get_asset_summary(db: Session = Depends(get_db)):

    latest_soc = (
        db.query(
            StateOfCharge.asset_id,
            func.max(StateOfCharge.timestamp).label("latest_ts")
        )
        .group_by(StateOfCharge.asset_id)
        .subquery()
    )

    results = (
        db.query(Asset, StateOfCharge)
        .join(latest_soc, Asset.id == latest_soc.c.asset_id)
        .join(StateOfCharge, (StateOfCharge.asset_id == Asset.id) &
                             (StateOfCharge.timestamp == latest_soc.c.latest_ts))
        .all()
    )

    total_power_mw      = sum(soc.power_mw or 0.0 for _, soc in results)
    total_energy_mwh    = sum(soc.energy_mwh or 0.0 for _, soc in results)
    total_reactive_mvar = sum(soc.reactive_power_mvar or 0.0 for _, soc in results)

    by_type = {}
    for asset, soc in results:
        t = asset.asset_type.value
        if t not in by_type:
            by_type[t] = {"power_mw": 0.0, "energy_mwh": 0.0, "asset_count": 0}
        by_type[t]["power_mw"]    += soc.power_mw or 0.0
        by_type[t]["energy_mwh"]  += soc.energy_mwh or 0.0
        by_type[t]["asset_count"] += 1

    return {
        "total_power_mw":      round(total_power_mw, 3),
        "total_energy_mwh":    round(total_energy_mwh, 3),
        "total_reactive_mvar": round(total_reactive_mvar, 3),
        "by_asset_type": {
            "all": {
                "power_mw":    round(total_power_mw, 3),
                "energy_mwh":  round(total_energy_mwh, 3),
                "asset_count": len(results),
            },
            **{
                k: {
                    "power_mw":    round(v["power_mw"], 3),
                    "energy_mwh":  round(v["energy_mwh"], 3),
                    "asset_count": v["asset_count"],
                }
                for k, v in by_type.items()
            }
        }
    }


@app.get(
    "/assets/{asset_id}/soc",
    dependencies=[Depends(verify_api_key)],
    tags=["Assets"],
    summary="Get an asset's state-of-charge/telemetry history",
    description="Two modes: `S` returns only the latest record; `D` returns history over a "
                "time range. In `D` mode, ranges over 2 days are automatically downsampled "
                "(via TimescaleDB `time_bucket`) to roughly `limit` points instead of returning "
                "every raw record.",
    response_description="Asset metadata (incl. GPS/edge_id) plus one record (mode S) or a list of records (mode D).",
    responses={
        200: {"content": {"application/json": {"examples": {
            "mode_S": {
                "summary": "mode=S — latest record only",
                "value": {
                    "asset_id": 1,
                    "asset_name": "Tesla Megapack 2 XL - Unit 01",
                    "eic_code": "17W-TM2XL-001---X",
                    "asset_type": "battery",
                    "max_capacity_mwh": 4.0,
                    "latitude": 48.7115,
                    "longitude": 2.171,
                    "edge_id": None,
                    "record": {
                        "timestamp": "2026-09-13T08:31:57.835236+00:00",
                        "operational_mode": "active",
                        "asset_status": "communicating",
                        "energy_mwh": 3.8,
                        "power_mw": -1.4494,
                        "reactive_power_mvar": -0.2391,
                        "power_factor": 0.9867,
                        "voltage": 1392.2,
                        "current_amps": 1.04,
                        "temperature_celsius": 32.1,
                    },
                },
            },
            "mode_D_downsampled": {
                "summary": "mode=D — range >2 days, downsampled via time_bucket",
                "value": {
                    "asset_id": 1,
                    "asset_name": "Tesla Megapack 2 XL - Unit 01",
                    "eic_code": "17W-TM2XL-001---X",
                    "asset_type": "battery",
                    "max_capacity_mwh": 4.0,
                    "latitude": 48.7115,
                    "longitude": 2.171,
                    "edge_id": None,
                    "record_count": 51,
                    "resolution_minutes": 836,
                    "downsampled": True,
                    "from_ts": "2026-08-01T00:00:00",
                    "to_ts": "2026-08-30T00:00:00",
                    "records": [
                        {
                            "timestamp": "2026-07-31T21:20:00+00:00",
                            "energy_mwh": 3.186,
                            "power_mw": 0.539,
                            "reactive_power_mvar": -0.004,
                            "power_factor": 0.989,
                            "voltage": 1397.16,
                            "current_amps": 0.911,
                            "temperature_celsius": 32.62,
                        }
                    ],
                },
            },
        }}}},
        400: {"description": "mode is neither S nor D"},
        404: {"description": "Asset not found, or no state-of-charge records in range"},
    },
)
def get_asset_soc(
    asset_id: int = Path(..., description="Id of the asset to query.", examples=[1]),
    mode: str = Query(..., description="`S` for summary (latest record only), `D` for detail (history).", examples=["D"]),
    from_ts: Optional[str] = Query(None, description="ISO datetime start of the range (mode D only). Defaults to 2 days before to_ts.", examples=["2026-04-25T00:00:00"]),
    to_ts: Optional[str] = Query(None, description="ISO datetime end of the range (mode D only). Defaults to now.", examples=["2026-05-02T23:59:59"]),
    limit: int = Query(288, description="Target max number of records in D mode — default 288 = 24h at 10-min intervals. Ranges beyond 2 days are downsampled to roughly this many points.", examples=[288]),
    db: Session = Depends(get_db)
):
    asset = db.query(Asset).filter(Asset.id == asset_id).first()

    if not asset:
        raise HTTPException(status_code=404, detail=f"Asset {asset_id} not found")

    if mode.upper() == "S":
        record = (
            db.query(StateOfCharge)
            .filter(StateOfCharge.asset_id == asset_id)
            .order_by(StateOfCharge.timestamp.desc())
            .first()
        )
        if not record:
            raise HTTPException(status_code=404, detail=f"No state of charge records found for asset {asset_id}")

        return {
            "asset_id":         asset.id,
            "asset_name":       asset.name,
            "eic_code":         asset.eic_code,
            "asset_type":       asset.asset_type.value,
            "max_capacity_mwh": asset.max_capacity_mwh,
            "latitude":         asset.latitude,
            "longitude":        asset.longitude,
            "edge_id":          asset.edge_id,
            "record": {
                "timestamp":           record.timestamp.isoformat(),
                "operational_mode":    record.operational_mode.value if record.operational_mode else None,
                "asset_status":        record.asset_status.value if record.asset_status else None,
                "energy_mwh":          record.energy_mwh,
                "power_mw":            record.power_mw,
                "reactive_power_mvar": record.reactive_power_mvar,
                "power_factor":        record.power_factor,
                "voltage":             record.voltage,
                "current_amps":        record.current_amps,
                "temperature_celsius": record.temperature_celsius,
            }
        }

    elif mode.upper() == "D":
        if not to_ts:
            to_dt = datetime.utcnow()
        else:
            to_dt = datetime.fromisoformat(to_ts)
            
        if not from_ts:
            from_dt = to_dt - timedelta(days=2)
        else:
            from_dt = datetime.fromisoformat(from_ts)

        delta_days     = (to_dt - from_dt).days
        bucket_minutes = ceil((delta_days * 24 * 60) / limit)

        if delta_days <= 2:
            records = (
                db.query(StateOfCharge)
                .filter(StateOfCharge.asset_id == asset_id)
                .filter(StateOfCharge.timestamp >= from_dt)
                .filter(StateOfCharge.timestamp <= to_dt)
                .order_by(StateOfCharge.timestamp.desc())
                .limit(limit)
                .all()
            )

            if not records:
                raise HTTPException(status_code=404, detail=f"No state of charge records found for asset {asset_id}")

            return {
                "asset_id":           asset.id,
                "asset_name":         asset.name,
                "eic_code":           asset.eic_code,
                "asset_type":         asset.asset_type.value,
                "max_capacity_mwh":   asset.max_capacity_mwh,
                "latitude":           asset.latitude,
                "longitude":          asset.longitude,
                "edge_id":            asset.edge_id,
                "record_count":       len(records),
                "resolution_minutes": 10,
                "downsampled":        False,
                "from_ts": from_dt.isoformat(),
                "to_ts":   to_dt.isoformat(),
                "records": [
                    {
                        "timestamp":           r.timestamp.isoformat(),
                        "operational_mode":    r.operational_mode.value if r.operational_mode else None,
                        "asset_status":        r.asset_status.value if r.asset_status else None,
                        "energy_mwh":          r.energy_mwh,
                        "power_mw":            r.power_mw,
                        "reactive_power_mvar": r.reactive_power_mvar,
                        "power_factor":        r.power_factor,
                        "voltage":             r.voltage,
                        "current_amps":        r.current_amps,
                        "temperature_celsius": r.temperature_celsius,
                    }
                    for r in records
                ]
            }

        else:
            sql = text("""
                SELECT
                    time_bucket(:bucket, timestamp) AS bucket,
                    AVG(energy_mwh)          AS energy_mwh,
                    AVG(power_mw)            AS power_mw,
                    AVG(reactive_power_mvar) AS reactive_power_mvar,
                    AVG(power_factor)        AS power_factor,
                    AVG(voltage)             AS voltage,
                    AVG(current_amps)        AS current_amps,
                    AVG(temperature_celsius) AS temperature_celsius
                FROM state_of_charge
                WHERE asset_id = :asset_id
                AND timestamp BETWEEN :from_dt AND :to_dt
                GROUP BY bucket
                ORDER BY bucket
            """)

            rows = db.execute(sql, {
                "bucket":   f"{bucket_minutes} minutes",
                "asset_id": asset_id,
                "from_dt":  from_dt,
                "to_dt":    to_dt
            }).fetchall()

            if not rows:
                raise HTTPException(status_code=404, detail=f"No state of charge records found for asset {asset_id}")

            return {
                "asset_id":           asset.id,
                "asset_name":         asset.name,
                "eic_code":           asset.eic_code,
                "asset_type":         asset.asset_type.value,
                "max_capacity_mwh":   asset.max_capacity_mwh,
                "latitude":           asset.latitude,
                "longitude":          asset.longitude,
                "edge_id":            asset.edge_id,
                "record_count":       len(rows),
                "resolution_minutes": bucket_minutes,
                "downsampled":        True,
                "from_ts":            from_dt.isoformat(),
                "to_ts":              to_dt.isoformat(),
                "records": [
                    {
                        "timestamp":           row.bucket.isoformat(),
                        "energy_mwh":          row.energy_mwh,
                        "power_mw":            row.power_mw,
                        "reactive_power_mvar": row.reactive_power_mvar,
                        "power_factor":        row.power_factor,
                        "voltage":             row.voltage,
                        "current_amps":        row.current_amps,
                        "temperature_celsius": row.temperature_celsius,
                    }
                    for row in rows
                ]
            }

    else:
        raise HTTPException(status_code=400, detail="mode must be S (summary) or D (detail)")


@app.post(
    "/llm/ask",
    dependencies=[Depends(verify_api_key)],
    tags=["LLM"],
    summary="Ask a natural-language question about the grid",
    description="Streams back a plain-text answer from the LLM, with live battery asset "
                "data from the database injected into its system prompt.",
    response_description="A streaming plain-text response, token by token.",
    responses={200: {"content": {"text/plain": {"example": "The Tesla Megapack 2 XL - Unit 01 is currently at 3.8 MWh (95% of its 4.0 MWh capacity) and discharging at 1.45 MW."}}}},
)
def ask_llm(question: str = Query(..., description="Natural-language question about the grid/assets.", examples=["What's the current state of charge of the Tesla Megapack units?"])):
    return StreamingResponse(
        ask_grid_question_stream(question),
        media_type="text/plain"
    )
