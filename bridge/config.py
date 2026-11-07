"""
bridge/config.py

Loads the bridge configuration: a TOML file listing the OpenEMS Backends
("connections") and the RADA assets each one feeds, plus the secrets, which
are read from environment variables. Nothing secret is written in the TOML
file: each connection only names the variables that hold its credentials.
"""

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

# Asset kinds the bridge knows how to translate (see translate.py).
ASSET_KINDS = ("battery", "solar")

# OpenEMS B2B pushes values every 2 seconds, so posting faster makes no sense.
MIN_POST_INTERVAL_SEC = 2
DEFAULT_POST_INTERVAL_SEC = 60


class ConfigError(ValueError):
    """The configuration file or the environment is invalid."""


@dataclass(frozen=True)
class AssetConfig:
    """One RADA asset, fed by one OpenEMS component on one edge."""

    asset_id: int   # id of the asset in RADA
    edge_id: str    # OpenEMS edge, e.g. "edge0"
    component: str  # OpenEMS component, e.g. "ess0" or "meter1"
    kind: str       # one of ASSET_KINDS


@dataclass(frozen=True)
class ConnectionConfig:
    """One OpenEMS Backend, reached through its B2B websocket."""

    name: str
    url: str
    user: str
    password: str = field(repr=False)  # never printed in logs
    assets: tuple[AssetConfig, ...] = ()

    @property
    def edge_ids(self) -> list[str]:
        """The edges this connection reads, without duplicates."""
        return sorted({asset.edge_id for asset in self.assets})


@dataclass(frozen=True)
class BridgeConfig:
    rada_api_url: str
    rada_api_key: str = field(repr=False)
    post_interval_sec: int = DEFAULT_POST_INTERVAL_SEC
    connections: tuple[ConnectionConfig, ...] = ()


def load_config(path: str | Path, env: Mapping[str, str] = os.environ) -> BridgeConfig:
    """Read the TOML file at `path` and the secrets from `env`."""
    path = Path(path)
    try:
        with path.open("rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"Config file not found: {path}") from None
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {path}: {exc}") from None

    post_interval = raw.get("post_interval_sec", DEFAULT_POST_INTERVAL_SEC)
    if not isinstance(post_interval, int) or post_interval < MIN_POST_INTERVAL_SEC:
        raise ConfigError(f"post_interval_sec must be an integer of at least {MIN_POST_INTERVAL_SEC}")

    raw_connections = raw.get("connections", [])
    if not raw_connections:
        raise ConfigError("At least one [[connections]] block is required")

    connections = tuple(_parse_connection(c, env) for c in raw_connections)

    names = [c.name for c in connections]
    if len(names) != len(set(names)):
        raise ConfigError("Connection names must be unique")

    return BridgeConfig(
        rada_api_url=_require_env(env, "RADA_API_URL").rstrip("/"),
        rada_api_key=_require_env(env, "RADA_API_KEY"),
        post_interval_sec=post_interval,
        connections=connections,
    )


def _parse_connection(raw: dict, env: Mapping[str, str]) -> ConnectionConfig:
    name = _require_str(raw, "name", "connection")
    where = f"connection '{name}'"

    url = _require_str(raw, "url", where)
    if not url.startswith(("ws://", "wss://")):
        raise ConfigError(f"{where}: url must start with ws:// or wss://")

    user = _require_env(env, _require_str(raw, "user_env", where))
    password = _require_env(env, _require_str(raw, "password_env", where))

    raw_assets = raw.get("assets", [])
    if not raw_assets:
        raise ConfigError(f"{where}: at least one [[connections.assets]] block is required")
    assets = tuple(_parse_asset(a, where) for a in raw_assets)

    # One OpenEMS component feeds one RADA asset, and vice versa.
    sources = [(a.edge_id, a.component) for a in assets]
    if len(sources) != len(set(sources)):
        raise ConfigError(f"{where}: the same edge_id and component appear twice")
    asset_ids = [a.asset_id for a in assets]
    if len(asset_ids) != len(set(asset_ids)):
        raise ConfigError(f"{where}: the same asset_id appears twice")

    return ConnectionConfig(name=name, url=url, user=user, password=password, assets=assets)


def _parse_asset(raw: dict, where: str) -> AssetConfig:
    asset_id = raw.get("asset_id")
    # bool is a subclass of int in Python, so exclude it explicitly.
    if not isinstance(asset_id, int) or isinstance(asset_id, bool) or asset_id <= 0:
        raise ConfigError(f"{where}: asset_id must be a positive integer (got {asset_id!r})")

    kind = _require_str(raw, "kind", where)
    if kind not in ASSET_KINDS:
        raise ConfigError(f"{where}: kind must be one of {', '.join(ASSET_KINDS)} (got {kind!r})")

    return AssetConfig(
        asset_id=asset_id,
        edge_id=_require_str(raw, "edge_id", where),
        component=_require_str(raw, "component", where),
        kind=kind,
    )


def _require_str(raw: dict, key: str, where: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{where}: '{key}' is required and must be text")
    return value.strip()


def _require_env(env: Mapping[str, str], name: str) -> str:
    value = env.get(name, "").strip()
    if not value:
        raise ConfigError(f"Environment variable {name} is not set")
    return value
