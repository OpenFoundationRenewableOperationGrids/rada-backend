"""
routers/measurements.py

Ingestion (docs/DATA_MODEL.md §8): POST /sites/{site_id}/measurements takes a
batch of {time, metric, asset_id, value} items, where metric is a catalogue
key. Vendor-neutral: the bridge, the simulator, the seed and the CSV import
all post this same shape.

A batch is all or nothing: every item is checked before anything is written,
and a 422 lists every invalid item. A value already stored (same time, site,
asset and metric) is ignored, not an error. A post moves the site's
last_seen_at forward, never back.
"""

from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Path
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth.deps import require_ingest
from database import get_db
from models import Asset, Metric, MetricLevel, Site, measurement
from routers.errors import field_error, unprocessable

router = APIRouter(tags=["Measurements"])

# One batch is one INSERT: 6 bind parameters per row, well under PostgreSQL's 65,535
MAX_BATCH_SIZE = 5000
# Allowance for clocks that run a little ahead; anything later is refused
MAX_FUTURE_SKEW = timedelta(minutes=5)
# The rules the database enforces as well, should a row get past the checks below
# (an asset moved to another site between the check and the insert, for example)
SAFETY_NET_CONSTRAINTS = {"ck_metric_level", "fk_measurement_asset_site"}


# --- Schemas ---

class MeasurementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: AwareDatetime = Field(..., description="With a time zone or offset; stored in UTC.",
                                examples=["2026-11-25T10:00:00Z"])
    metric: str = Field(..., min_length=1, max_length=64, description="A key from GET /metrics.",
                        examples=["battery_power"])
    asset_id: Optional[int] = Field(None, description="Empty for a site-wide value.", examples=[49])
    value: float = Field(..., allow_inf_nan=False, description="In the metric's unit.", examples=[-800.0])

    @field_validator("time")
    @classmethod
    def to_utc(cls, value: datetime) -> datetime:
        return value.astimezone(timezone.utc)


class MeasurementBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["bridge-openems", "simulator", "seed", "csv"] = Field(
        ..., description="Who writes the batch; stored on every row.")
    items: list[MeasurementIn] = Field(..., min_length=1, max_length=MAX_BATCH_SIZE)


class MeasurementResult(BaseModel):
    inserted: int = Field(..., description="Rows stored.", examples=[2])
    ignored: int = Field(..., description="Items already stored (same time, site, asset and metric).", examples=[0])


# --- Checks ---

def item_errors(db: Session, site_id: int, items: list[MeasurementIn], metrics: dict[str, Metric]) -> list[dict]:
    """Every reason an item cannot be stored, as 422 entries. Empty when the batch is valid."""
    asset_ids = {item.asset_id for item in items if item.asset_id is not None}
    assets_on_site = set(db.scalars(select(Asset.id).where(Asset.site_id == site_id, Asset.id.in_(asset_ids))))
    latest_allowed = datetime.now(timezone.utc) + MAX_FUTURE_SKEW

    errors = []
    for index, item in enumerate(items):
        loc = ("body", "items", index)
        metric = metrics.get(item.metric)
        if metric is None:
            errors.append(field_error(loc + ("metric",), "unknown_metric",
                                      f"Unknown metric {item.metric!r}: see GET /metrics", item.metric))
        elif not metric.is_active:
            errors.append(field_error(loc + ("metric",), "inactive_metric",
                                      f"Metric {item.metric!r} is inactive", item.metric))
        elif metric.level is MetricLevel.SITE and item.asset_id is not None:
            errors.append(field_error(loc + ("asset_id",), "metric_level",
                                      f"Metric {item.metric!r} is site-level: asset_id must be empty", item.asset_id))
        elif metric.level is MetricLevel.ASSET and item.asset_id is None:
            errors.append(field_error(loc + ("asset_id",), "metric_level",
                                      f"Metric {item.metric!r} is asset-level: asset_id is required", None))
        if item.asset_id is not None and item.asset_id not in assets_on_site:
            errors.append(field_error(loc + ("asset_id",), "asset_not_on_site",
                                      f"Asset {item.asset_id} is not on site {site_id}", item.asset_id))
        if item.time > latest_allowed:
            errors.append(field_error(loc + ("time",), "time_in_future",
                                      f"More than {MAX_FUTURE_SKEW} in the future", item.time))
    return errors


# --- Route ---

@router.post(
    "/sites/{site_id}/measurements", response_model=MeasurementResult,
    dependencies=[Depends(require_ingest)],
    summary="Post a batch of measurements",
    description=f"Up to {MAX_BATCH_SIZE} items, all stored or none. Each item's metric must exist and be active, "
                "its asset must be on this site, and the metric's level must allow the asset to be filled in "
                "(`asset`, `both`) or empty (`site`, `both`). Times need a time zone and may be at most "
                f"{MAX_FUTURE_SKEW} in the future. An item already stored is ignored and counted in `ignored`. "
                "The site's `last_seen_at` moves forward to the latest time stored.",
    responses={
        404: {"description": "Site not found"},
        422: {"description": "Invalid batch; `loc` gives the index of each invalid item. Nothing was stored"},
    },
)
def post_measurements(
    body: MeasurementBatch,
    site_id: int = Path(..., description="Id of the site the measurements belong to.", examples=[1]),
    db: Session = Depends(get_db),
):
    if db.get(Site, site_id) is None:
        raise HTTPException(status_code=404, detail=f"Site {site_id} not found")
    keys = {item.metric for item in body.items}
    metrics = {metric.key: metric for metric in db.query(Metric).filter(Metric.key.in_(keys))}
    errors = item_errors(db, site_id, body.items, metrics)
    if errors:
        raise unprocessable(errors)

    rows = [
        {"time": item.time, "site_id": site_id, "asset_id": item.asset_id,
         "metric_id": metrics[item.metric].id, "value": item.value, "source": body.source}
        for item in body.items
    ]
    statement = pg_insert(measurement).values(rows).on_conflict_do_nothing().returning(measurement.c.time)
    try:
        # RETURNING lists only the rows stored, not the duplicates ignored
        stored_times = db.execute(statement).scalars().all()
        if stored_times:
            # GREATEST skips a NULL, and never moves last_seen_at back for late data
            db.execute(
                update(Site).where(Site.id == site_id)
                .values(last_seen_at=func.greatest(Site.last_seen_at, max(stored_times)))
                .execution_options(synchronize_session=False)
            )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        if getattr(getattr(error.orig, "diag", None), "constraint_name", None) not in SAFETY_NET_CONSTRAINTS:
            raise
        raise unprocessable([field_error(("body", "items"), "rejected_by_database",
                                         f"Nothing was stored: {error.orig.diag.message_primary}")])
    return {"inserted": len(stored_times), "ignored": len(rows) - len(stored_times)}
