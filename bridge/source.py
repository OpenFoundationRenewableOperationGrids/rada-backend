"""
bridge/source.py

Where the bridge gets its values from. `ChannelSource` is the small internal
interface asked for in BE-01: the rest of the bridge only sees batches of
`Reading`s, so the B2B websocket below can be swapped for polling
(getEdgesChannelsValues over B2B REST) without touching translation or
posting.

A source connects once and yields batches until the connection ends. It does
not reconnect itself: the reconnect loop lives in runner.py, so every source
gets the same behaviour.
"""

import asyncio
import base64
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import websockets

from bridge import jsonrpc

log = logging.getLogger("bridge.source")

# How long to wait for the Backend to answer a request before giving up.
REQUEST_TIMEOUT_SEC = 10


@dataclass(frozen=True)
class Reading:
    """One channel value from one edge, stamped when the bridge received it."""

    time: datetime   # UTC; OpenEMS notifications carry no timestamp of their own
    edge_id: str
    address: str     # channel address, e.g. "ess0/Soc"
    value: Any       # number, or None when the edge has no value yet


class ConnectionLost(Exception):
    """The connection to the source ended or could not be set up."""


class ChannelSource(Protocol):
    name: str

    def stream(self, edge_ids: list[str], channels: list[str]) -> AsyncIterator[list[Reading]]:
        """Connect, subscribe, and yield one batch of readings per update."""
        ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def basic_auth(user: str, password: str) -> str:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return f"Basic {token}"


class B2BWebsocketSource:
    """OpenEMS Backend B2B websocket (io.openems.backend.b2bwebsocket, port 8076)."""

    def __init__(
        self,
        name: str,
        url: str,
        user: str,
        password: str,
        connect: Callable = websockets.connect,  # replaced by a fake in the tests
        clock: Callable[[], datetime] = utc_now,
    ):
        self.name = name
        self.url = url
        self._auth = basic_auth(user, password)
        self._connect = connect
        self._clock = clock
        self._subscription_count = 0
        # Latest online/offline status per edge, from getEdgesStatus.
        self.edges_online: dict[str, bool] = {}

    async def stream(self, edge_ids: list[str], channels: list[str]) -> AsyncIterator[list[Reading]]:
        try:
            async with self._connect(
                self.url,
                additional_headers={"Authorization": self._auth},
                proxy=None,          # a Tailscale address: never go through an HTTP proxy
                open_timeout=REQUEST_TIMEOUT_SEC,
            ) as ws:
                log.info("[%s] Connected to %s", self.name, self.url)

                status = await self._call(ws, jsonrpc.get_edges_status(edge_ids))
                self.edges_online = jsonrpc.parse_edges_status(status)
                offline = [e for e in edge_ids if not self.edges_online.get(e)]
                if offline:
                    # Still subscribe: OpenEMS starts sending values once the edge reconnects.
                    log.warning("[%s] Edges offline: %s", self.name, ", ".join(offline))

                self._subscription_count += 1
                await self._call(
                    ws, jsonrpc.subscribe_edges_channels(self._subscription_count, edge_ids, channels)
                )
                log.info("[%s] Subscribed to %d channels on %s", self.name, len(channels), ", ".join(edge_ids))

                async for text in ws:
                    batch = self._readings_from(text)
                    if batch:
                        yield batch

        except (OSError, asyncio.TimeoutError, websockets.exceptions.WebSocketException,
                jsonrpc.JsonRpcError, jsonrpc.ProtocolError) as exc:
            raise ConnectionLost(f"{type(exc).__name__}: {exc}") from exc

        # The Backend closed the connection cleanly: still a lost connection for us.
        raise ConnectionLost("Connection closed by the Backend")

    async def _call(self, ws, request: dict) -> Any:
        """Send a request and wait for the response with the same id."""
        await ws.send(jsonrpc.encode(request))

        async def wait_for_response():
            async for text in ws:
                message = jsonrpc.parse(text)
                if isinstance(message, jsonrpc.Response) and message.id == request["id"]:
                    return message.unwrap()
                # Anything else before our answer (e.g. a late notification) is skipped.
            raise ConnectionLost("Connection closed while waiting for a response")

        return await asyncio.wait_for(wait_for_response(), REQUEST_TIMEOUT_SEC)

    def _readings_from(self, text: str) -> list[Reading]:
        try:
            message = jsonrpc.parse(text)
        except jsonrpc.ProtocolError as exc:
            log.warning("[%s] Ignoring an invalid message: %s", self.name, exc)
            return []

        if not isinstance(message, jsonrpc.Notification) or message.method != jsonrpc.EDGES_CURRENT_DATA:
            return []

        received_at = self._clock()
        return [
            Reading(time=received_at, edge_id=edge_id, address=address, value=value)
            for edge_id, channels in jsonrpc.parse_edges_current_data(message.params).items()
            for address, value in channels.items()
        ]
