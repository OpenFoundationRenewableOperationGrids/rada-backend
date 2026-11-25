"""
test_measurements_api.py

POST /sites/{site_id}/measurements (docs/DATA_MODEL.md §8): a batch is
checked item by item and stored all or nothing, duplicates are ignored,
site-level and asset-level rows stay apart, the site's last_seen_at only
moves forward, and only ingest callers may post.
"""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

import routers.measurements
from models import Metric, Site, measurement
from routers.measurements import MAX_BATCH_SIZE, MAX_FUTURE_SKEW
from seed_metrics import load_metric_catalogue
from test_auth import admin, make_key, viewer  # noqa: F401 — admin and viewer are fixtures
from test_site_hierarchy import make_asset, make_portfolio, make_site

T0 = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)


@pytest.fixture()
def place(db_session):
    """The catalogue, a site with an asset, and an asset on another site. Committed for the API."""
    load_metric_catalogue(db_session)
    portfolio = make_portfolio(db_session)
    site, other_site = make_site(db_session, portfolio), make_site(db_session, portfolio, name="Other site")
    asset, other_asset = make_asset(site), make_asset(other_site, name="Other battery")
    db_session.add_all([asset, other_asset])
    db_session.commit()
    return {"site": site.id, "asset": asset.id, "other_asset": other_asset.id}


def item(metric="grid_power", asset_id=None, at=T0, value=1500.0):
    time = at.isoformat() if isinstance(at, datetime) else at
    return {"time": time, "metric": metric, "asset_id": asset_id, "value": value}


def post(client, headers, site_id, items, source="bridge-openems"):
    return client.post(f"/sites/{site_id}/measurements", headers=headers, json={"source": source, "items": items})


def stored_rows(db):
    db.expire_all()
    rows = db.execute(
        select(measurement, Metric.key).join(Metric, Metric.id == measurement.c.metric_id)
        .order_by(measurement.c.time, measurement.c.asset_id.nulls_first())
    ).mappings().all()
    return [dict(row) for row in rows]


def last_seen(db, site_id):
    db.expire_all()
    return db.get(Site, site_id).last_seen_at


def errors_of(response):
    assert response.status_code == 422, response.text
    return [(tuple(error["loc"]), error["type"]) for error in response.json()["detail"]]


# --- Storing ---

def test_batch_is_stored(client, auth_headers, db_session, place):
    response = post(client, auth_headers, place["site"], [
        item("grid_power", value=1520.0),
        item("battery_power", asset_id=place["asset"], value=-800.0),
    ])

    assert response.status_code == 200, response.text
    assert response.json() == {"inserted": 2, "ignored": 0}
    rows = stored_rows(db_session)
    assert [(row["key"], row["asset_id"], row["value"], row["source"]) for row in rows] == [
        ("grid_power", None, 1520.0, "bridge-openems"),
        ("battery_power", place["asset"], -800.0, "bridge-openems"),
    ]
    assert all(row["site_id"] == place["site"] and row["time"] == T0 for row in rows)


def test_time_with_an_offset_is_stored_in_utc(client, auth_headers, db_session, place):
    response = post(client, auth_headers, place["site"], [item(at="2026-11-17T11:00:00+01:00")])

    assert response.status_code == 200
    [row] = stored_rows(db_session)
    assert row["time"] == T0
    assert row["time"].utcoffset() == timedelta(0)


def test_site_and_asset_rows_of_one_metric_are_both_kept(client, auth_headers, db_session, place):
    response = post(client, auth_headers, place["site"], [
        item("battery_power", value=-800.0),
        item("battery_power", asset_id=place["asset"], value=-800.0),
    ])

    assert response.json() == {"inserted": 2, "ignored": 0}
    assert [row["asset_id"] for row in stored_rows(db_session)] == [None, place["asset"]]


def test_a_batch_sent_twice_is_stored_once(client, auth_headers, db_session, place):
    batch = [item("grid_power"), item("battery_power", asset_id=place["asset"])]

    first = post(client, auth_headers, place["site"], batch)
    second = post(client, auth_headers, place["site"], batch)

    assert first.json() == {"inserted": 2, "ignored": 0}
    assert second.status_code == 200
    assert second.json() == {"inserted": 0, "ignored": 2}
    assert len(stored_rows(db_session)) == 2


def test_a_duplicate_inside_one_batch_is_ignored(client, auth_headers, db_session, place):
    response = post(client, auth_headers, place["site"], [item(value=1.0), item(value=2.0), item(at=T0 + timedelta(seconds=5))])

    assert response.json() == {"inserted": 2, "ignored": 1}
    assert [row["value"] for row in stored_rows(db_session)] == [1.0, 1500.0]


@pytest.mark.parametrize("source", ["bridge-openems", "simulator", "seed", "csv"])
def test_every_allowed_source_is_stored(client, auth_headers, db_session, place, source):
    assert post(client, auth_headers, place["site"], [item()], source=source).status_code == 200
    assert stored_rows(db_session)[0]["source"] == source


# --- All or nothing, and why an item is refused ---

def test_one_invalid_item_stores_nothing_and_is_named(client, auth_headers, db_session, place):
    response = post(client, auth_headers, place["site"], [item(), item("no_such_metric"), item("voltage")])

    assert errors_of(response) == [
        (("body", "items", 1, "metric"), "unknown_metric"),
        (("body", "items", 2, "asset_id"), "metric_level"),
    ]
    assert stored_rows(db_session) == []


def test_inactive_metric_is_refused(client, auth_headers, db_session, place):
    db_session.query(Metric).filter(Metric.key == "temperature").one().is_active = False
    db_session.commit()

    response = post(client, auth_headers, place["site"], [item("temperature", asset_id=place["asset"])])

    assert errors_of(response) == [(("body", "items", 0, "metric"), "inactive_metric")]


@pytest.mark.parametrize("asset", ["other_asset", "missing"])
def test_asset_not_on_this_site_is_refused(client, auth_headers, place, asset):
    asset_id = place.get(asset, 999_999)

    response = post(client, auth_headers, place["site"], [item("battery_power", asset_id=asset_id)])

    assert errors_of(response) == [(("body", "items", 0, "asset_id"), "asset_not_on_site")]


def test_site_level_metric_with_an_asset_is_refused(client, auth_headers, place):
    response = post(client, auth_headers, place["site"], [item("consumption_power", asset_id=place["asset"])])

    assert errors_of(response) == [(("body", "items", 0, "asset_id"), "metric_level")]


def test_asset_level_metric_without_an_asset_is_refused(client, auth_headers, place):
    response = post(client, auth_headers, place["site"], [item("reactive_power")])

    assert errors_of(response) == [(("body", "items", 0, "asset_id"), "metric_level")]


def test_time_too_far_in_the_future_is_refused(client, auth_headers, db_session, place):
    now = datetime.now(timezone.utc)
    within = now + MAX_FUTURE_SKEW - timedelta(seconds=30)
    beyond = now + MAX_FUTURE_SKEW + timedelta(seconds=30)

    assert errors_of(post(client, auth_headers, place["site"], [item(at=beyond)])) == [
        (("body", "items", 0, "time"), "time_in_future")]
    assert post(client, auth_headers, place["site"], [item(at=within)]).status_code == 200


@pytest.mark.parametrize("bad_item", [
    item(at="2026-11-17T10:00:00"),         # no time zone
    {**item(), "value": "lots"},
    {**item(), "unit": "W"},                # unknown field
])
def test_invalid_item_fields_are_refused(client, auth_headers, db_session, place, bad_item):
    response = post(client, auth_headers, place["site"], [item(at=T0 + timedelta(seconds=1)), bad_item])

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][:3] == ["body", "items", 1]
    assert stored_rows(db_session) == []


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_value_is_refused(client, auth_headers, db_session, place, token):
    """Python's JSON parser accepts these tokens, so they must be refused explicitly."""
    body = f'{{"source": "csv", "items": [{{"time": "{T0.isoformat()}", "metric": "grid_power", "value": {token}}}]}}'

    response = client.post(f"/sites/{place['site']}/measurements", content=body,
                           headers={**auth_headers, "Content-Type": "application/json"})

    # A 422 echoing the value as text: JSON cannot carry NaN, and a 500 would hide the reason
    assert errors_of(response) == [(("body", "items", 0, "value"), "finite_number")]
    assert response.json()["detail"][0]["input"] == str(float(token))
    assert stored_rows(db_session) == []


@pytest.mark.parametrize("source", ["openems", "", None])
def test_unknown_source_is_refused(client, auth_headers, place, source):
    assert errors_of(post(client, auth_headers, place["site"], [item()], source=source))[0][0] == ("body", "source")


def test_empty_batch_is_refused(client, auth_headers, place):
    assert errors_of(post(client, auth_headers, place["site"], [])) == [(("body", "items"), "too_short")]


def test_batch_above_the_maximum_is_refused(client, auth_headers, db_session, place):
    items = [item(at=T0 + timedelta(seconds=n)) for n in range(MAX_BATCH_SIZE + 1)]

    assert errors_of(post(client, auth_headers, place["site"], items)) == [(("body", "items"), "too_long")]
    assert stored_rows(db_session) == []


def test_batch_of_the_maximum_size_is_stored(client, auth_headers, place):
    items = [item(at=T0 + timedelta(seconds=n)) for n in range(MAX_BATCH_SIZE)]

    assert post(client, auth_headers, place["site"], items).json() == {"inserted": MAX_BATCH_SIZE, "ignored": 0}


def test_unknown_site_is_404(client, auth_headers, place):
    response = post(client, auth_headers, 999_999, [item()])

    assert response.status_code == 404
    assert response.json() == {"detail": "Site 999999 not found"}


@pytest.mark.parametrize(("metric", "asset"), [("consumption_power", "asset"), ("battery_power", "other_asset")])
def test_database_rule_is_a_422_when_the_checks_miss_it(client, auth_headers, db_session, place, monkeypatch,
                                                         metric, asset):
    """The level trigger and the asset/site foreign key stay as a safety net: never a 500."""
    monkeypatch.setattr(routers.measurements, "item_errors", lambda *args: [])

    response = post(client, auth_headers, place["site"], [item(), item(metric, asset_id=place[asset])])

    assert errors_of(response) == [(("body", "items"), "rejected_by_database")]
    assert stored_rows(db_session) == []
    assert last_seen(db_session, place["site"]) is None


# --- The site's last_seen_at ---

def test_last_seen_moves_forward_to_the_latest_time_stored(client, auth_headers, db_session, place):
    post(client, auth_headers, place["site"], [item(at=T0), item(at=T0 + timedelta(minutes=1))])
    assert last_seen(db_session, place["site"]) == T0 + timedelta(minutes=1)

    post(client, auth_headers, place["site"], [item(at=T0 + timedelta(minutes=2))])
    assert last_seen(db_session, place["site"]) == T0 + timedelta(minutes=2)


def test_last_seen_does_not_move_back_for_older_data(client, auth_headers, db_session, place):
    post(client, auth_headers, place["site"], [item(at=T0)])

    response = post(client, auth_headers, place["site"], [item(at=T0 - timedelta(days=1))])

    assert response.json() == {"inserted": 1, "ignored": 0}
    assert last_seen(db_session, place["site"]) == T0


def test_last_seen_ignores_a_batch_of_duplicates(client, auth_headers, db_session, place):
    post(client, auth_headers, place["site"], [item(at=T0 + timedelta(minutes=1))])
    # Set it back by hand, so that any update from the duplicate below would show
    db_session.query(Site).filter(Site.id == place["site"]).update({"last_seen_at": T0})
    db_session.commit()

    response = post(client, auth_headers, place["site"], [item(at=T0 + timedelta(minutes=1))])

    assert response.json() == {"inserted": 0, "ignored": 1}
    assert last_seen(db_session, place["site"]) == T0


# --- Who may post ---

def test_an_ingest_key_posts(client, admin, place):  # noqa: F811
    ingest = {"X-API-Key": make_key(client, admin, "bridge-openems", "ingest")["key"]}

    assert post(client, ingest, place["site"], [item()]).status_code == 200


def test_an_administrator_posts(client, admin, place):  # noqa: F811
    assert post(client, admin, place["site"], [item()]).status_code == 200


def test_posting_needs_credentials(client, place):
    assert post(client, {}, place["site"], [item()]).status_code == 401


def test_an_export_key_cannot_post(client, admin, db_session, place):  # noqa: F811
    export = {"X-API-Key": make_key(client, admin, "erp", "export")["key"]}

    assert post(client, export, place["site"], [item()]).status_code == 403
    assert stored_rows(db_session) == []


def test_a_viewer_cannot_post(client, viewer, place):  # noqa: F811
    assert post(client, viewer, place["site"], [item()]).status_code == 403
