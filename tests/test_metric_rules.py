"""
test_metric_rules.py

The rules of revision 0003 that `alembic check` cannot see: the level rule
and its guard on metric (triggers), the asset-belongs-to-site rule of
metric_mapping, the language and metric constraints, and the tariff and
fixed-asset constraints.
"""

from datetime import date
from decimal import Decimal

import pytest

from models import (
    FixedAsset, FixedAssetLink, Language, Metric, MetricCategory, MetricKind, MetricLevel,
    MetricMapping, Tariff,
)
from test_site_hierarchy import make_asset, make_portfolio, make_site, violated_constraint


def make_metric(db, key, level):
    metric = Metric(key=key, unit="W", kind=MetricKind.INSTANTANEOUS, level=level, category=MetricCategory.CORE)
    db.add(metric)
    db.flush()
    return metric


@pytest.fixture()
def site_and_asset(db_session):
    site = make_site(db_session, make_portfolio(db_session))
    asset = make_asset(site)
    db_session.add(asset)
    db_session.flush()
    return site, asset


def mapping(site, metric, asset=None, address="meter0/ActivePower"):
    return MetricMapping(site_id=site.id, asset_id=asset.id if asset else None, metric_id=metric.id,
                         source="openems", source_address=address)


# --- Level rule (trigger check_metric_level) ---

@pytest.mark.parametrize("level, with_asset, allowed", [
    (MetricLevel.SITE, False, True),
    (MetricLevel.SITE, True, False),
    (MetricLevel.ASSET, True, True),
    (MetricLevel.ASSET, False, False),
    (MetricLevel.BOTH, True, True),
    (MetricLevel.BOTH, False, True),
])
def test_metric_level_allows_or_refuses_an_asset(db_session, site_and_asset, level, with_asset, allowed):
    site, asset = site_and_asset
    metric = make_metric(db_session, "some_metric", level)
    db_session.add(mapping(site, metric, asset if with_asset else None))

    if allowed:
        db_session.flush()  # no IntegrityError
    else:
        assert violated_constraint(db_session) == "ck_metric_level"


def test_level_rule_also_applies_on_update(db_session, site_and_asset):
    site, asset = site_and_asset
    row = mapping(site, make_metric(db_session, "consumption_power", MetricLevel.SITE))
    db_session.add(row)
    db_session.flush()
    row.asset_id = asset.id

    assert violated_constraint(db_session) == "ck_metric_level"


def test_mapped_asset_must_belong_to_the_site(db_session, site_and_asset):
    _, asset = site_and_asset
    other_site = make_site(db_session, make_portfolio(db_session, name="Other"), name="Other site")
    db_session.flush()
    db_session.add(mapping(other_site, make_metric(db_session, "grid_power", MetricLevel.BOTH), asset))

    assert violated_constraint(db_session) == "fk_metric_mapping_asset_site"


def test_one_mapping_per_site_source_and_address(db_session, site_and_asset):
    site, _ = site_and_asset
    metric = make_metric(db_session, "grid_power", MetricLevel.BOTH)
    db_session.add(mapping(site, metric))
    db_session.flush()
    db_session.add(mapping(site, metric))

    assert violated_constraint(db_session) == "uq_metric_mapping_site_source_address"


# --- Level changes (trigger check_metric_level_change) ---

@pytest.mark.parametrize("new_level, with_asset", [(MetricLevel.SITE, True), (MetricLevel.ASSET, False)])
def test_level_change_contradicted_by_a_mapping_is_refused(db_session, site_and_asset, new_level, with_asset):
    site, asset = site_and_asset
    metric = make_metric(db_session, "grid_power", MetricLevel.BOTH)
    db_session.add(mapping(site, metric, asset if with_asset else None))
    db_session.flush()
    metric.level = new_level

    assert violated_constraint(db_session) == "ck_metric_level"


def test_level_change_no_row_contradicts_is_allowed(db_session, site_and_asset):
    site, _ = site_and_asset
    metric = make_metric(db_session, "grid_power", MetricLevel.BOTH)
    db_session.add(mapping(site, metric))  # site-wide row
    db_session.flush()
    metric.level = MetricLevel.SITE
    db_session.flush()  # no IntegrityError


# --- Language and metric ---

def test_only_one_default_language(db_session):
    db_session.add(Language(code="en", name="English", is_default=True))
    db_session.flush()
    db_session.add(Language(code="fr", name="Français", is_default=True))

    assert violated_constraint(db_session) == "uq_language_single_default"


@pytest.mark.parametrize("key", ["Battery Power", "battery-power", "1st_metric", ""])
def test_metric_key_format(db_session, key):
    db_session.add(Metric(key=key, unit="W", kind=MetricKind.STATE, level=MetricLevel.SITE,
                          category=MetricCategory.CUSTOM))

    assert violated_constraint(db_session) == "ck_metric_key_format"


# --- Tariff ---

def tariff(site, valid_from, valid_to=None, currency="EUR"):
    return Tariff(site_id=site.id, valid_from=valid_from, valid_to=valid_to, currency=currency,
                  import_price_per_kwh=Decimal("0.2"), export_price_per_kwh=Decimal("0.1"))


@pytest.fixture()
def site(db_session):
    site = make_site(db_session, make_portfolio(db_session))
    db_session.flush()
    return site


def test_tariff_end_date_is_inclusive(db_session, site):
    db_session.add(tariff(site, date(2026, 1, 1), date(2026, 12, 31)))
    db_session.flush()
    db_session.add(tariff(site, date(2026, 12, 31), date(2027, 6, 30)))

    assert violated_constraint(db_session) == "ex_tariff_no_overlap"


def test_consecutive_tariff_periods_are_allowed(db_session, site):
    db_session.add_all([tariff(site, date(2026, 1, 1), date(2026, 12, 31)), tariff(site, date(2027, 1, 1))])
    db_session.flush()  # no IntegrityError


def test_open_ended_tariff_cannot_overlap_a_later_period(db_session, site):
    db_session.add(tariff(site, date(2027, 1, 1)))  # valid_to empty: open-ended
    db_session.flush()
    db_session.add(tariff(site, date(2028, 1, 1), date(2028, 12, 31)))

    assert violated_constraint(db_session) == "ex_tariff_no_overlap"


def test_same_period_on_two_sites_is_allowed(db_session, site):
    other = make_site(db_session, site.portfolio, name="Other site")
    db_session.flush()
    db_session.add_all([tariff(site, date(2026, 1, 1)), tariff(other, date(2026, 1, 1))])
    db_session.flush()  # no IntegrityError


def test_tariff_cannot_end_before_it_starts(db_session, site):
    db_session.add(tariff(site, date(2026, 5, 1), date(2026, 4, 30)))

    assert violated_constraint(db_session) == "ck_tariff_valid_period"


def test_tariff_currency_must_be_iso4217(db_session, site):
    db_session.add(tariff(site, date(2026, 1, 1), currency="eur"))

    assert violated_constraint(db_session) == "ck_tariff_currency_iso4217"


# --- Fixed assets ---

def test_fixed_asset_reference_is_unique_within_a_portfolio(db_session):
    first, second = make_portfolio(db_session, "First"), make_portfolio(db_session, "Second")
    db_session.add_all([
        FixedAsset(portfolio_id=first.id, reference="FA-001", description="PV", currency="EUR"),
        FixedAsset(portfolio_id=second.id, reference="FA-001", description="PV", currency="EUR"),
    ])
    db_session.flush()  # the same reference in two portfolios is fine
    db_session.add(FixedAsset(portfolio_id=first.id, reference="FA-001", description="Again", currency="EUR"))

    assert violated_constraint(db_session) == "uq_fixed_asset_portfolio_reference"


def test_an_asset_is_linked_to_a_fixed_asset_once(db_session, site_and_asset):
    site, asset = site_and_asset
    fixed = FixedAsset(portfolio_id=site.portfolio_id, reference="FA-001", description="BESS", currency="EUR")
    db_session.add(fixed)
    db_session.flush()
    db_session.add(FixedAssetLink(fixed_asset_id=fixed.id, asset_id=asset.id))
    db_session.flush()
    db_session.add(FixedAssetLink(fixed_asset_id=fixed.id, asset_id=asset.id))

    assert violated_constraint(db_session) == "uq_fixed_asset_link_pair"
