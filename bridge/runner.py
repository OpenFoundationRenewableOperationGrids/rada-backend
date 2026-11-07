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
from collections.abc import Awaitable, Callable

from bridge.source import ChannelSource, ConnectionLost, Reading

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
