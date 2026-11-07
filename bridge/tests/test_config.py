"""Unit tests for bridge/config.py — no database, no network."""

import pytest

from bridge.config import ConfigError, load_config

ENV = {
    "RADA_API_URL": "https://preprod-api.example.com/",
    "RADA_API_KEY": "rada-key",
    "OPENEMS_VPS_USER": "admin",
    "OPENEMS_VPS_PASSWORD": "secret",
}

VALID = """
post_interval_sec = 60

[[connections]]
name = "vps"
url = "ws://100.88.150.125:8076"
user_env = "OPENEMS_VPS_USER"
password_env = "OPENEMS_VPS_PASSWORD"

[[connections.assets]]
asset_id = 1
edge_id = "edge0"
component = "ess0"
kind = "battery"

[[connections.assets]]
asset_id = 2
edge_id = "edge0"
component = "meter1"
kind = "solar"
"""


def write(tmp_path, text):
    path = tmp_path / "connections.toml"
    path.write_text(text)
    return path


def test_load_valid_config(tmp_path):
    config = load_config(write(tmp_path, VALID), ENV)

    assert config.rada_api_url == "https://preprod-api.example.com"  # trailing slash removed
    assert config.rada_api_key == "rada-key"
    assert config.post_interval_sec == 60
    assert len(config.connections) == 1

    conn = config.connections[0]
    assert conn.name == "vps"
    assert conn.user == "admin"
    assert conn.password == "secret"
    assert conn.edge_ids == ["edge0"]  # two assets, one edge
    assert [(a.asset_id, a.component, a.kind) for a in conn.assets] == [
        (1, "ess0", "battery"),
        (2, "meter1", "solar"),
    ]


def test_secrets_are_not_shown_in_repr(tmp_path):
    config = load_config(write(tmp_path, VALID), ENV)

    assert "secret" not in repr(config)
    assert "rada-key" not in repr(config)


def test_post_interval_defaults_to_60(tmp_path):
    config = load_config(write(tmp_path, VALID.replace("post_interval_sec = 60", "")), ENV)

    assert config.post_interval_sec == 60


def test_missing_file():
    with pytest.raises(ConfigError, match="not found"):
        load_config("does/not/exist.toml", ENV)


def test_missing_env_variable(tmp_path):
    env = {k: v for k, v in ENV.items() if k != "OPENEMS_VPS_PASSWORD"}

    with pytest.raises(ConfigError, match="OPENEMS_VPS_PASSWORD"):
        load_config(write(tmp_path, VALID), env)


def test_unknown_asset_kind(tmp_path):
    with pytest.raises(ConfigError, match="kind must be one of"):
        load_config(write(tmp_path, VALID.replace('"solar"', '"wind"')), ENV)


def test_asset_id_must_be_positive(tmp_path):
    with pytest.raises(ConfigError, match="asset_id"):
        load_config(write(tmp_path, VALID.replace("asset_id = 2", "asset_id = 0")), ENV)


def test_same_component_twice(tmp_path):
    with pytest.raises(ConfigError, match="edge_id and component appear twice"):
        load_config(write(tmp_path, VALID.replace('"meter1"', '"ess0"')), ENV)


def test_same_asset_id_twice(tmp_path):
    with pytest.raises(ConfigError, match="asset_id appears twice"):
        load_config(write(tmp_path, VALID.replace("asset_id = 2", "asset_id = 1")), ENV)


def test_url_must_be_a_websocket(tmp_path):
    with pytest.raises(ConfigError, match="ws://"):
        load_config(write(tmp_path, VALID.replace("ws://", "http://")), ENV)


def test_post_interval_too_short(tmp_path):
    with pytest.raises(ConfigError, match="post_interval_sec"):
        load_config(write(tmp_path, VALID.replace("= 60", "= 1")), ENV)


def test_no_connections(tmp_path):
    with pytest.raises(ConfigError, match="At least one"):
        load_config(write(tmp_path, "post_interval_sec = 60\n"), ENV)
