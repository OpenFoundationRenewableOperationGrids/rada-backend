"""Unit tests for bridge/poster.py and the publishing loop, with httpx's MockTransport
standing in for the RADA API."""

import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from bridge.config import AssetConfig
from bridge.poster import DryRunPoster, RadaPoster
from bridge.runner import publish_forever, publish_once
from bridge.source import Reading
from bridge.translate import Snapshot

T0 = datetime(2026, 11, 7, 13, 50, tzinfo=timezone.utc)
BATTERY = AssetConfig(asset_id=1, edge_id="edge0", component="ess0", kind="battery")
SOLAR = AssetConfig(asset_id=2, edge_id="edge0", component="meter1", kind="solar")
PAYLOAD = {"timestamp": T0.isoformat(), "power_mw": 1.5, "energy_mwh": 0.0}


def poster_answering(status, body=None, requests=None):
    def handler(request):
        if requests is not None:
            requests.append(request)
        return httpx.Response(status, json=body or {})
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return RadaPoster("https://preprod-api.example.com/", "rada-key", client=client)


def test_posts_to_the_asset_with_the_api_key():
    requests = []
    poster = poster_answering(201, {"record_id": 7, "asset_id": 2}, requests)

    ok = asyncio.run(poster.post_telemetry(2, PAYLOAD))

    assert ok is True
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://preprod-api.example.com/assets/2/telemetry"
    assert request.headers["X-API-Key"] == "rada-key"
    assert json.loads(request.content) == PAYLOAD


@pytest.mark.parametrize("status", [403, 404, 422, 500])
def test_refused_post_returns_false(status):
    poster = poster_answering(status, {"detail": "nope"})

    assert asyncio.run(poster.post_telemetry(2, PAYLOAD)) is False


def test_unreachable_api_returns_false():
    def handler(request):
        raise httpx.ConnectError("refused", request=request)
    poster = RadaPoster("http://x", "k", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))

    assert asyncio.run(poster.post_telemetry(2, PAYLOAD)) is False


def test_dry_run_posts_nothing():
    assert asyncio.run(DryRunPoster().post_telemetry(2, PAYLOAD)) is True


class RecordingPoster:
    def __init__(self):
        self.posts = []

    async def post_telemetry(self, asset_id, payload):
        self.posts.append((asset_id, payload))
        return True


def full_snapshot():
    snap = Snapshot()
    snap.update([
        Reading(T0, "edge0", "ess0/ActivePower", -700_000),
        Reading(T0, "edge0", "ess0/Soc", 21),
        Reading(T0, "edge0", "ess0/Capacity", 8_000_000),
        Reading(T0, "edge0", "meter1/ActivePower", 1_500_000),
    ])
    return snap


def test_publish_once_posts_every_asset():
    poster = RecordingPoster()

    posted, skipped = asyncio.run(publish_once([BATTERY, SOLAR], full_snapshot(), poster, T0))

    assert (posted, skipped) == (2, 0)
    assert [(a, p["power_mw"]) for a, p in poster.posts] == [(1, -0.7), (2, 1.5)]


def test_publish_once_skips_an_asset_without_data():
    poster = RecordingPoster()
    snap = Snapshot()
    snap.update([Reading(T0, "edge0", "meter1/ActivePower", 1_500_000)])

    posted, skipped = asyncio.run(publish_once([BATTERY, SOLAR], snap, poster, T0))

    assert (posted, skipped) == (1, 1)
    assert poster.posts[0][0] == 2


class FakeTime:
    """A clock that only moves when the code sleeps (or when we move it)."""

    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now


class Stop(Exception):
    pass


def run_publisher(poster, rounds, post_duration=0.0, interval=60):
    fake, delays = FakeTime(), []

    class SlowPoster:
        async def post_telemetry(self, asset_id, payload):
            fake.now += post_duration          # posting takes time
            return await poster.post_telemetry(asset_id, payload)

    async def sleep(seconds):
        delays.append(seconds)
        if len(delays) > rounds:
            raise Stop
        fake.now += seconds

    with pytest.raises(Stop):
        asyncio.run(publish_forever([SOLAR], full_snapshot(), SlowPoster(), interval,
                                    clock=lambda: T0 + timedelta(seconds=1),
                                    sleep=sleep, monotonic=fake.monotonic))
    return delays


def test_publish_forever_waits_then_posts_each_interval():
    poster = RecordingPoster()

    delays = run_publisher(poster, rounds=2)

    assert delays == [60, 60, 60]
    assert len(poster.posts) == 2       # one post per completed interval


def test_publishing_time_does_not_drift_the_rhythm():
    # Each round takes 0.5 s to post: the next wait is shortened to stay on time.
    delays = run_publisher(RecordingPoster(), rounds=3, post_duration=0.5)

    assert delays == [60, 59.5, 59.5, 59.5]


def test_missed_rounds_are_skipped_not_bursted():
    # One round takes 150 s (e.g. the laptop slept): two rounds are skipped.
    poster = RecordingPoster()
    delays = run_publisher(poster, rounds=1, post_duration=150)

    assert delays[1] == 30              # back on the 60 s grid: 1000+60+3x60 = 1240
