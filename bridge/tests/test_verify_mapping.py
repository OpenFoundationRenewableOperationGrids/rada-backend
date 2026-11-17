"""Tests for the BE-02 mapping verification script (pure functions only)."""

import pytest

from bridge.verify_mapping import (
    SEEDS_DIR,
    Row,
    balance_check,
    load_toml,
    missing_core,
    parse_asset,
    plan_rows,
    render_table,
)

TEMPLATE = load_toml(SEEDS_DIR / "openems_mapping.toml")
METRICS = {m["key"]: m for m in load_toml(SEEDS_DIR / "metrics.toml")["metric"]}


def test_plan_rows_site_then_assets_with_component_prefix():
    rows = plan_rows(TEMPLATE, [("battery", "ess0"), ("meter", "meter0")])
    assert rows[0].component == "_sum"
    assert Row("battery_power", "ess0", "ess0/ActivePower") in rows
    voltage = next(r for r in rows if r.address == "meter0/Voltage")
    assert voltage.scale == 0.001 and voltage.level == "asset"


def test_plan_rows_unknown_asset_type():
    with pytest.raises(ValueError, match="No template"):
        plan_rows(TEMPLATE, [("heatpump", "hp0")])


def test_every_template_metric_is_in_the_catalogue():
    rows = plan_rows(TEMPLATE, [(t, f"{t}0") for t in TEMPLATE["asset"]])
    assert {r.metric for r in rows} <= set(METRICS)


def test_render_table_scales_and_flags_missing_values():
    rows = [Row("voltage", "meter0", "meter0/Voltage", 0.001), Row("grid_power", "_sum", "_sum/GridActivePower")]
    table = render_table(rows, {"meter0/Voltage": 230_500}, METRICS)
    assert "230.5 V" in table
    assert "**no value**" in table


def test_missing_optional_metric_does_not_fail():
    rows = [Row("reactive_power", "meter0", "meter0/ReactivePower"),
            Row("grid_power", "_sum", "_sum/GridActivePower")]
    values = {"_sum/GridActivePower": -500_000}
    assert "no value (optional)" in render_table(rows, values, METRICS)
    assert missing_core(rows, values, METRICS) == []
    assert missing_core(rows, {}, METRICS) == ["_sum/GridActivePower"]


def test_balance_check_ok_and_check():
    ok = {"_sum/ConsumptionActivePower": 1_000_000, "_sum/ProductionActivePower": 1_500_000,
          "_sum/GridActivePower": -200_000, "_sum/EssActivePower": -300_000}
    assert "→ OK" in balance_check(ok)
    off = dict(ok, **{"_sum/GridActivePower": 200_000})
    assert "CHECK" in balance_check(off)
    assert "not checked" in balance_check({})


@pytest.mark.parametrize("bad", ["battery", "=ess0", "battery="])
def test_parse_asset_rejects_bad_input(bad):
    import argparse
    with pytest.raises(argparse.ArgumentTypeError):
        parse_asset(bad)
