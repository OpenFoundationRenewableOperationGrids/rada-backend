"""
bridge/runner.py

Keeps one source running unattended: when the connection drops or can't be
set up, it waits and tries again, doubling the wait each time (1 s, 2 s,
4 s ... up to 60 s). The wait goes back to 1 s as soon as data flows again.

Stopping is done by cancelling the task (Ctrl+C or `docker stop`), which
interrupts the wait or the stream straight away.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import datetime

from bridge.config import AssetConfig
from bridge.source import ChannelSource, ConnectionLost, Reading, utc_now
from bridge.translate import Skipped, Snapshot, to_telemetry

log = logging.getLogger("bridge.runner")


class Backoff:
    """Wait times between reconnection attempts."""

    def __init__(self, initial: float = 1.0, maximum: float = 60.0, factor: float = 2.0):
        self.initial = initial
        self.maximum = maximum
        self.factor = factor
        self._next = initial

    def next_delay(self) -> float:
        delay = self._next
        self._next = min(self._next * self.factor, self.maximum)
        return delay

    def reset(self) -> None:
        self._next = self.initial


async def run_forever(
    source: ChannelSource,
    edge_ids: list[str],
    channels: list[str],
    on_readings: Callable[[list[Reading]], Awaitable[None]],
    backoff: Backoff | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Stream from `source` and hand every batch to `on_readings`, reconnecting forever."""
    backoff = backoff or Backoff()

    while True:
        try:
            async for batch in source.stream(edge_ids, channels):
                backoff.reset()          # data is flowing: the next failure starts at 1 s again
                await on_readings(batch)
        except ConnectionLost as exc:
            delay = backoff.next_delay()
            log.warning("[%s] %s. Reconnecting in %.0f s", source.name, exc, delay)
            await sleep(delay)


async def publish_once(assets, snapshot: Snapshot, poster, now: datetime) -> tuple[int, int]:
    """Post the latest values of every asset once. Returns (posted, skipped)."""
    posted = skipped = 0
    for asset in assets:
        result = to_telemetry(asset, snapshot, now)
        if isinstance(result, Skipped):
            log.warning("Asset %s (%s/%s): nothing posted, %s",
                        asset.asset_id, asset.edge_id, asset.component, result.reason)
            skipped += 1
        elif await poster.post_telemetry(asset.asset_id, result):
            posted += 1
        else:
            skipped += 1
    return posted, skipped


async def publish_forever(
    assets: tuple[AssetConfig, ...] | list[AssetConfig],
    snapshot: Snapshot,
    poster,
    interval_sec: float,
    clock: Callable[[], datetime] = utc_now,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Every `interval_sec`, post the latest snapshot of each asset.

    Rounds follow a fixed rhythm (start + n x interval), so the time spent
    posting doesn't add up into drift. If the process falls more than a whole
    interval behind (e.g. a laptop waking from sleep), missed rounds are
    skipped rather than posted in a burst.
    """
    next_round = monotonic() + interval_sec   # wait first, so the first round has data
    while True:
        await sleep(max(0.0, next_round - monotonic()))
        posted, skipped = await publish_once(assets, snapshot, poster, clock())
        log.info("Posted %d asset(s), skipped %d", posted, skipped)

        next_round += interval_sec
        now = monotonic()
        if next_round <= now:
            missed = int((now - next_round) // interval_sec) + 1
            log.warning("Fell behind: skipping %d round(s)", missed)
            next_round += missed * interval_sec
