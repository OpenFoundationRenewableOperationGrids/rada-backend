import pytest
from sqlalchemy.orm import Query

from models import Asset


def make_asset_payload(**overrides):
    payload = {
        "eic_code": "10T-FR-BATT-01",
        "name": "Battery One",
        "asset_type": "battery",
        "max_capacity_mwh": 10.0,
        "max_charge_rate_mw": 2.0,
        "max_discharge_rate_mw": 2.0,
    }
    payload.update(overrides)
    return payload


# --- POST /assets ---

def test_create_asset_success(client, db_session, auth_headers):
    response = client.post("/assets", json=make_asset_payload(), headers=auth_headers)

    assert response.status_code == 201
    body = response.json()
    assert body["action"] == "created"
    assert isinstance(body["asset_id"], int)

    asset = db_session.query(Asset).filter(Asset.id == body["asset_id"]).first()
    assert asset is not None
    assert asset.eic_code == "10T-FR-BATT-01"
    assert asset.name == "Battery One"


def test_create_asset_duplicate_eic_code_returns_409(client, auth_headers):
    client.post("/assets", json=make_asset_payload(), headers=auth_headers)
    response = client.post("/assets", json=make_asset_payload(name="Battery Two"), headers=auth_headers)

    assert response.status_code == 409


def test_create_asset_missing_required_field_returns_422(client, auth_headers):
    payload = make_asset_payload()
    del payload["name"]

    response = client.post("/assets", json=payload, headers=auth_headers)

    assert response.status_code == 422


# --- PUT /assets/{asset_id} ---

def test_replace_asset_success(client, db_session, auth_headers):
    created = client.post("/assets", json=make_asset_payload(), headers=auth_headers).json()
    asset_id = created["asset_id"]

    response = client.put(
        f"/assets/{asset_id}",
        json=make_asset_payload(
            eic_code="10T-FR-BATT-01",
            name="Battery One Renamed",
            max_capacity_mwh=20.0,
        ),
        headers=auth_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "updated"
    assert body["asset_id"] == asset_id

    db_session.expire_all()
    asset = db_session.query(Asset).filter(Asset.id == asset_id).first()
    assert asset.name == "Battery One Renamed"
    assert asset.max_capacity_mwh == 20.0


def test_replace_asset_not_found_returns_404(client, auth_headers):
    response = client.put("/assets/999999", json=make_asset_payload(), headers=auth_headers)

    assert response.status_code == 404


def test_replace_asset_missing_field_returns_422(client, auth_headers):
    created = client.post("/assets", json=make_asset_payload(), headers=auth_headers).json()
    asset_id = created["asset_id"]

    payload = make_asset_payload()
    del payload["max_charge_rate_mw"]

    response = client.put(f"/assets/{asset_id}", json=payload, headers=auth_headers)

    assert response.status_code == 422


def test_replace_asset_eic_code_conflict_returns_409(client, auth_headers):
    client.post("/assets", json=make_asset_payload(), headers=auth_headers)
    second = client.post(
        "/assets", json=make_asset_payload(eic_code="10T-FR-BATT-02", name="Battery Two"), headers=auth_headers
    ).json()

    response = client.put(
        f"/assets/{second['asset_id']}",
        json=make_asset_payload(eic_code="10T-FR-BATT-01", name="Battery Two"),
        headers=auth_headers,
    )

    assert response.status_code == 409


# --- PATCH /assets/{asset_id} ---

def test_patch_asset_partial_update(client, db_session, auth_headers):
    created = client.post("/assets", json=make_asset_payload(), headers=auth_headers).json()
    asset_id = created["asset_id"]

    response = client.patch(f"/assets/{asset_id}", json={"name": "Battery One Patched"}, headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "updated"

    db_session.expire_all()
    asset = db_session.query(Asset).filter(Asset.id == asset_id).first()
    assert asset.name == "Battery One Patched"
    # Untouched fields keep their original value
    assert asset.max_capacity_mwh == 10.0
    assert asset.eic_code == "10T-FR-BATT-01"


def test_patch_asset_not_found_returns_404(client, auth_headers):
    response = client.patch("/assets/999999", json={"name": "Nope"}, headers=auth_headers)

    assert response.status_code == 404


def test_patch_asset_eic_code_conflict_returns_409(client, auth_headers):
    client.post("/assets", json=make_asset_payload(), headers=auth_headers)
    second = client.post(
        "/assets", json=make_asset_payload(eic_code="10T-FR-BATT-02", name="Battery Two"), headers=auth_headers
    ).json()

    response = client.patch(
        f"/assets/{second['asset_id']}", json={"eic_code": "10T-FR-BATT-01"}, headers=auth_headers
    )

    assert response.status_code == 409


def test_patch_asset_empty_body_is_noop(client, db_session, auth_headers):
    created = client.post("/assets", json=make_asset_payload(), headers=auth_headers).json()
    asset_id = created["asset_id"]

    response = client.patch(f"/assets/{asset_id}", json={}, headers=auth_headers)

    assert response.status_code == 200
    db_session.expire_all()
    asset = db_session.query(Asset).filter(Asset.id == asset_id).first()
    assert asset.name == "Battery One"


def test_patch_asset_null_on_non_nullable_field_returns_422(client, db_session, auth_headers):
    created = client.post("/assets", json=make_asset_payload(), headers=auth_headers).json()
    asset_id = created["asset_id"]

    response = client.patch(f"/assets/{asset_id}", json={"max_capacity_mwh": None}, headers=auth_headers)

    assert response.status_code == 422
    assert "max_capacity_mwh" in response.json()["detail"]
    db_session.expire_all()
    asset = db_session.query(Asset).filter(Asset.id == asset_id).first()
    assert asset.max_capacity_mwh == 10.0


# --- eic_code race: the unique constraint fires on commit ---

def bypass_eic_code_precheck(monkeypatch, real_lookups):
    """Let the first `real_lookups` Query.first() calls through, then return None,
    so the route's eic_code pre-check misses the duplicate and db.commit() raises
    IntegrityError — as when a concurrent request inserts the same eic_code."""
    real_first = Query.first
    calls = {"n": 0}

    def first(self):
        calls["n"] += 1
        return real_first(self) if calls["n"] <= real_lookups else None

    monkeypatch.setattr(Query, "first", first)


def test_create_asset_integrity_error_returns_409(client, auth_headers, monkeypatch):
    client.post("/assets", json=make_asset_payload(), headers=auth_headers)
    bypass_eic_code_precheck(monkeypatch, real_lookups=0)

    response = client.post("/assets", json=make_asset_payload(name="Battery Two"), headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "Asset with eic_code 10T-FR-BATT-01 already exists"


@pytest.mark.parametrize("method", ["put", "patch"])
def test_update_asset_integrity_error_returns_409(client, db_session, auth_headers, monkeypatch, method):
    client.post("/assets", json=make_asset_payload(), headers=auth_headers)
    second = client.post(
        "/assets", json=make_asset_payload(eic_code="10T-FR-BATT-02", name="Battery Two"), headers=auth_headers
    ).json()
    # Real lookup of the asset by id, then the eic_code conflict check is skipped
    bypass_eic_code_precheck(monkeypatch, real_lookups=1)

    body = make_asset_payload(name="Battery Two") if method == "put" else {"eic_code": "10T-FR-BATT-01"}
    response = getattr(client, method)(f"/assets/{second['asset_id']}", json=body, headers=auth_headers)

    assert response.status_code == 409
    assert response.json()["detail"] == "Asset with eic_code 10T-FR-BATT-01 already exists"
    monkeypatch.undo()
    db_session.expire_all()
    asset = db_session.query(Asset).filter(Asset.id == second["asset_id"]).first()
    assert asset.eic_code == "10T-FR-BATT-02"
