"""
test_catalogue_api.py

The metric catalogue routes (docs/DATA_MODEL.md §8): GET /languages,
GET /metrics with and without ?lang=, POST /metrics for custom metrics, and
who may call them.
"""

import pytest

import routers.catalogue
from models import Language, Metric, MetricCategory, MetricTranslation
from seed_metrics import load_metric_catalogue
from test_auth import admin, make_key, viewer  # noqa: F401 — admin and viewer are fixtures

CORE_METRIC_COUNT = 16  # seeds/metrics.toml: 11 core + 5 optional


@pytest.fixture()
def catalogue(db_session):
    """The shipped catalogue (en default, fr, de), committed so the API sees it."""
    load_metric_catalogue(db_session)
    db_session.commit()


def metric_by_key(body, key):
    return next(metric for metric in body if metric["key"] == key)


# --- GET /languages ---

def test_languages_lists_the_active_ones_default_first(client, auth_headers, catalogue):
    response = client.get("/languages", headers=auth_headers)

    assert response.status_code == 200
    assert response.json() == [
        {"code": "en", "name": "English", "is_default": True},
        {"code": "de", "name": "Deutsch", "is_default": False},
        {"code": "fr", "name": "Français", "is_default": False},
    ]


def test_languages_hides_an_inactive_language(client, auth_headers, catalogue, db_session):
    db_session.get(Language, "de").is_active = False
    db_session.commit()

    codes = [language["code"] for language in client.get("/languages", headers=auth_headers).json()]

    assert codes == ["en", "fr"]


# --- GET /metrics ---

def test_metrics_lists_every_metric_with_every_translation(client, auth_headers, catalogue):
    response = client.get("/metrics", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body) == CORE_METRIC_COUNT
    battery = metric_by_key(body, "battery_power")
    assert {field: battery[field] for field in ("unit", "kind", "level", "category", "is_active")} == {
        "unit": "W", "kind": "instantaneous", "level": "both", "category": "core", "is_active": True,
    }
    assert list(battery["translations"]) == ["en", "de", "fr"]
    assert battery["translations"]["fr"]["name"] == "Puissance batterie"
    assert battery["translations"]["en"]["sign_note"]


def test_metrics_leaves_out_names_in_an_inactive_language(client, auth_headers, catalogue, db_session):
    db_session.get(Language, "de").is_active = False
    db_session.commit()

    battery = metric_by_key(client.get("/metrics", headers=auth_headers).json(), "battery_power")

    assert list(battery["translations"]) == ["en", "fr"]


def test_metrics_in_one_language(client, auth_headers, catalogue):
    response = client.get("/metrics", params={"lang": "fr"}, headers=auth_headers)

    assert response.status_code == 200
    battery = metric_by_key(response.json(), "battery_power")
    assert battery["language"] == "fr"
    assert battery["name"] == "Puissance batterie"
    assert "translations" not in battery


def test_metrics_in_one_language_falls_back_to_the_default(client, auth_headers, catalogue, db_session):
    metric = db_session.query(Metric).filter(Metric.key == "battery_power").one()
    db_session.query(MetricTranslation).filter_by(metric_id=metric.id, language_code="fr").delete()
    db_session.commit()

    body = client.get("/metrics", params={"lang": "fr"}, headers=auth_headers).json()

    battery = metric_by_key(body, "battery_power")
    assert (battery["language"], battery["name"]) == ("en", "Battery power")
    assert metric_by_key(body, "grid_power")["language"] == "fr"


def test_metrics_lists_an_inactive_metric_as_inactive(client, auth_headers, catalogue, db_session):
    db_session.query(Metric).filter(Metric.key == "temperature").one().is_active = False
    db_session.commit()

    body = client.get("/metrics", headers=auth_headers).json()

    assert metric_by_key(body, "temperature")["is_active"] is False


@pytest.mark.parametrize("lang", ["xx", "de"])
def test_metrics_refuses_an_unknown_or_inactive_language(client, auth_headers, catalogue, db_session, lang):
    db_session.get(Language, "de").is_active = False
    db_session.commit()

    response = client.get("/metrics", params={"lang": lang}, headers=auth_headers)

    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert (error["loc"], error["type"], error["input"]) == (["query", "lang"], "unknown_language", lang)


# --- POST /metrics ---

CUSTOM_METRIC = {
    "key": "inverter_temperature", "unit": "°C", "kind": "instantaneous", "level": "asset",
    "translations": {"en": {"name": "Inverter temperature", "sign_note": "Heatsink sensor"},
                     "fr": {"name": "Température onduleur"}},
}


def test_create_custom_metric(client, auth_headers, catalogue, db_session):
    response = client.post("/metrics", json=CUSTOM_METRIC, headers=auth_headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert {field: body[field] for field in ("key", "unit", "kind", "level", "category", "is_active")} == {
        "key": "inverter_temperature", "unit": "°C", "kind": "instantaneous", "level": "asset",
        "category": "custom", "is_active": True,
    }
    assert body["translations"] == {
        "en": {"name": "Inverter temperature", "sign_note": "Heatsink sensor"},
        "fr": {"name": "Température onduleur", "sign_note": None},
    }
    stored = db_session.query(Metric).filter(Metric.key == "inverter_temperature").one()
    assert stored.id == body["id"]
    assert stored.category is MetricCategory.CUSTOM
    assert sorted(t.language_code for t in stored.translations) == ["en", "fr"]


def test_created_metric_is_listed_after_the_catalogue(client, auth_headers, catalogue):
    client.post("/metrics", json=CUSTOM_METRIC, headers=auth_headers)

    body = client.get("/metrics", params={"lang": "de"}, headers=auth_headers).json()

    assert body[-1]["key"] == "inverter_temperature"
    assert (body[-1]["language"], body[-1]["name"]) == ("en", "Inverter temperature")


@pytest.mark.parametrize("key", ["battery_power", "inverter_temperature"])
def test_create_metric_refuses_an_existing_key(client, auth_headers, catalogue, key):
    client.post("/metrics", json=CUSTOM_METRIC, headers=auth_headers)

    response = client.post("/metrics", json={**CUSTOM_METRIC, "key": key}, headers=auth_headers)

    assert response.status_code == 409
    assert key in response.json()["detail"]


def test_create_metric_is_409_when_the_key_appears_after_the_check(client, auth_headers, catalogue, db_session,
                                                                  monkeypatch):
    """A concurrent POST can insert the key between the pre-check and the commit: the unique key answers."""
    client.post("/metrics", json=CUSTOM_METRIC, headers=auth_headers)
    monkeypatch.setattr(routers.catalogue, "key_exists", lambda *args: False)

    response = client.post("/metrics", json=CUSTOM_METRIC, headers=auth_headers)

    assert response.status_code == 409
    assert response.json() == {"detail": "A metric with key inverter_temperature already exists"}
    assert db_session.query(Metric).filter(Metric.key == "inverter_temperature").count() == 1


def test_create_metric_needs_a_name_in_the_default_language(client, auth_headers, catalogue, db_session):
    response = client.post("/metrics", headers=auth_headers,
                           json={**CUSTOM_METRIC, "translations": {"fr": {"name": "Température onduleur"}}})

    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert (error["type"], error["loc"]) == ("missing_default_language", ["body", "translations"])
    assert db_session.query(Metric).filter(Metric.key == "inverter_temperature").count() == 0


def test_create_metric_refuses_an_unknown_language(client, auth_headers, catalogue):
    translations = {**CUSTOM_METRIC["translations"], "xx": {"name": "?"}}

    response = client.post("/metrics", json={**CUSTOM_METRIC, "translations": translations}, headers=auth_headers)

    assert response.status_code == 422
    [error] = response.json()["detail"]
    assert (error["type"], error["loc"]) == ("unknown_language", ["body", "translations", "xx"])


@pytest.mark.parametrize("change", [
    {"key": "Inverter-Temp"},           # breaks the key format
    {"key": "9th_sensor"},              # must start with a letter
    {"unit": "  "},                     # unit required
    {"kind": "average"},                # not a kind
    {"category": "core"},               # the category is never chosen by the caller
    {"translations": {"en": {"name": ""}}},
])
def test_create_metric_validates_the_body(client, auth_headers, catalogue, change):
    response = client.post("/metrics", json={**CUSTOM_METRIC, **change}, headers=auth_headers)

    assert response.status_code == 422


def test_create_metric_needs_an_administrator(client, admin):  # noqa: F811
    export_key = {"X-API-Key": make_key(client, admin, "erp", "export")["key"]}
    ingest_key = {"X-API-Key": make_key(client, admin, "bridge", "ingest")["key"]}

    assert client.post("/metrics", json=CUSTOM_METRIC).status_code == 401
    assert client.post("/metrics", json=CUSTOM_METRIC, headers=export_key).status_code == 403
    assert client.post("/metrics", json=CUSTOM_METRIC, headers=ingest_key).status_code == 403


def test_create_metric_is_refused_to_a_viewer(client, viewer):  # noqa: F811
    assert client.post("/metrics", json=CUSTOM_METRIC, headers=viewer).status_code == 403


# --- Who may read ---

@pytest.mark.parametrize("path", ["/languages", "/metrics"])
def test_catalogue_needs_credentials(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ["/languages", "/metrics"])
def test_catalogue_is_refused_to_an_ingest_key(client, admin, path):  # noqa: F811
    ingest = {"X-API-Key": make_key(client, admin, "bridge", "ingest")["key"]}

    assert client.get(path, headers=ingest).status_code == 403


@pytest.mark.parametrize("path", ["/languages", "/metrics"])
def test_catalogue_is_read_by_an_export_key_and_a_user(client, admin, path):  # noqa: F811
    export = {"X-API-Key": make_key(client, admin, "erp", "export")["key"]}

    assert client.get(path, headers=export).status_code == 200
    assert client.get(path, headers=admin).status_code == 200
