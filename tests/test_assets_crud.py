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

def test_create_asset_success(client, db_session):
    response = client.post("/assets", json=make_asset_payload())

    assert response.status_code == 201
    body = response.json()
    assert body["action"] == "created"
    assert isinstance(body["asset_id"], int)

    asset = db_session.query(Asset).filter(Asset.id == body["asset_id"]).first()
    assert asset is not None
    assert asset.eic_code == "10T-FR-BATT-01"
    assert asset.name == "Battery One"


def test_create_asset_duplicate_eic_code_returns_409(client):
    client.post("/assets", json=make_asset_payload())
    response = client.post("/assets", json=make_asset_payload(name="Battery Two"))

    assert response.status_code == 409


def test_create_asset_missing_required_field_returns_422(client):
    payload = make_asset_payload()
    del payload["name"]

    response = client.post("/assets", json=payload)

    assert response.status_code == 422


# --- PUT /assets/{asset_id} ---

def test_replace_asset_success(client, db_session):
    created = client.post("/assets", json=make_asset_payload()).json()
    asset_id = created["asset_id"]

    response = client.put(
        f"/assets/{asset_id}",
        json=make_asset_payload(
            eic_code="10T-FR-BATT-01",
            name="Battery One Renamed",
            max_capacity_mwh=20.0,
        ),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "updated"
    assert body["asset_id"] == asset_id

    db_session.expire_all()
    asset = db_session.query(Asset).filter(Asset.id == asset_id).first()
    assert asset.name == "Battery One Renamed"
    assert asset.max_capacity_mwh == 20.0


def test_replace_asset_not_found_returns_404(client):
    response = client.put("/assets/999999", json=make_asset_payload())

    assert response.status_code == 404


def test_replace_asset_missing_field_returns_422(client):
    created = client.post("/assets", json=make_asset_payload()).json()
    asset_id = created["asset_id"]

    payload = make_asset_payload()
    del payload["max_charge_rate_mw"]

    response = client.put(f"/assets/{asset_id}", json=payload)

    assert response.status_code == 422


def test_replace_asset_eic_code_conflict_returns_409(client):
    first = client.post("/assets", json=make_asset_payload()).json()
    second = client.post(
        "/assets", json=make_asset_payload(eic_code="10T-FR-BATT-02", name="Battery Two")
    ).json()

    response = client.put(
        f"/assets/{second['asset_id']}",
        json=make_asset_payload(eic_code="10T-FR-BATT-01", name="Battery Two"),
    )

    assert response.status_code == 409


# --- PATCH /assets/{asset_id} ---

def test_patch_asset_partial_update(client, db_session):
    created = client.post("/assets", json=make_asset_payload()).json()
    asset_id = created["asset_id"]

    response = client.patch(f"/assets/{asset_id}", json={"name": "Battery One Patched"})

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "updated"

    db_session.expire_all()
    asset = db_session.query(Asset).filter(Asset.id == asset_id).first()
    assert asset.name == "Battery One Patched"
    # Untouched fields keep their original value
    assert asset.max_capacity_mwh == 10.0
    assert asset.eic_code == "10T-FR-BATT-01"


def test_patch_asset_not_found_returns_404(client):
    response = client.patch("/assets/999999", json={"name": "Nope"})

    assert response.status_code == 404


def test_patch_asset_eic_code_conflict_returns_409(client):
    client.post("/assets", json=make_asset_payload())
    second = client.post(
        "/assets", json=make_asset_payload(eic_code="10T-FR-BATT-02", name="Battery Two")
    ).json()

    response = client.patch(
        f"/assets/{second['asset_id']}", json={"eic_code": "10T-FR-BATT-01"}
    )

    assert response.status_code == 409


def test_patch_asset_empty_body_is_noop(client, db_session):
    created = client.post("/assets", json=make_asset_payload()).json()
    asset_id = created["asset_id"]

    response = client.patch(f"/assets/{asset_id}", json={})

    assert response.status_code == 200
    db_session.expire_all()
    asset = db_session.query(Asset).filter(Asset.id == asset_id).first()
    assert asset.name == "Battery One"
