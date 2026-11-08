"""
bridge/__main__.py

Entry point: `python -m bridge [--config PATH] [--dry-run]`.

For each OpenEMS Backend in the config it runs two tasks side by side:
  - the source: keeps the B2B websocket subscribed and the snapshot up to date
    (reconnecting by itself when the connection drops);
  - the publisher: every post interval, posts the snapshot of each asset to RADA.

Ctrl+C or `docker stop` (SIGTERM) stops everything cleanly.
"""

import argparse
import asyncio
import logging
import os
import signal
import sys
from collections.abc import Callable

import websockets
from dotenv import load_dotenv

from bridge.config import BridgeConfig, ConfigError, load_config
from bridge.poster import DryRunPoster, RadaPoster
from bridge.runner import publish_forever, run_forever
from bridge.source import B2BWebsocketSource
from bridge.translate import Snapshot, all_channels

log = logging.getLogger("bridge")

DEFAULT_CONFIG_PATH = "bridge/connections.toml"


async def run_bridge(config: BridgeConfig, poster, connect: Callable = websockets.connect) -> None:
    """Run every connection until cancelled."""
    tasks = []
    for conn in config.connections:
        # One snapshot per Backend: two Backends may both have an "edge0".
        snapshot = Snapshot()

        async def on_readings(batch, snapshot=snapshot):
            snapshot.update(batch)

        source = B2BWebsocketSource(conn.name, conn.url, conn.user, conn.password, connect=connect)
        channels = all_channels(conn.assets)
        log.info("[%s] %s, edges %s, channels %s", conn.name, conn.url,
                 ", ".join(conn.edge_ids), ", ".join(channels))

        tasks.append(asyncio.create_task(
            run_forever(source, conn.edge_ids, channels, on_readings), name=f"source-{conn.name}"))
        tasks.append(asyncio.create_task(
            publish_forever(conn.assets, snapshot, poster, config.post_interval_sec),
            name=f"publisher-{conn.name}"))

    try:
        # The tasks only end by being cancelled, or if a bug raises an exception.
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def main_async(config: BridgeConfig, dry_run: bool) -> None:
    poster = DryRunPoster() if dry_run else RadaPoster(config.rada_api_url, config.rada_api_key)

    # `docker stop` sends SIGTERM: treat it like Ctrl+C.
    main_task = asyncio.current_task()
    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGTERM, main_task.cancel)
    except (NotImplementedError, RuntimeError):
        pass  # not available on Windows; Ctrl+C still works

    try:
        await run_bridge(config, poster)
    finally:
        await poster.aclose()


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m bridge", description="RADA OpenEMS bridge")
    parser.add_argument(
        "--config",
        default=os.getenv("BRIDGE_CONFIG", DEFAULT_CONFIG_PATH),
        help=f"path to the TOML config (default: $BRIDGE_CONFIG or {DEFAULT_CONFIG_PATH})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="read from OpenEMS and log what would be posted, without posting anything to RADA",
    )
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # websockets logs every connection attempt and httpx every request;
    # keep their warnings only (our own "Posted N asset(s)" line says enough).
    for noisy in ("websockets", "httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    try:
        config = load_config(args.config, require_rada=not args.dry_run)
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    if args.dry_run:
        log.info("DRY RUN: nothing will be posted to RADA")
    else:
        log.info("Posting to %s every %s s", config.rada_api_url, config.post_interval_sec)

    try:
        asyncio.run(main_async(config, args.dry_run))
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    log.info("Bridge stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
