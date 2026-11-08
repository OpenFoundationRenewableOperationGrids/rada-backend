"""
bridge/jsonrpc.py

Builds the JSON-RPC 2.0 requests the OpenEMS Backend's B2B websocket
understands, and reads what comes back. Pure functions only: no network,
so everything here is easy to test.

Message formats verified against OpenEMS 2026.1.0 on 7 Nov 2026:

    -> {"jsonrpc":"2.0","id":"<uuid>","method":"getEdgesStatus","params":{"edgeIds":["edge0"]}}
    <- {"jsonrpc":"2.0","id":"<uuid>","result":{"edge0":{"online":true}}}

    -> {"jsonrpc":"2.0","id":"<uuid>","method":"subscribeEdgesChannels",
        "params":{"count":1,"ids":["edge0"],"channels":["_sum/EssSoc"]}}
    <- {"jsonrpc":"2.0","id":"<uuid>","result":{}}
    <- {"jsonrpc":"2.0","method":"edgesCurrentData","params":{"edge0":{"_sum/EssSoc":21}}}   (every 2 s)
"""

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

EDGES_CURRENT_DATA = "edgesCurrentData"


class JsonRpcError(Exception):
    """The Backend answered a request with an error."""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(f"JSON-RPC error {code}: {message}")
        self.code = code
        self.message = message
        self.data = data


class ProtocolError(ValueError):
    """A message that isn't valid JSON-RPC 2.0."""


# --- Requests ---------------------------------------------------------------

def new_id() -> str:
    """OpenEMS requires a UUID as the request id."""
    return str(uuid.uuid4())


def make_request(method: str, params: dict | None = None, request_id: str | None = None) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id or new_id(),
        "method": method,
        "params": params or {},
    }


def get_edges_status(edge_ids: list[str]) -> dict:
    """Ask which edges are connected to the Backend."""
    return make_request("getEdgesStatus", {"edgeIds": list(edge_ids)})


def subscribe_edges_channels(count: int, edge_ids: list[str], channels: list[str]) -> dict:
    """Subscribe to channels on the given edges.

    `count` numbers the subscription: send a new request with a higher
    count to replace the previous channel list.
    """
    return make_request(
        "subscribeEdgesChannels",
        {"count": count, "ids": list(edge_ids), "channels": list(channels)},
    )


def edge_rpc(edge_id: str, payload: dict) -> dict:
    """Wrap a request so that the Backend forwards it to one edge."""
    return make_request("edgeRpc", {"edgeId": edge_id, "payload": payload})


def get_edge_config(edge_id: str) -> dict:
    """The edge's components, factories and channels (for asset discovery, BE-05)."""
    return edge_rpc(edge_id, make_request("getEdgeConfig"))


def encode(message: dict) -> str:
    return json.dumps(message, separators=(",", ":"))


# --- Replies and notifications ---------------------------------------------

@dataclass(frozen=True)
class Response:
    """Answer to one of our requests, matched to it by `id`."""

    id: str
    result: Any = None
    error: JsonRpcError | None = None

    def unwrap(self) -> Any:
        """Return the result, or raise the Backend's error."""
        if self.error is not None:
            raise self.error
        return self.result


@dataclass(frozen=True)
class Notification:
    """A message the Backend pushes without being asked, e.g. edgesCurrentData."""

    method: str
    params: dict = field(default_factory=dict)


def parse(text: str) -> Response | Notification:
    """Turn one websocket message into a Response or a Notification."""
    try:
        message = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"Not JSON: {exc}") from None

    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        raise ProtocolError("Not a JSON-RPC 2.0 message")

    if "method" in message and "id" not in message:
        params = message.get("params") or {}
        if not isinstance(params, dict):
            raise ProtocolError("Notification params must be an object")
        return Notification(method=message["method"], params=params)

    if "id" not in message:
        raise ProtocolError("Message has neither an id nor a method")

    if "error" in message:
        err = message["error"] or {}
        if not isinstance(err, dict):
            raise ProtocolError("Error must be an object")
        return Response(
            id=message["id"],
            error=JsonRpcError(err.get("code", 0), err.get("message", ""), err.get("data")),
        )
    return Response(id=message["id"], result=message.get("result"))


def parse_edges_status(result: Any) -> dict[str, bool]:
    """{"edge0": {"online": true}} -> {"edge0": True}"""
    if not isinstance(result, dict) or not all(isinstance(s, dict) for s in result.values()):
        raise ProtocolError(f"Unexpected getEdgesStatus result: {result!r}")
    return {edge_id: bool(status.get("online")) for edge_id, status in result.items()}


def parse_edges_current_data(params: dict) -> dict[str, dict[str, Any]]:
    """Values per edge and channel address, e.g. {"edge0": {"ess0/Soc": 21}}.

    A value can be None when the edge has no value for that channel yet
    (for example energy counters on an edge without a Timedata component).
    """
    return {
        edge_id: dict(channels)
        for edge_id, channels in params.items()
        if isinstance(channels, dict)
    }
