"""
test_site_hierarchy.py

The site hierarchy of revision 0002 (docs/DATA_MODEL.md §2): the constraints
the database enforces, which `alembic check` cannot see, and the transitional
MVP rule of mvp_sites.py that gives every new asset a site.
"""

from datetime import datetime, timezone

import pytest
from sqlalchemy.exc import IntegrityError

import main
from models import Asset, AssetType, DiscoveredComponent, Portfolio, Site, SourceConnection, SourceKind
from mvp_sites import DEMO_PORTFOLIO, MVP_TIMEZONE, site_for_new_asset
from test_assets_crud import make_asset_payload


def make_portfolio(db, name="Test portfolio"):
    portfolio = Portfolio(name=name, currency="EUR")
    db.add(portfolio)
    db.flush()
    return portfolio


def make_site(db, portfolio, **overrides):
    values = {"name": "Test site", "timezone": "Europe/Paris", "portfolio": portfolio}
    values.update(overrides)
    site = Site(**values)
    db.add(site)
    return site


def make_asset(site, **overrides):
    values = {
        "eic_code": None, "name": "Battery", "asset_type": AssetType.BATTERY,
        "max_capacity_mwh": 1.0, "max_charge_rate_mw": 0.5, "max_discharge_rate_mw": 0.5,
        "site": site,
    }
    values.update(overrides)
    return Asset(**values)


def violated_constraint(db):
    """Flush, expect an IntegrityError, and return the name of the violated constraint."""
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    db.rollback()
    return excinfo.value.orig.diag.constraint_name


# --- Transitional MVP rule (mvp_sites.py) ---

def test_post_asset_gets_its_own_site(client, db_session, auth_headers):
    payload = make_asset_payload(latitude=48.7115, longitude=2.171)
    response = client.post("/assets", json=payload, headers=auth_headers)

    assert response.status_code == 201
    asset = db_session.get(Asset, response.json()["asset_id"])
    assert asset.site.name == "Battery One"
    assert asset.site.edge_id is None
    assert float(asset.site.latitude) == 48.7115
    assert asset.site.timezone == MVP_TIMEZONE
    assert asset.site.portfolio.name == DEMO_PORTFOLIO


def test_post_assets_on_one_edge_share_its_site(client, db_session, auth_headers):
    first = client.post("/assets", json=make_asset_payload(eic_code="A", edge_id="edge0"), headers=auth_headers)
    second = client.post("/assets", json=make_asset_payload(eic_code="B", edge_id="edge0"), headers=auth_headers)

    sites = {db_session.get(Asset, r.json()["asset_id"]).site_id for r in (first, second)}
    assert len(sites) == 1
    assert db_session.query(Site).one().name == "edge0"


def test_two_edge_assets_in_one_session_share_one_site(db_session):
    # Without autoflush, the second lookup only finds the site because the helper flushes it
    with db_session.no_autoflush:
        first = site_for_new_asset(db_session, name="A", edge_id="edge0", latitude=None, longitude=None)
        second = site_for_new_asset(db_session, name="B", edge_id="edge0", latitude=None, longitude=None)
        db_session.add_all([make_asset(first, name="A"), make_asset(second, name="B")])
    db_session.commit()

    assert first is second
    assert db_session.query(Site).count() == 1
    assert db_session.query(Portfolio).count() == 1


def test_eic_code_conflict_is_recognised(db_session):
    site = site_for_new_asset(db_session, name="A", edge_id=None, latitude=None, longitude=None)
    db_session.add_all([make_asset(site, eic_code="DUP", name="A"), make_asset(site, eic_code="DUP", name="B")])
    with pytest.raises(IntegrityError) as excinfo:
        db_session.flush()
    db_session.rollback()

    assert main.is_eic_code_conflict(excinfo.value)


def test_other_integrity_errors_are_not_eic_conflicts(db_session):
    db_session.add(Portfolio(name="Bad currency", currency="eur"))
    with pytest.raises(IntegrityError) as excinfo:
        db_session.flush()
    db_session.rollback()

    assert not main.is_eic_code_conflict(excinfo.value)


# --- Constraints of revision 0002 ---

def test_second_unlinked_site_on_the_same_edge_is_refused(db_session):
    portfolio = make_portfolio(db_session)
    make_site(db_session, portfolio, name="first", edge_id="edge0")
    db_session.flush()
    make_site(db_session, portfolio, name="second", edge_id="edge0")

    assert violated_constraint(db_session) == "uq_site_edge_without_connection"


def test_sites_without_an_edge_are_not_duplicates(db_session):
    portfolio = make_portfolio(db_session)
    make_site(db_session, portfolio, name="first")
    make_site(db_session, portfolio, name="second")
    db_session.flush()  # no IntegrityError


def test_same_edge_on_two_connections_is_allowed(db_session):
    portfolio = make_portfolio(db_session)
    for name in ("backend-a", "backend-b"):
        connection = SourceConnection(name=name, kind=SourceKind.OPENEMS, url=f"wss://{name}")
        make_site(db_session, portfolio, name=name, edge_id="edge0", source_connection=connection)
    db_session.flush()  # no IntegrityError


def test_same_edge_twice_on_one_connection_is_refused(db_session):
    portfolio = make_portfolio(db_session)
    connection = SourceConnection(name="backend", kind=SourceKind.OPENEMS, url="wss://backend")
    make_site(db_session, portfolio, name="first", edge_id="edge0", source_connection=connection)
    db_session.flush()
    make_site(db_session, portfolio, name="second", edge_id="edge0", source_connection=connection)

    assert violated_constraint(db_session) == "uq_site_source_connection_edge"


@pytest.mark.parametrize("currency", ["eur", "EU", "E1R"])
def test_currency_must_be_three_capital_letters(db_session, currency):
    db_session.add(Portfolio(name="Bad", currency=currency))

    assert violated_constraint(db_session) == "ck_portfolio_currency_iso4217"


def test_source_component_is_unique_within_a_site(db_session):
    site = make_site(db_session, make_portfolio(db_session))
    db_session.add_all([
        make_asset(site, name="no component 1"), make_asset(site, name="no component 2"),
        make_asset(site, name="ess0 first", source_component="ess0"),
    ])
    db_session.flush()  # two empty source_components are fine
    db_session.add(make_asset(site, name="ess0 again", source_component="ess0"))

    assert violated_constraint(db_session) == "uq_assets_site_source_component"


def test_asset_requires_a_site(db_session):
    db_session.add(make_asset(None))

    with pytest.raises(IntegrityError, match="site_id"):
        db_session.flush()
    db_session.rollback()


def test_confirmed_component_must_be_an_asset_of_the_same_site(db_session):
    portfolio = make_portfolio(db_session)
    here, elsewhere = make_site(db_session, portfolio, name="here"), make_site(db_session, portfolio, name="elsewhere")
    asset_elsewhere = make_asset(elsewhere)
    db_session.add(asset_elsewhere)
    db_session.flush()
    now = datetime.now(timezone.utc)
    db_session.add(DiscoveredComponent(
        site_id=here.id, source_component="ess0", asset_id=asset_elsewhere.id,
        first_seen_at=now, last_seen_at=now,
    ))

    assert violated_constraint(db_session) == "fk_discovered_component_asset_site"
