"""
bridge/__main__.py

Entry point: `python -m bridge`. For now it loads and checks the
configuration and prints a summary; the connection to OpenEMS is added in
the next steps.
"""

import argparse
import logging
import os
import sys

from dotenv import load_dotenv

from bridge.config import ConfigError, load_config

log = logging.getLogger("bridge")

DEFAULT_CONFIG_PATH = "bridge/connections.toml"


def main() -> int:
    parser = argparse.ArgumentParser(prog="python -m bridge", description="RADA OpenEMS bridge")
    parser.add_argument(
        "--config",
        default=os.getenv("BRIDGE_CONFIG", DEFAULT_CONFIG_PATH),
        help=f"path to the TOML config (default: $BRIDGE_CONFIG or {DEFAULT_CONFIG_PATH})",
    )
    args = parser.parse_args()

    load_dotenv()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        log.error("Configuration error: %s", exc)
        return 2

    log.info("RADA API: %s, posting every %s s", config.rada_api_url, config.post_interval_sec)
    for conn in config.connections:
        log.info("Connection '%s' at %s, edges %s", conn.name, conn.url, ", ".join(conn.edge_ids))
        for asset in conn.assets:
            log.info("  asset %s <- %s/%s (%s)", asset.asset_id, asset.edge_id, asset.component, asset.kind)

    return 0


if __name__ == "__main__":
    sys.exit(main())
