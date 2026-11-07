"""
bridge/poster.py

Sends payloads to RADA. `RadaPoster` posts to the API with the X-API-Key
header; `DryRunPoster` only logs what it would send, for a first live test.

A failed post is logged and skipped, never retried: the next round, one
post interval later, sends fresher values anyway.
"""

import json
import logging

import httpx

log = logging.getLogger("bridge.poster")

REQUEST_TIMEOUT_SEC = 10


class RadaPoster:
    def __init__(self, base_url: str, api_key: str, client: httpx.AsyncClient | None = None):
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-API-Key": api_key}
        self._client = client or httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SEC)

    async def post_telemetry(self, asset_id: int, payload: dict) -> bool:
        """POST /assets/{asset_id}/telemetry. Returns True when RADA stored the record."""
        url = f"{self._base_url}/assets/{asset_id}/telemetry"
        try:
            response = await self._client.post(url, json=payload, headers=self._headers)
        except httpx.HTTPError as exc:
            log.warning("Asset %s: could not reach RADA (%s: %s)", asset_id, type(exc).__name__, exc)
            return False

        if response.status_code == 201:
            log.debug("Asset %s: stored record %s", asset_id, response.json().get("record_id"))
            return True

        # 403 wrong key, 404 unknown asset, 422 invalid payload: say what RADA answered.
        log.error("Asset %s: RADA answered %s: %s", asset_id, response.status_code, response.text[:300])
        return False

    async def aclose(self) -> None:
        await self._client.aclose()


class DryRunPoster:
    """Logs the payloads instead of posting them."""

    async def post_telemetry(self, asset_id: int, payload: dict) -> bool:
        log.info("DRY RUN asset %s: %s", asset_id, json.dumps(payload))
        return True

    async def aclose(self) -> None:
        pass
