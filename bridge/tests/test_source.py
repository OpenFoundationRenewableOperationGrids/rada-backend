"""Unit tests for bridge/source.py against a fake Backend."""

import asyncio
import base64
from datetime import datetime, timezone

import pytest

from bridge import source as source_module
from bridge.source import B2BWebsocketSource, ConnectionLost
from bridge.tests.fakes import FakeConnect, FakeWebSocket, current_data

T0 = datetime(2026, 11, 7, 13, 50, tzinfo=timezone.utc)
CHANNELS = ["ess0/Soc", "ess0/ActivePower"]


def make_source(*attempts):
    connect = FakeConnect(*attempts)
    src = B2BWebsocketSource("vps", "ws://100.88.150.125:8076", "admin", "admin",
                             connect=connect, clock=lambda: T0)
    return src, connect


async def collect(src, edge_ids=("edge0",), channels=CHANNELS):
    """Run one stream to its end; return the batches and the exception that ended it."""
    batches = []
    with pytest.raises(ConnectionLost) as exc_info:
        async for batch in src.stream(list(edge_ids), list(channels)):
            batches.append(batch)
    return batches, exc_info.value


def test_streams_timestamped_readings():
    ws = FakeWebSocket(notifications=[
        current_data({"ess0/Soc": 21, "ess0/ActivePower": -700000}),
        current_data({"ess0/Soc": 22, "ess0/ActivePower": -700000}),
    ])
    src, _ = make_source(ws)

    batches, _ = asyncio.run(collect(src))

    assert len(batches) == 2
    first = {r.address: r for r in batches[0]}
    assert first["ess0/Soc"].value == 21
    assert first["ess0/ActivePower"].value == -700000
    assert first["ess0/Soc"].edge_id == "edge0"
    assert first["ess0/Soc"].time == T0        # stamped by the bridge on arrival


def test_sends_status_then_subscription():
    src, _ = make_source(FakeWebSocket())
    ws = src._connect.attempts[0]

    asyncio.run(collect(src))

    assert [m["method"] for m in ws.sent] == ["getEdgesStatus", "subscribeEdgesChannels"]
    assert ws.sent[0]["params"] == {"edgeIds": ["edge0"]}
    assert ws.sent[1]["params"] == {"count": 1, "ids": ["edge0"], "channels": CHANNELS}


def test_connects_with_basic_auth_and_no_proxy():
    src, connect = make_source(FakeWebSocket())

    asyncio.run(collect(src))

    url, kwargs = connect.calls[0]
    assert url == "ws://100.88.150.125:8076"
    expected = "Basic " + base64.b64encode(b"admin:admin").decode()
    assert kwargs["additional_headers"] == {"Authorization": expected}
    assert kwargs["proxy"] is None


def test_offline_edge_is_recorded_and_still_subscribed():
    ws = FakeWebSocket(edges_online={"edge0": True, "edge1": False})
    src, _ = make_source(ws)

    asyncio.run(collect(src, edge_ids=("edge0", "edge1")))

    assert src.edges_online == {"edge0": True, "edge1": False}
    assert ws.sent[1]["params"]["ids"] == ["edge0", "edge1"]


def test_missing_value_stays_none():
    src, _ = make_source(FakeWebSocket(notifications=[current_data({"ess0/Soc": None})]))

    batches, _ = asyncio.run(collect(src))

    assert batches[0][0].value is None


def test_clean_close_is_a_lost_connection():
    src, _ = make_source(FakeWebSocket())

    _, exc = asyncio.run(collect(src))

    assert "closed by the Backend" in str(exc)


def test_dropped_connection_after_data():
    ws = FakeWebSocket(notifications=[current_data({"ess0/Soc": 21})], drop=True)
    src, _ = make_source(ws)

    batches, exc = asyncio.run(collect(src))

    assert len(batches) == 1                   # what arrived before the drop is kept
    assert "ConnectionClosedError" in str(exc)


def test_connection_refused():
    src, _ = make_source(ConnectionRefusedError("refused"))

    batches, exc = asyncio.run(collect(src))

    assert batches == []
    assert "ConnectionRefusedError" in str(exc)


def test_error_response_to_subscription():
    ws = FakeWebSocket(errors={"subscribeEdgesChannels": (5007, "bad params")})
    src, _ = make_source(ws)

    _, exc = asyncio.run(collect(src))

    assert "5007" in str(exc)


def test_no_answer_times_out(monkeypatch):
    monkeypatch.setattr(source_module, "REQUEST_TIMEOUT_SEC", 0.05)
    src, _ = make_source(FakeWebSocket(silent=True))

    _, exc = asyncio.run(collect(src))

    assert "TimeoutError" in str(exc)


def test_invalid_and_unrelated_messages_are_ignored():
    ws = FakeWebSocket(notifications=[
        "not json",
        {"jsonrpc": "2.0", "method": "somethingElse", "params": {}},
        current_data({"ess0/Soc": 30}),
    ])
    src, _ = make_source(ws)

    batches, _ = asyncio.run(collect(src))

    assert len(batches) == 1
    assert batches[0][0].value == 30


def test_subscription_count_increases_on_each_connection():
    first, second = FakeWebSocket(), FakeWebSocket()
    src, _ = make_source(first, second)

    asyncio.run(collect(src))
    asyncio.run(collect(src))

    assert first.sent[1]["params"]["count"] == 1
    assert second.sent[1]["params"]["count"] == 2
