"""Unit tests for bridge/translate.py, using the values seen on edge0 on 7 Nov 2026."""

from datetime import datetime, timedelta, timezone

from bridge.config import AssetConfig
from bridge.source import Reading
from bridge.translate import Skipped, Snapshot, all_channels, channels_for, to_telemetry

T0 = datetime(2026, 11, 7, 13, 50, tzinfo=timezone.utc)
BATTERY = AssetConfig(asset_id=1, edge_id="edge0", component="ess0", kind="battery")
SOLAR = AssetConfig(asset_id=2, edge_id="edge0", component="meter1", kind="solar")


def snapshot_with(values, time=T0, edge_id="edge0"):
    snap = Snapshot()
    snap.update([Reading(time=time, edge_id=edge_id, address=a, value=v) for a, v in values.items()])
    return snap


def test_channels_per_kind():
    assert channels_for(BATTERY) == ["ess0/ActivePower", "ess0/Soc", "ess0/Capacity"]
    assert channels_for(SOLAR) == ["meter1/ActivePower"]


def test_all_channels_without_duplicates():
    twin = AssetConfig(asset_id=3, edge_id="edge1", component="ess0", kind="battery")

    assert all_channels([BATTERY, SOLAR, twin]) == [
        "ess0/ActivePower", "ess0/Capacity", "ess0/Soc", "meter1/ActivePower",
    ]


def test_battery_charging():
    # 21 % of 8 MWh, charging at 700 kW: the 7 Nov test with a +200 kW grid setpoint.
    snap = snapshot_with({"ess0/ActivePower": -700_000, "ess0/Soc": 21, "ess0/Capacity": 8_000_000})

    payload = to_telemetry(BATTERY, snap, now=T0 + timedelta(seconds=5))

    assert payload == {
        "timestamp": "2026-11-07T13:50:00+00:00",
        "power_mw": -0.7,                    # negative = charging, as in OpenEMS
        "energy_mwh": 1.68,                  # 21 / 100 x 8 MWh
        "state_of_charge_percent": 21,
        "asset_status": "communicating",
    }


def test_battery_discharging_is_positive():
    snap = snapshot_with({"ess0/ActivePower": 500_000, "ess0/Soc": 100, "ess0/Capacity": 8_000_000})

    payload = to_telemetry(BATTERY, snap, now=T0)

    assert payload["power_mw"] == 0.5
    assert payload["energy_mwh"] == 8.0


def test_solar_producing():
    snap = snapshot_with({"meter1/ActivePower": 1_500_000})

    payload = to_telemetry(SOLAR, snap, now=T0)

    assert payload["power_mw"] == 1.5
    assert payload["energy_mwh"] == 0.0
    assert "state_of_charge_percent" not in payload   # the API refuses it for solar


def test_timestamp_is_the_newest_value_used():
    snap = snapshot_with({"ess0/Soc": 21, "ess0/Capacity": 8_000_000})
    snap.update([Reading(time=T0 + timedelta(seconds=2), edge_id="edge0",
                         address="ess0/ActivePower", value=-700_000)])

    payload = to_telemetry(BATTERY, snap, now=T0 + timedelta(seconds=3))

    assert payload["timestamp"] == "2026-11-07T13:50:02+00:00"


def test_skipped_when_a_channel_never_arrived():
    snap = snapshot_with({"ess0/ActivePower": -700_000, "ess0/Soc": 21})   # no Capacity

    result = to_telemetry(BATTERY, snap, now=T0)

    assert isinstance(result, Skipped)
    assert "ess0/Capacity" in result.reason


def test_skipped_when_the_edge_has_no_value():
    snap = snapshot_with({"meter1/ActivePower": None})

    result = to_telemetry(SOLAR, snap, now=T0)

    assert isinstance(result, Skipped)
    assert "no value" in result.reason


def test_skipped_when_stale():
    snap = snapshot_with({"meter1/ActivePower": 1_500_000})

    result = to_telemetry(SOLAR, snap, now=T0 + timedelta(seconds=31))

    assert isinstance(result, Skipped)
    assert "stale" in result.reason


def test_values_from_another_edge_are_not_mixed_up():
    snap = snapshot_with({"meter1/ActivePower": 1_500_000}, edge_id="edge1")

    assert isinstance(to_telemetry(SOLAR, snap, now=T0), Skipped)
