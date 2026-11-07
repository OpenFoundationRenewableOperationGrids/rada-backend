"""Unit tests for bridge/runner.py: the reconnect loop."""

import asyncio
from datetime import datetime, timezone

import pytest

from bridge.runner import Backoff, run_forever
from bridge.source import ConnectionLost, Reading

T0 = datetime(2026, 11, 7, 13, 50, tzinfo=timezone.utc)


class StopTest(Exception):
    """Raised by the fake sleep to end run_forever after a few loops."""


class ScriptedSource:
    """Each call to stream() plays the next attempt: a list of batches, then ConnectionLost."""

    name = "fake"

    def __init__(self, *attempts):
        self.attempts = list(attempts)

    async def stream(self, edge_ids, channels):
        batches = self.attempts.pop(0)
        for batch in batches:
            yield batch
        raise ConnectionLost("test")


def reading(value):
    return Reading(time=T0, edge_id="edge0", address="ess0/Soc", value=value)


def fake_sleep(delays, stop_after):
    async def sleep(seconds):
        delays.append(seconds)
        if len(delays) >= stop_after:
            raise StopTest
    return sleep


def test_backoff_doubles_up_to_the_maximum():
    backoff = Backoff(initial=1, maximum=60)

    assert [backoff.next_delay() for _ in range(8)] == [1, 2, 4, 8, 16, 32, 60, 60]


def test_backoff_reset():
    backoff = Backoff()
    backoff.next_delay()
    backoff.next_delay()

    backoff.reset()

    assert backoff.next_delay() == 1


def test_reconnects_with_growing_delays_while_failing():
    source = ScriptedSource([], [], [], [])
    delays = []

    async def on_readings(batch):
        pass

    with pytest.raises(StopTest):
        asyncio.run(run_forever(source, ["edge0"], ["ess0/Soc"], on_readings,
                                sleep=fake_sleep(delays, stop_after=4)))

    assert delays == [1, 2, 4, 8]


def test_delay_resets_once_data_flows_and_batches_are_passed_on():
    # fail, fail, deliver two batches then fail, fail
    source = ScriptedSource([], [], [[reading(1)], [reading(2)]], [])
    delays, received = [], []

    async def on_readings(batch):
        received.append(batch[0].value)

    with pytest.raises(StopTest):
        asyncio.run(run_forever(source, ["edge0"], ["ess0/Soc"], on_readings,
                                sleep=fake_sleep(delays, stop_after=4)))

    assert received == [1, 2]
    assert delays == [1, 2, 1, 2]   # back to 1 s after data flowed


def test_cancelling_stops_the_loop():
    source = ScriptedSource(*([[]] * 100))

    async def on_readings(batch):
        pass

    async def main():
        task = asyncio.create_task(run_forever(source, ["edge0"], ["ess0/Soc"], on_readings,
                                               backoff=Backoff(initial=10)))
        await asyncio.sleep(0.01)   # let it fail once and start waiting 10 s
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(main())
