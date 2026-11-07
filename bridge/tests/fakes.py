"""A fake OpenEMS B2B websocket, so the source can be tested without a Backend."""

import json
from collections import deque

from websockets.exceptions import ConnectionClosedError


class FakeWebSocket:
    """Answers requests like the OpenEMS Backend does.

    - getEdgesStatus is answered from `edges_online`.
    - subscribeEdgesChannels is acknowledged, then the `notifications` are delivered.
    - `errors` maps a method name to a JSON-RPC error to return instead.
    - After the last message the connection ends: cleanly, or with an error if `drop` is set.
    """

    def __init__(self, edges_online=None, notifications=(), errors=None, drop=False, silent=False,
                 hold_open=False):
        self.edges_online = edges_online or {"edge0": True}
        self.notifications = list(notifications)
        self.errors = errors or {}
        self.drop = drop
        self.silent = silent          # never answer: to test timeouts
        self.hold_open = hold_open    # stay connected after the last message
        self.sent = []                # every request the bridge sent, as dicts
        self._inbox = deque()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send(self, text):
        request = json.loads(text)
        self.sent.append(request)
        if self.silent:
            return
        method, request_id = request["method"], request["id"]

        if method in self.errors:
            code, message = self.errors[method]
            self._push({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})
        elif method == "getEdgesStatus":
            result = {e: {"online": self.edges_online.get(e, False)} for e in request["params"]["edgeIds"]}
            self._push({"jsonrpc": "2.0", "id": request_id, "result": result})
        elif method == "subscribeEdgesChannels":
            self._push({"jsonrpc": "2.0", "id": request_id, "result": {}})
            for note in self.notifications:
                self._inbox.append(note if isinstance(note, str) else json.dumps(note))

    def _push(self, message):
        self._inbox.append(json.dumps(message))

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._inbox:
            return self._inbox.popleft()
        if self.silent or self.hold_open:
            # Like a real socket with nothing to say: wait (until the timeout fires).
            import asyncio
            await asyncio.sleep(3600)
        if self.drop:
            raise ConnectionClosedError(None, None)
        raise StopAsyncIteration


class FakeConnect:
    """Stands in for websockets.connect. Each call uses the next item of `attempts`:
    a FakeWebSocket, or an exception to raise (e.g. connection refused)."""

    def __init__(self, *attempts):
        self.attempts = list(attempts)
        self.calls = []               # (url, kwargs) of every connection attempt

    def __call__(self, url, **kwargs):
        self.calls.append((url, kwargs))
        attempt = self.attempts.pop(0)
        if isinstance(attempt, Exception):
            raise attempt
        return attempt


def current_data(values, edge_id="edge0"):
    """An edgesCurrentData notification, as OpenEMS sends it."""
    return {"jsonrpc": "2.0", "method": "edgesCurrentData", "params": {edge_id: values}}
