"""The whole bridge wired together: a fake Backend in, a recording poster out."""

import asyncio
import dataclasses
import logging

from bridge.__main__ import run_bridge
from bridge.config import AssetConfig, BridgeConfig, ConnectionConfig
from bridge.tests.fakes import FakeConnect, FakeWebSocket, current_data


class RecordingPoster:
    def __init__(self):
        self.posts = []

    async def post_telemetry(self, asset_id, payload):
        self.posts.append((asset_id, payload))
        return True


def config(post_interval_sec):
    conn = ConnectionConfig(
        name="vps", url="ws://100.88.150.125:8076", user="admin", password="admin",
        assets=(
            AssetConfig(1, "edge0", "ess0", "battery"),
            AssetConfig(2, "edge0", "meter1", "solar"),
        ),
    )
    cfg = BridgeConfig(rada_api_url="http://rada", rada_api_key="k", connections=(conn,))
    # Bypass the 2 s minimum of the file format, to keep the test fast.
    return dataclasses.replace(cfg, post_interval_sec=post_interval_sec)


def test_bridge_end_to_end(caplog):
    ws = FakeWebSocket(hold_open=True, notifications=[current_data({
        "ess0/ActivePower": -700_000, "ess0/Soc": 21, "ess0/Capacity": 8_000_000,
        "meter1/ActivePower": 1_500_000,
    })])
    connect, poster = FakeConnect(ws), RecordingPoster()

    async def main():
        task = asyncio.create_task(run_bridge(config(0.05), poster, connect=connect))
        await asyncio.sleep(0.13)     # about two post intervals
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    with caplog.at_level(logging.INFO):
        asyncio.run(main())

    # Subscribed once, to the channels both assets need.
    subscribe = ws.sent[1]
    assert subscribe["params"]["channels"] == [
        "ess0/ActivePower", "ess0/Capacity", "ess0/Soc", "meter1/ActivePower",
    ]
    # Each interval posted both assets.
    assert len(poster.posts) >= 2
    by_asset = dict(poster.posts)
    assert by_asset[1]["power_mw"] == -0.7
    assert by_asset[1]["energy_mwh"] == 1.68
    assert by_asset[2]["power_mw"] == 1.5
