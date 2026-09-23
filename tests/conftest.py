"""
conftest.py

Test setup — points the app at a dedicated `rada_test` Postgres database
(derived from DATABASE_URL, never rada_dev) so tests never touch dev
data, forces a known API_KEY/AUTH_ENABLED regardless of the developer's
own .env, and resets the schema before the test session runs.
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

database.Base.metadata.drop_all(bind=database.engine)
database.Base.metadata.create_all(bind=database.engine)

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
    """Wipe asset-related tables before every test so they don't leak into each other."""
    session = database.SessionLocal()
    try:
        session.execute(models.DispatchCommand.__table__.delete())
        session.execute(models.StateOfCharge.__table__.delete())
        session.execute(models.Asset.__table__.delete())
        session.commit()
    finally:
        session.close()
    yield


@pytest.fixture()
def client():
    with TestClient(main.app) as c:
        yield c


@pytest.fixture()
def auth_headers():
    return {"X-API-Key": API_KEY}
