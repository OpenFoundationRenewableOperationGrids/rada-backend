"""
bridge/translate.py

Turns OpenEMS channel values into the payload of RADA's existing endpoint
POST /assets/{asset_id}/telemetry. Python computes: every number posted is
worked out here, from the channels, with no guesswork.

Temporary until BE-03/BE-05: the generic /measurements endpoint will take
the raw channels (W, Wh, %) instead, and this file will shrink to a mapping.

Units: OpenEMS reports W, Wh and %. The current API takes MW and MWh, with
the same sign convention as OpenEMS for the battery (negative = charging,
positive = discharging) and positive = producing for solar.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from bridge.config import AssetConfig
from bridge.source import Reading

# Which channels of the asset's component each kind needs, in OpenEMS names.
CHANNELS_BY_KIND: dict[str, tuple[str, ...]] = {
    "battery": ("ActivePower", "Soc", "Capacity"),   # W, %, Wh
    "solar": ("ActivePower",),                       # W
}

# A value older than this is not posted: the edge has probably stopped sending.
DEFAULT_MAX_AGE = timedelta(seconds=30)

W_PER_MW = 1_000_000
WH_PER_MWH = 1_000_000


def channels_for(asset: AssetConfig) -> list[str]:
    """Channel addresses for one asset, e.g. ["ess0/ActivePower", "ess0/Soc", "ess0/Capacity"]."""
    return [f"{asset.component}/{channel}" for channel in CHANNELS_BY_KIND[asset.kind]]


def all_channels(assets: tuple[AssetConfig, ...] | list[AssetConfig]) -> list[str]:
    """Every channel to subscribe to for these assets, without duplicates."""
    return sorted({address for asset in assets for address in channels_for(asset)})


class Snapshot:
    """The latest reading of every channel, per edge. Updated every 2 s by the
    source, read every post interval by the publisher."""

    def __init__(self) -> None:
        self._latest: dict[tuple[str, str], Reading] = {}

    def update(self, batch: list[Reading]) -> None:
        for reading in batch:
            self._latest[(reading.edge_id, reading.address)] = reading

    def get(self, edge_id: str, address: str) -> Reading | None:
        return self._latest.get((edge_id, address))


@dataclass(frozen=True)
class Skipped:
    """Why an asset has nothing to post this round."""

    reason: str


def to_telemetry(
    asset: AssetConfig,
    snapshot: Snapshot,
    now: datetime,
    max_age: timedelta = DEFAULT_MAX_AGE,
) -> dict | Skipped:
    """Build the telemetry payload for one asset, or say why it can't be built."""
    readings: dict[str, Reading] = {}
    for channel in CHANNELS_BY_KIND[asset.kind]:
        address = f"{asset.component}/{channel}"
        reading = snapshot.get(asset.edge_id, address)
        if reading is None:
            return Skipped(f"no value received yet for {address}")
        if reading.value is None:
            return Skipped(f"{address} has no value on the edge")
        if now - reading.time > max_age:
            return Skipped(f"{address} is stale (last received {reading.time.isoformat()})")
        readings[channel] = reading

    # The payload's time is that of the newest value used.
    timestamp = max(r.time for r in readings.values())
    power_w = readings["ActivePower"].value

    payload = {
        "timestamp": timestamp.isoformat(),
        "power_mw": round(power_w / W_PER_MW, 6),
        "asset_status": "communicating",
    }

    if asset.kind == "battery":
        soc_percent = readings["Soc"].value
        capacity_wh = readings["Capacity"].value
        payload["energy_mwh"] = round(soc_percent / 100 * capacity_wh / WH_PER_MWH, 6)
        payload["state_of_charge_percent"] = soc_percent
    else:
        # The endpoint asks for energy_mwh; for solar it is 0 by convention.
        payload["energy_mwh"] = 0.0

    return payload
