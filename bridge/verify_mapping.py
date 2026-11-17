"""
bridge/verify_mapping.py

BE-02 verification step: read every channel of the default OpenEMS mapping
template (seeds/openems_mapping.toml) on one edge, through the Backend's B2B
websocket, and print a Markdown table of metric, channel and live value for
the pull request. Read only: nothing is posted to RADA.

    python -m bridge.verify_mapping --edge edge0 \
        --asset battery=ess0 --asset solar=meter1 --asset meter=meter0

The connection (URL and the names of the credential variables) comes from
the bridge's own config file; the credentials from the environment (.env).

Python computes: the scale factors and the energy-balance check below are
worked out here, so a reviewer only has to judge whether signs and units
are plausible.
"""

import argparse
import asyncio
import logging
import os
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from bridge.config import ConfigError, load_config
from bridge.source import B2BWebsocketSource, ConnectionLost

log = logging.getLogger("bridge.verify_mapping")

SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"
DEFAULT_CONFIG_PATH = "bridge/connections.toml"
DEFAULT_WAIT_SEC = 20

# Allowed gap in the instantaneous balance before it is flagged, in W.
# The _sum values are read from one 2 s notification but are not computed in
# the same instant on the edge, so a small difference is expected.
BALANCE_TOLERANCE_W = 1_000


@dataclass(frozen=True)
class Row:
    """One mapped channel to read."""

    metric: str
    component: str   # "_sum" for site rows, otherwise the asset's component
    address: str     # full channel address, e.g. "ess0/ActivePower"
    scale: float = 1.0

    @property
    def level(self) -> str:
        return "site" if self.component == "_sum" else "asset"


def load_toml(path: Path) -> dict:
    with open(path, "rb") as f:
        return tomllib.load(f)


def plan_rows(template: dict, assets: list[tuple[str, str]]) -> list[Row]:
    """The site rows of the template, then the asset rows for each (type, component)."""
    rows = [Row(r["metric"], "_sum", r["address"], r.get("scale", 1.0)) for r in template["site"]]
    for asset_type, component in assets:
        if asset_type not in template["asset"]:
            raise ValueError(f"No template for asset type {asset_type!r} "
                             f"(known: {', '.join(sorted(template['asset']))})")
        for r in template["asset"][asset_type]:
            rows.append(Row(r["metric"], component, f"{component}/{r['channel']}", r.get("scale", 1.0)))
    return rows


def scaled(row: Row, raw) -> float | None:
    return None if raw is None else raw * row.scale


def render_table(rows: list[Row], raw_values: dict, metrics: dict) -> str:
    """Markdown table: metric, level, channel, raw value, scale, value with unit, sign note."""
    lines = [
        "| Metric | Level | Channel | Raw | Scale | Value | Sign / note |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        metric = metrics[row.metric]
        raw = raw_values.get(row.address)
        value = scaled(row, raw)
        if value is None:
            shown = "no value (optional)" if metric["category"] == "optional" else "**no value**"
        else:
            shown = f"{value:,.3f}".rstrip("0").rstrip(".") + f" {metric['unit']}"
        scale = "" if row.scale == 1 else f"×{row.scale:g}"
        note = metric.get("sign_note", {}).get("en", "")
        lines.append(f"| `{row.metric}` | {row.level} | `{row.address}` | {raw!s} | {scale} | {shown} | {note} |")
    return "\n".join(lines)


def balance_check(raw_values: dict) -> str:
    """consumption = production + grid + battery, on the site (_sum) values."""
    names = {
        "consumption": "_sum/ConsumptionActivePower",
        "production": "_sum/ProductionActivePower",
        "grid": "_sum/GridActivePower",
        "battery": "_sum/EssActivePower",
    }
    v = {k: raw_values.get(a) for k, a in names.items()}
    missing = [k for k, x in v.items() if x is None]
    if missing:
        return f"Energy balance not checked: no value for {', '.join(missing)}."
    expected = v["production"] + v["grid"] + v["battery"]
    gap = v["consumption"] - expected
    verdict = "OK" if abs(gap) <= BALANCE_TOLERANCE_W else "**CHECK**"
    return (
        f"Energy balance (site, W): consumption {v['consumption']:,} vs "
        f"production {v['production']:,} + grid {v['grid']:,} + battery {v['battery']:,} "
        f"= {expected:,}; gap {gap:,} W → {verdict} (tolerance {BALANCE_TOLERANCE_W:,} W)"
    )


async def collect(source: B2BWebsocketSource, edge_id: str, addresses: list[str], wait_sec: float) -> dict:
    """Latest raw value per address, until every address has a value or wait_sec runs out."""
    latest: dict[str, object] = {}

    async def read():
        async for batch in source.stream([edge_id], addresses):
            for reading in batch:
                if reading.edge_id == edge_id:
                    latest[reading.address] = reading.value
            if all(latest.get(a) is not None for a in addresses):
                return

    try:
        await asyncio.wait_for(read(), wait_sec)
    except asyncio.TimeoutError:
        log.warning("Stopped after %s s; channels without a value are shown as such", wait_sec)
    return latest


def parse_asset(text: str) -> tuple[str, str]:
    asset_type, sep, component = text.partition("=")
    if not sep or not asset_type or not component:
        raise argparse.ArgumentTypeError(f"expected TYPE=COMPONENT, e.g. battery=ess0, got {text!r}")
    return asset_type.strip(), component.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    parser.add_argument("--edge", required=True, help="OpenEMS edge id, e.g. edge0")
    parser.add_argument("--asset", action="append", type=parse_asset, default=[],
                        metavar="TYPE=COMPONENT", help="e.g. battery=ess0 (repeat for each asset)")
    parser.add_argument("--connection", help="connection name in the config (default: the first one)")
    parser.add_argument("--config", default=os.getenv("BRIDGE_CONFIG", DEFAULT_CONFIG_PATH))
    parser.add_argument("--wait", type=float, default=DEFAULT_WAIT_SEC, help="seconds to wait for values")
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("websockets").setLevel(logging.WARNING)

    try:
        config = load_config(args.config, require_rada=False)
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2
    conns = [c for c in config.connections if args.connection in (None, c.name)]
    if not conns:
        log.error("No connection named %r in %s", args.connection, args.config)
        return 2
    conn = conns[0]

    metrics = {m["key"]: m for m in load_toml(SEEDS_DIR / "metrics.toml")["metric"]}
    try:
        rows = plan_rows(load_toml(SEEDS_DIR / "openems_mapping.toml"), args.asset)
    except ValueError as exc:
        log.error("%s", exc)
        return 2

    addresses = sorted({r.address for r in rows})
    source = B2BWebsocketSource(conn.name, conn.url, conn.user, conn.password)
    try:
        raw_values = asyncio.run(collect(source, args.edge, addresses, args.wait))
    except ConnectionLost as exc:
        log.error("Could not read from the Backend: %s", exc)
        return 1

    print(f"\n### Mapping verification: {args.edge} via `{conn.name}`\n")
    print(render_table(rows, raw_values, metrics))
    print("\n" + balance_check(raw_values) + "\n")
    return 1 if missing_core(rows, raw_values, metrics) else 0


def missing_core(rows: list[Row], raw_values: dict, metrics: dict) -> list[str]:
    """Addresses of core metrics without a value. Optional metrics (e.g. reactive
    power on a simulated meter) may be absent without failing the check."""
    return [
        r.address for r in rows
        if raw_values.get(r.address) is None and metrics[r.metric]["category"] == "core"
    ]


if __name__ == "__main__":
    sys.exit(main())
