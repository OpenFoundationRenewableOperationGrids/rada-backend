"""
test_measurement.py

The time-series storage of revision 0004 (docs/DATA_MODEL.md §4): the
measurement hypertable and its rules, and the measurement_hourly continuous
aggregate. Also that revision 0004 can be undone.
"""

from datetime import datetime, timezone

import pytest
from alembic import command
from sqlalchemy import insert, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

import database
from models import Metric, MetricCategory, MetricKind, MetricLevel, measurement
from test_site_hierarchy import make_asset, make_portfolio, make_site, violated_constraint

T0 = datetime(2026, 11, 17, 10, 0, tzinfo=timezone.utc)


def make_metric(db, key, level):
    metric = Metric(key=key, unit="W", kind=MetricKind.INSTANTANEOUS, level=level, category=MetricCategory.CORE)
    db.add(metric)
    db.flush()
    return metric


@pytest.fixture()
def setup(db_session):
    """A site with one asset, and a metric of each level."""
    site = make_site(db_session, make_portfolio(db_session))
    asset = make_asset(site)
    db_session.add(asset)
    db_session.flush()
    metrics = {level: make_metric(db_session, f"metric_{level.value}", level) for level in MetricLevel}
    return site, asset, metrics


def row(site, metric, asset=None, at=T0, value=1.0):
    return {"time": at, "site_id": site.id, "asset_id": asset.id if asset else None,
            "metric_id": metric.id, "value": value, "source": "test"}


def insert_ignoring_duplicates(db, rows):
    """How writers store a batch: a row already stored is ignored, not an error.
    Returns how many rows were stored (counted with RETURNING: rowcount can be -1)."""
    statement = pg_insert(measurement).values(rows).on_conflict_do_nothing().returning(measurement.c.time)
    return len(db.execute(statement).all())


# --- Schema ---

def test_measurement_is_a_hypertable_without_timescaledb_default_index(db_session):
    hypertables = db_session.execute(text(
        "SELECT hypertable_name FROM timescaledb_information.hypertables")).scalars().all()
    indexes = db_session.execute(text(
        "SELECT indexname FROM pg_indexes WHERE tablename = 'measurement'")).scalars().all()

    assert hypertables == ["measurement"]
    assert sorted(indexes) == ["ix_measurement_time", "uq_measurement_point"]


def test_hourly_aggregate_is_real_time_with_its_refresh_policy(db_session):
    materialized_only = db_session.execute(text("""
        SELECT materialized_only FROM timescaledb_information.continuous_aggregates
        WHERE view_name = 'measurement_hourly'
    """)).scalar_one()
    # The jobs view files a refresh policy under the aggregate's own name
    schedule, config = db_session.execute(text("""
        SELECT schedule_interval::text, config FROM timescaledb_information.jobs
        WHERE hypertable_name = 'measurement_hourly' AND application_name LIKE 'Refresh Continuous%'
    """)).one()

    assert materialized_only is False
    assert schedule == "00:30:00"
    assert (config["start_offset"], config["end_offset"]) == ("3 days", "01:00:00")


# --- One row per time, site, asset and metric ---

def test_a_batch_posted_twice_is_stored_once(db_session, setup):
    site, asset, metrics = setup
    both = metrics[MetricLevel.BOTH]
    first = [row(site, both, value=100.0), row(site, both, asset, value=40.0)]  # a site row and an asset row
    again = [row(site, both, value=999.0), row(site, both, asset, value=999.0)]

    assert insert_ignoring_duplicates(db_session, first) == 2
    assert insert_ignoring_duplicates(db_session, again) == 0
    values = db_session.execute(select(measurement.c.value).order_by(measurement.c.value)).scalars().all()
    assert values == [40.0, 100.0]  # the first post is kept


def test_an_empty_asset_counts_as_a_value(db_session, setup):
    site, _, metrics = setup
    db_session.execute(insert(measurement).values(row(site, metrics[MetricLevel.SITE])))
    db_session.flush()

    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(insert(measurement).values(row(site, metrics[MetricLevel.SITE])))
    db_session.rollback()
    # Each chunk has its own copy of the index, named after the chunk:
    # PostgreSQL reports e.g. _hyper_1_2_chunk_uq_measurement_point
    assert excinfo.value.orig.diag.constraint_name.endswith("uq_measurement_point")


# --- Level rule and asset/site ---

@pytest.mark.parametrize("level, with_asset, allowed", [
    (MetricLevel.SITE, False, True),
    (MetricLevel.SITE, True, False),
    (MetricLevel.ASSET, True, True),
    (MetricLevel.ASSET, False, False),
    (MetricLevel.BOTH, True, True),
    (MetricLevel.BOTH, False, True),
])
def test_metric_level_allows_or_refuses_an_asset(db_session, setup, level, with_asset, allowed):
    site, asset, metrics = setup
    statement = insert(measurement).values(row(site, metrics[level], asset if with_asset else None))

    if allowed:
        db_session.execute(statement)  # no IntegrityError
    else:
        with pytest.raises(IntegrityError) as excinfo:
            db_session.execute(statement)
        db_session.rollback()
        assert excinfo.value.orig.diag.constraint_name == "ck_metric_level"
        # Named after the table, not the chunk the row would land in
        assert "a measurement row" in str(excinfo.value.orig)


def test_measured_asset_must_belong_to_the_site(db_session, setup):
    _, asset, metrics = setup
    other_site = make_site(db_session, make_portfolio(db_session, name="Other"), name="Other site")
    db_session.flush()

    with pytest.raises(IntegrityError) as excinfo:
        db_session.execute(insert(measurement).values(row(other_site, metrics[MetricLevel.BOTH], asset)))
    db_session.rollback()
    assert excinfo.value.orig.diag.constraint_name == "fk_measurement_asset_site"


@pytest.mark.parametrize("new_level, with_asset", [(MetricLevel.SITE, True), (MetricLevel.ASSET, False)])
def test_level_change_contradicted_by_a_measurement_is_refused(db_session, setup, new_level, with_asset):
    site, asset, metrics = setup
    metric = metrics[MetricLevel.BOTH]
    db_session.execute(insert(measurement).values(row(site, metric, asset if with_asset else None)))
    metric.level = new_level

    assert violated_constraint(db_session) == "ck_metric_level"


# --- measurement_hourly ---

def test_hourly_aggregate_shows_new_rows_at_once(db_session, setup):
    site, _, metrics = setup
    metric = metrics[MetricLevel.SITE]
    db_session.execute(insert(measurement).values([
        row(site, metric, at=T0.replace(minute=minute), value=value)
        for minute, value in [(0, 10.0), (20, 30.0), (40, 20.0)]
    ]))
    db_session.commit()

    hour = db_session.execute(text("""
        SELECT bucket, avg_value, min_value, max_value, first_value, last_value, sample_count
        FROM measurement_hourly WHERE site_id = :site_id AND metric_id = :metric_id
    """), {"site_id": site.id, "metric_id": metric.id}).one()

    assert hour == (T0, 20.0, 10.0, 30.0, 10.0, 20.0, 3)


def test_hourly_aggregate_materialises_on_refresh(db_session, setup):
    site, _, metrics = setup
    db_session.execute(insert(measurement).values(row(site, metrics[MetricLevel.SITE], value=5.0)))
    db_session.commit()

    # refresh_continuous_aggregate() cannot run inside a transaction
    with database.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        conn.execute(text("CALL refresh_continuous_aggregate('measurement_hourly', NULL, NULL)"))
        table = conn.execute(text("""
            SELECT format('%I.%I', materialization_hypertable_schema, materialization_hypertable_name)
            FROM timescaledb_information.continuous_aggregates WHERE view_name = 'measurement_hourly'
        """)).scalar_one()
        materialised = conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()

    assert materialised == 1


# --- Revision 0004 can be undone ---

def test_downgrade_of_0004_removes_the_hypertable_and_restores_0003(alembic_config):
    database.engine.dispose()  # no pooled connection holding a lock
    command.downgrade(alembic_config, "0003")
    try:
        with database.engine.connect() as conn:
            assert conn.execute(text("SELECT to_regclass('measurement')")).scalar() is None
            assert conn.execute(text("SELECT count(*) FROM timescaledb_information.hypertables")).scalar() == 0
            assert conn.execute(text(
                "SELECT count(*) FROM timescaledb_information.jobs WHERE application_name LIKE 'Refresh Continuous%'"
            )).scalar() == 0
            # Both functions are back to their 0003 versions
            level_rule, level_change = conn.execute(text("""
                SELECT p.prosrc FROM pg_proc p
                WHERE p.proname IN ('check_metric_level', 'check_metric_level_change') ORDER BY p.proname
            """)).scalars().all()
            assert "TG_ARGV" not in level_rule
            assert "measurement" not in level_change
    finally:
        database.engine.dispose()
        command.upgrade(alembic_config, "head")
