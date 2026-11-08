"""Unit tests for bridge/jsonrpc.py, using the messages captured from
OpenEMS 2026.1.0 on 7 Nov 2026."""

import json
import uuid

import pytest

from bridge import jsonrpc
from bridge.jsonrpc import JsonRpcError, Notification, ProtocolError, Response


# --- Requests ---------------------------------------------------------------

def test_request_has_a_uuid_id():
    request = jsonrpc.make_request("getEdgesStatus")

    assert request["jsonrpc"] == "2.0"
    uuid.UUID(request["id"])  # raises if it isn't a UUID
    assert request["params"] == {}


def test_each_request_gets_a_new_id():
    assert jsonrpc.make_request("x")["id"] != jsonrpc.make_request("x")["id"]


def test_get_edges_status():
    request = jsonrpc.get_edges_status(["edge0", "edge1"])

    assert request["method"] == "getEdgesStatus"
    assert request["params"] == {"edgeIds": ["edge0", "edge1"]}


def test_subscribe_edges_channels():
    request = jsonrpc.subscribe_edges_channels(1, ["edge0"], ["ess0/Soc", "meter1/ActivePower"])

    assert request["method"] == "subscribeEdgesChannels"
    assert request["params"] == {
        "count": 1,
        "ids": ["edge0"],
        "channels": ["ess0/Soc", "meter1/ActivePower"],
    }


def test_get_edge_config_is_wrapped_in_edge_rpc():
    request = jsonrpc.get_edge_config("edge0")

    assert request["method"] == "edgeRpc"
    assert request["params"]["edgeId"] == "edge0"
    inner = request["params"]["payload"]
    assert inner["method"] == "getEdgeConfig"
    assert inner["id"] != request["id"]


def test_encode_is_compact_json():
    text = jsonrpc.encode({"a": 1, "b": [1, 2]})

    assert text == '{"a":1,"b":[1,2]}'


# --- Parsing: the replies we saw on 7 Nov -----------------------------------

def test_parse_edges_status_response():
    text = (
        '{"jsonrpc":"2.0","id":"8f1c2a9e-0000-4000-8000-000000000002",'
        '"result":{"edge1":{"online":false},"edge0":{"online":true}}}'
    )

    message = jsonrpc.parse(text)

    assert isinstance(message, Response)
    assert message.id == "8f1c2a9e-0000-4000-8000-000000000002"
    assert jsonrpc.parse_edges_status(message.unwrap()) == {"edge0": True, "edge1": False}


def test_parse_error_response():
    # The error we got when getEdgesStatus was sent without edgeIds.
    text = (
        '{"jsonrpc":"2.0","id":"8f1c2a9e-0000-4000-8000-000000000001",'
        '"error":{"code":5007,"message":"JSON [edgeIds:{}] is not JSON-Array","data":["edgeIds","{}"]}}'
    )

    message = jsonrpc.parse(text)

    assert isinstance(message, Response)
    with pytest.raises(JsonRpcError) as exc_info:
        message.unwrap()
    assert exc_info.value.code == 5007
    assert "edgeIds" in exc_info.value.message


def test_parse_subscribe_ack():
    message = jsonrpc.parse('{"jsonrpc":"2.0","id":"abc","result":{}}')

    assert isinstance(message, Response)
    assert message.unwrap() == {}


def test_parse_edges_current_data_notification():
    text = (
        '{"jsonrpc":"2.0","method":"edgesCurrentData","params":{"edge0":{'
        '"_sum/ConsumptionActivePower":1000000,"_sum/EssActivePower":-700000,"_sum/EssSoc":21,'
        '"_sum/GridActivePower":200000,"_sum/GridBuyActiveEnergy":369870,'
        '"_sum/ProductionActivePower":1500000}}}'
    )

    message = jsonrpc.parse(text)

    assert isinstance(message, Notification)
    assert message.method == jsonrpc.EDGES_CURRENT_DATA
    values = jsonrpc.parse_edges_current_data(message.params)
    assert values["edge0"]["_sum/EssSoc"] == 21
    assert values["edge0"]["_sum/EssActivePower"] == -700000


def test_current_data_keeps_missing_values_as_none():
    # Counters are empty on an edge without a Timedata component.
    params = {"edge0": {"_sum/GridBuyActiveEnergy": None, "_sum/EssSoc": 5}}

    values = jsonrpc.parse_edges_current_data(params)

    assert values == {"edge0": {"_sum/GridBuyActiveEnergy": None, "_sum/EssSoc": 5}}


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[1, 2]",
        '{"id": "x", "result": {}}',            # missing "jsonrpc": "2.0"
        '{"jsonrpc": "2.0"}',                   # neither id nor method
        '{"jsonrpc": "2.0", "method": "x", "params": [1]}',
        '{"jsonrpc": "2.0", "id": "x", "error": "boom"}',   # error is not an object
    ],
)
def test_invalid_messages(text):
    with pytest.raises(ProtocolError):
        jsonrpc.parse(text)


def test_round_trip():
    request = jsonrpc.get_edges_status(["edge0"])

    assert json.loads(jsonrpc.encode(request)) == request


@pytest.mark.parametrize("result", [None, [], "online", {"edge0": True}])
def test_unexpected_edges_status_is_a_protocol_error(result):
    with pytest.raises(ProtocolError):
        jsonrpc.parse_edges_status(result)
