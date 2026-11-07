"""The simulator must leave alone the assets fed by a real source (BE-01)."""

from telemetry_simulator import simulated_assets


def test_assets_with_an_edge_id_are_not_simulated():
    assets = [
        {"id": 1, "asset_type": "battery", "edge_id": None},      # seeded asset
        {"id": 49, "asset_type": "battery", "edge_id": "edge0"},  # fed by the OpenEMS bridge
        {"id": 50, "asset_type": "solar", "edge_id": "edge0"},
        {"id": 7, "asset_type": "wind"},                          # no edge_id field at all
        {"id": 8, "asset_type": "solar", "edge_id": ""},
    ]

    assert [a["id"] for a in simulated_assets(assets)] == [1, 7, 8]
