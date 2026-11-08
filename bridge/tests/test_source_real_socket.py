"""The source against a real websocket server on localhost that answers like
the OpenEMS Backend. Checks what the fake can't: the real `websockets`
connection options and the Basic Auth header on the handshake."""

import asyncio
import base64
import json

import websockets

from bridge.source import B2BWebsocketSource, ConnectionLost


def test_real_websocket_round_trip():
    seen_auth = []

    async def backend(ws):
        seen_auth.append(ws.request.headers.get("Authorization"))
        async for text in ws:
            request = json.loads(text)
            if request["method"] == "getEdgesStatus":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                          "result": {"edge0": {"online": True}}}))
            elif request["method"] == "subscribeEdgesChannels":
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": {}}))
                await ws.send(json.dumps({"jsonrpc": "2.0", "method": "edgesCurrentData",
                                          "params": {"edge0": {"ess0/Soc": 42}}}))
                await ws.close()

    async def main():
        async with websockets.serve(backend, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            src = B2BWebsocketSource("local", f"ws://127.0.0.1:{port}", "admin", "admin")
            batches = []
            try:
                async for batch in src.stream(["edge0"], ["ess0/Soc"]):
                    batches.append(batch)
            except ConnectionLost:
                pass
            return batches

    batches = asyncio.run(main())

    assert seen_auth == ["Basic " + base64.b64encode(b"admin:admin").decode()]
    assert batches[0][0].address == "ess0/Soc"
    assert batches[0][0].value == 42
