import os
import threading
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends, Query, HTTPException, Security
from fastapi.security.api_key import APIKeyHeader
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session
from sqlalchemy import func, text
from pydantic import BaseModel
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
    eic_code: str
    name: str
    asset_type: AssetType
    max_capacity_mwh: float
    max_charge_rate_mw: float
    max_discharge_rate_mw: float
    reactive_power_capacity_mvar: Optional[float] = None
    efficiency: Optional[float] = None

class TelemetryCreate(BaseModel):
    timestamp: datetime
    energy_mwh: float
    power_mw: float
    operational_mode: Optional[GridConnectionStatus] = None
    asset_status: Optional[AssetStatus] = None
    reactive_power_mvar: Optional[float] = None
    power_factor: Optional[float] = None
    voltage: Optional[float] = None
    current_amps: Optional[float] = None
    temperature_celsius: Optional[float] = None
    state_of_charge_percent: Optional[float] = None  # validated but not stored


# --- Endpoints ---

@app.get("/")
def read_root():
    return {"message": "Asset Grid Manager API", "status": "running"}

@app.get("/health")
def health_check():
    return {"status": "healthy"}


@app.post("/assets", status_code=201, dependencies=[Depends(verify_api_key)])
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


@app.post("/assets/{asset_id}/telemetry", status_code=201, dependencies=[Depends(verify_api_key)])
def add_telemetry(asset_id: int, payload: TelemetryCreate, db: Session = Depends(get_db)):
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


@app.get("/assetslist", dependencies=[Depends(verify_api_key)])
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


@app.get("/assets/summary", dependencies=[Depends(verify_api_key)])
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


@app.get("/assets/{asset_id}/soc", dependencies=[Depends(verify_api_key)])
def get_asset_soc(
    asset_id: int,
    mode: str = Query(..., description="S for summary (latest record), D for detail (history)"),
    from_ts: Optional[str] = Query(None, description="ISO datetime start e.g. 2026-04-25T00:00:00"),
    to_ts: Optional[str] = Query(None, description="ISO datetime end e.g. 2026-05-02T23:59:59"),
    limit: int = Query(288, description="Max records in D mode — default 288 = 24hrs at 10min intervals"),
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


@app.post("/llm/ask", dependencies=[Depends(verify_api_key)])
def ask_llm(question: str):
    return StreamingResponse(
        ask_grid_question_stream(question),
        media_type="text/plain"
    )
