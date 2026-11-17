"""
conftest.py

Test setup — points the app at a dedicated `rada_test` Postgres database
(derived from DATABASE_URL, never rada_dev) so tests never touch dev
data, forces a known API_KEY/AUTH_ENABLED regardless of the developer's
own .env, and builds the schema by running the Alembic migrations before
the test session runs.
"""

import os
from dotenv import load_dotenv

load_dotenv()

_dev_url = os.environ["DATABASE_URL"]
os.environ["DATABASE_URL"] = _dev_url.rsplit("/", 1)[0] + "/rada_test"
os.environ["API_KEY"] = "test-api-key"
os.environ["AUTH_ENABLED"] = "true"
os.environ["TELEMETRY_SIMULATOR"] = "false"
os.environ["ENVIRONMENT"] = "development"

import pytest
from fastapi.testclient import TestClient

import database
import models  # noqa: F401 — registers all tables on database.Base.metadata
from alembic import command
from alembic.config import Config
from sqlalchemy import text

ALEMBIC_INI = os.path.join(os.path.dirname(__file__), "..", "alembic.ini")

# Guard: the next step drops every table, so only ever run it on a test DB
if not database.engine.url.database.endswith("_test"):
    raise RuntimeError(
        f"Refusing to reset database {database.engine.url.database!r}: "
        "the test schema is only rebuilt on a database whose name ends in _test."
    )

# Rebuild rada_test from the migrations, exactly as a new install would be
with database.engine.begin() as conn:
    conn.execute(text("DROP SCHEMA public CASCADE"))
    conn.execute(text("CREATE SCHEMA public"))
database.engine.dispose()  # migrations start on a fresh connection
command.upgrade(Config(ALEMBIC_INI), "head")

import main

API_KEY = os.environ["API_KEY"]


@pytest.fixture()
def db_session():
    """A raw session for arranging/asserting DB state directly in tests."""
    session = database.SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def clean_tables():
    """Wipe every data table before each test so tests don't leak into each other."""
    session = database.SessionLocal()
    try:
        # Children before parents, so no foreign key is left pointing at a deleted row
        for model in (
            models.DispatchCommand, models.StateOfCharge,
            models.MetricMapping, models.Tariff, models.FixedAssetLink, models.FixedAsset,
            models.DiscoveredComponent, models.Asset, models.Site, models.SourceConnection, models.Portfolio,
            models.MetricTranslation, models.Metric, models.Language,
        ):
            session.execute(model.__table__.delete())
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture()
def alembic_config():
    return Config(ALEMBIC_INI)


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def auth_headers():
    return {"X-API-Key": API_KEY}
