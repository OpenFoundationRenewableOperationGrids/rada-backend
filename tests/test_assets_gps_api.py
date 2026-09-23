"""
test_assets_gps_api.py

Integration tests for GPS/edge_id support on the assets endpoints —
covers the same scenarios validated manually via /docs during Task 1:
creation, retrieval, upsert behavior, and bounds validation.
"""

ASSET_PAYLOAD = {
    "eic_code": "17W-UNITTEST-01-X",
    "name": "Unit Test Asset",
    "asset_type": "battery",
    "max_capacity_mwh": 1.0,
    "max_charge_rate_mw": 0.5,
    "max_discharge_rate_mw": 0.5,
    "latitude": 48.8566,
    "longitude": 2.3522,
    "edge_id": "edge0",
}


def create_asset(client, auth_headers, **overrides):
    payload = {**ASSET_PAYLOAD, **overrides}
    return client.post("/assets", json=payload, headers=auth_headers)


def add_telemetry(client, auth_headers, asset_id):
    return client.post(
        f"/assets/{asset_id}/telemetry",
        json={"timestamp": "2026-01-01T12:00:00", "energy_mwh": 0.5, "power_mw": 0.2},
        headers=auth_headers,
    )


def test_create_asset_with_gps_and_edge_id(client, auth_headers):
    resp = create_asset(client, auth_headers)
    assert resp.status_code == 201
    assert resp.json()["action"] == "created"


def test_create_asset_without_gps_is_optional(client, auth_headers):
    payload = {k: v for k, v in ASSET_PAYLOAD.items() if k not in ("latitude", "longitude", "edge_id")}
    resp = client.post("/assets", json=payload, headers=auth_headers)
    assert resp.status_code == 201


def test_create_asset_rejects_out_of_bounds_latitude(client, auth_headers):
    resp = create_asset(client, auth_headers, latitude=200)
    assert resp.status_code == 422


def test_create_asset_rejects_out_of_bounds_longitude(client, auth_headers):
    resp = create_asset(client, auth_headers, longitude=-500)
    assert resp.status_code == 422


def test_assetslist_returns_gps_fields(client, auth_headers):
    created = create_asset(client, auth_headers).json()
    add_telemetry(client, auth_headers, created["asset_id"])

    resp = client.get("/assetslist", headers=auth_headers)
    assert resp.status_code == 200
    asset = next(a for a in resp.json() if a["id"] == created["asset_id"])
    assert asset["latitude"] == 48.8566
    assert asset["longitude"] == 2.3522
    assert asset["edge_id"] == "edge0"


def test_soc_summary_returns_gps_fields(client, auth_headers):
    created = create_asset(client, auth_headers).json()
    add_telemetry(client, auth_headers, created["asset_id"])

    resp = client.get(f"/assets/{created['asset_id']}/soc", params={"mode": "S"}, headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["latitude"] == 48.8566
    assert body["longitude"] == 2.3522
    assert body["edge_id"] == "edge0"


def test_upsert_overwrites_gps_coordinates(client, auth_headers):
    first = create_asset(client, auth_headers)
    assert first.json()["action"] == "created"
    asset_id = first.json()["asset_id"]
    add_telemetry(client, auth_headers, asset_id)

    second = create_asset(client, auth_headers, latitude=43.2965, longitude=5.3698, edge_id="edge1")
    assert second.status_code == 201
    assert second.json()["action"] == "updated"
    assert second.json()["asset_id"] == asset_id

    resp = client.get(f"/assets/{asset_id}/soc", params={"mode": "S"}, headers=auth_headers)
    body = resp.json()
    assert body["latitude"] == 43.2965
    assert body["longitude"] == 5.3698
    assert body["edge_id"] == "edge1"


def test_missing_api_key_is_rejected(client):
    resp = client.get("/assetslist", headers={})
    assert resp.status_code == 403


def test_wrong_api_key_is_rejected(client):
    resp = client.get("/assetslist", headers={"X-API-Key": "not-the-right-key"})
    assert resp.status_code == 403
