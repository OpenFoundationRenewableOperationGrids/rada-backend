"""
telemetry_simulator.py

Background simulator that posts a new telemetry record for every asset
in the database every 10 minutes. Controlled via .env:

    TELEMETRY_SIMULATOR=true
    SIMULATOR_INTERVAL_SEC=600   # optional, defaults to 600
    API_BASE_URL=http://localhost:8000  # optional

Called from main.py lifespan — not intended to be run standalone.
"""

import os
import time
import random
import logging
import requests
from math import pi, sin
from datetime import datetime, timezone

log = logging.getLogger("simulator")

API_BASE_URL  = os.getenv("API_BASE_URL", "http://localhost:8000")
INTERVAL_SEC  = int(os.getenv("SIMULATOR_INTERVAL_SEC", 600))
API_KEY       = os.getenv("API_KEY")
HEADERS       = {"X-API-Key": API_KEY}

# Per-asset state cache {asset_id: {field: last_value}}
_state: dict[int, dict] = {}


# ---------------------------------------------------------------------------
# Time-of-day factors
# ---------------------------------------------------------------------------

def solar_factor(hour: float) -> float:
    if hour < 6.0 or hour > 21.0:
        return 0.0
    return max(0.0, sin((hour - 6.0) / (21.0 - 6.0) * pi))


def battery_load_factor(hour: float) -> float:
    if 0 <= hour < 6:
        return -0.6   # charging overnight
    elif 6 <= hour < 7:
        return -0.2
    elif 7 <= hour < 10:
        return 0.7    # morning discharge
    elif 10 <= hour < 11:
        return 0.3
    elif 11 <= hour < 15:
        return -0.3   # midday solar absorption
    elif 15 <= hour < 16:
        return 0.2
    elif 16 <= hour < 21:
        return 0.85   # evening peak discharge
    else:
        return -0.1


def wind_factor(hour: float) -> float:
    return 0.85 if (hour < 6 or hour >= 20) else 0.65


# ---------------------------------------------------------------------------
# Operational mode logic
# ---------------------------------------------------------------------------

def derive_operational_mode(asset_type: str, power_mw: float, hour: float) -> str:
    if asset_type == "battery":
        if power_mw < -0.1 or power_mw > 0.1:
            return "active"
        else:
            return "curtailed"
    elif asset_type == "solar":
        return "curtailed" if solar_factor(hour) < 0.05 else "active"
    else:  # wind
        return "curtailed" if abs(power_mw) < 0.5 else "active"


# ---------------------------------------------------------------------------
# State initialisation and walk
# ---------------------------------------------------------------------------

def _init_state(asset: dict) -> dict:
    atype = asset["asset_type"]
    cap   = asset["max_capacity_mwh"]
    hour  = datetime.now(timezone.utc).hour

    if atype == "battery":
        energy = cap * random.uniform(0.3, 0.7)
        power  = asset["max_discharge_rate_mw"] * battery_load_factor(hour) * random.uniform(0.8, 1.0)
    elif atype == "solar":
        sf     = solar_factor(hour)
        power  = asset["max_discharge_rate_mw"] * sf * random.uniform(0.85, 1.0)
        energy = 0.0  # solar has no storage
    else:  # wind
        wf     = wind_factor(hour)
        power  = asset["max_discharge_rate_mw"] * wf * random.uniform(0.7, 1.0)
        energy = 0.0  # wind has no storage

    return {
        "energy_mwh":          round(energy, 3),
        "power_mw":            round(power, 3),
        "reactive_power_mvar": round(random.uniform(0.5, asset.get("reactive_power_capacity_mvar") or 5.0), 3),
        "power_factor":        round(random.uniform(0.97, 1.0), 4),
        "voltage":             round(random.uniform(395.0, 405.0), 2),
        "temperature_celsius": round(random.uniform(20.0, 35.0), 1) ,
    }


def _walk(prev: float, delta: float, lo: float, hi: float) -> float:
    return round(max(lo, min(hi, prev + random.uniform(-delta, delta))), 4)


def next_telemetry(asset: dict) -> dict:
    aid   = asset["id"]
    atype = asset["asset_type"]
    cap   = asset["max_capacity_mwh"]
    max_d = asset["max_discharge_rate_mw"]
    max_c = asset["max_charge_rate_mw"]
    max_q = asset.get("reactive_power_capacity_mvar") or 5.0
    now   = datetime.now(timezone.utc)
    hour  = now.hour + now.minute / 60.0

    if aid not in _state:
        _state[aid] = _init_state(asset)

    s = _state[aid]

    # Power — walk toward time-of-day target
    if atype == "battery":
        target = max_d * battery_load_factor(hour)
        power  = round(max(-max_c, min(max_d, s["power_mw"] + (target - s["power_mw"]) * 0.15 + random.uniform(-max_d * 0.03, max_d * 0.03))), 3)
    elif atype == "solar":
        target = max_d * solar_factor(hour)
        power  = round(max(0.0, min(max_d, s["power_mw"] + (target - s["power_mw"]) * 0.2 + random.uniform(-max_d * 0.02, max_d * 0.02))), 3)
    else:  # wind
        target = max_d * wind_factor(hour)
        power  = round(max(0.0, min(max_d, s["power_mw"] + (target - s["power_mw"]) * 0.1 + random.uniform(-max_d * 0.08, max_d * 0.08))), 3)

    # Energy — batteries only; solar and wind have no storage
    if atype == "battery":
        energy = round(max(0.0, min(cap, s["energy_mwh"] - power * (INTERVAL_SEC / 3600.0))), 3)
    else:
        energy = 0.0

    # Electrical
    voltage = _walk(s["voltage"], 1.5, 390.0, 415.0)
    current = round(abs(power) * 1000.0 / voltage, 2) if voltage > 0 else 0.0
    q_power = _walk(s["reactive_power_mvar"], 0.3, 0.0, max_q)
    pf      = _walk(s["power_factor"], 0.005, 0.95, 1.0)

    # Temperature — rises with load (battery) or with sun/wind exposure
    if atype == "battery":
        load_ratio  = abs(power) / max_d if max_d > 0 else 0
        temp_target = 25.0 + load_ratio * 15.0
    elif atype == "solar":
        temp_target = 20.0 + solar_factor(hour) * 25.0   # panels run well above ambient under full sun
    else:  # wind
        temp_target = 15.0 + wind_factor(hour) * 5.0     # nacelle temp, mostly ambient with mild load coupling

    temp = round(_walk(s["temperature_celsius"] or 25.0, 1.5, 15.0, 55.0) + (temp_target - (s["temperature_celsius"] or 25.0)) * 0.05, 2)

    # SoC percent — batteries only, never sent for solar/wind
    soc_pct = round((energy / cap) * 100.0, 2) if atype == "battery" and cap > 0 else None

    # 1-in-100 chance of fault
    if random.randint(1, 100) == 1:
        operational_mode = "fault"
        asset_status     = "unreachable"
    else:
        operational_mode = derive_operational_mode(atype, power, hour)
        asset_status     = "communicating"

    _state[aid] = {
        "energy_mwh":          energy,
        "power_mw":            power,
        "reactive_power_mvar": q_power,
        "power_factor":        pf,
        "voltage":             voltage,
        "temperature_celsius": temp,
    }

    payload = {
        "timestamp":           now.isoformat(),
        "energy_mwh":          energy,
        "power_mw":            power,
        "operational_mode":    operational_mode,
        "asset_status":        asset_status,
        "reactive_power_mvar": q_power,
        "power_factor":        pf,
        "voltage":             voltage,
        "current_amps":        current,
    }

    # Only add optional fields when they have a meaningful value
    if temp is not None:
        payload["temperature_celsius"] = temp
    if soc_pct is not None:
        payload["state_of_charge_percent"] = soc_pct

    return payload


# ---------------------------------------------------------------------------
# API calls
# ---------------------------------------------------------------------------

def _fetch_assets() -> list[dict]:
    try:
        r = requests.get(f"{API_BASE_URL}/assetslist", headers=HEADERS, timeout=10)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.error("Failed to fetch assets: %s", e)
        return []


def simulated_assets(assets: list[dict]) -> list[dict]:
    """The assets the simulator should feed: those with no real source.

    An asset with an edge_id is fed by the OpenEMS bridge (`python -m bridge`);
    simulating it too would mix invented readings with real ones.
    """
    return [asset for asset in assets if not asset.get("edge_id")]


def _post_telemetry(asset_id: int, payload: dict) -> bool:
    try:
        r = requests.post(f"{API_BASE_URL}/assets/{asset_id}/telemetry", headers=HEADERS, json=payload, timeout=10)
        r.raise_for_status()
        return True
    except Exception as e:
        log.warning("Telemetry post failed for asset %s: %s", asset_id, e)
        return False


# ---------------------------------------------------------------------------
# Main loop — called from main.py lifespan in a daemon thread
# ---------------------------------------------------------------------------

def run():
    log.info("Simulator started — interval %ss, target %s", INTERVAL_SEC, API_BASE_URL)

    while True:
        tick_start = time.monotonic()
        assets     = simulated_assets(_fetch_assets())

        if not assets:
            log.warning("No assets to simulate — retrying next tick")
        else:
            ok = fail = 0
            for asset in assets:
                if _post_telemetry(asset["id"], next_telemetry(asset)):
                    ok += 1
                else:
                    fail += 1
                time.sleep(0.1)  # avoid overwhelming the API thread pool
            log.info("Tick — %d posted, %d failed (of %d assets)", ok, fail, len(assets))

        elapsed = time.monotonic() - tick_start
        try:
            time.sleep(max(0.0, INTERVAL_SEC - elapsed))
        except KeyboardInterrupt:
            break

    log.info("Simulator stopped")
