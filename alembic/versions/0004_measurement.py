"""measurement: hypertable and measurement_hourly continuous aggregate

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-17

Adds the time-series storage of docs/DATA_MODEL.md §4:

- measurement, a TimescaleDB hypertable on time (7-day chunks). It is created
  without TimescaleDB's default index; ix_measurement_time, declared in the
  models, replaces it, so alembic check sees every index.
- uq_measurement_point: one row per time, site, asset and metric, with
  NULLS NOT DISTINCT so an empty asset_id (a site-wide value) counts as a
  value. TimescaleDB requires time in every unique index of a hypertable.
  Writers use INSERT ... ON CONFLICT DO NOTHING: a duplicate is ignored.
- The level rule: the check_metric_level() trigger function of revision 0003,
  now naming the table passed as the trigger's argument. Rows of a hypertable
  are stored in chunks, so without it the error would name a chunk
  (_hyper_1_1_chunk) instead of measurement. check_metric_level_change() is
  extended to measurement. Downgrade restores both 0003 versions.
- measurement_hourly, a continuous aggregate per UTC hour, site, asset and
  metric (average, minimum, maximum, first, last, sample count), with
  real-time aggregation on and a refresh policy: every 30 minutes, from
  3 days ago to 1 hour ago. Data older than 3 days (CSV import, long
  backfill) needs refresh_continuous_aggregate() by whoever writes it (BE-06).

No MVP history is copied from state_of_charge (decision of 15 November 2026).

CREATE EXTENSION timescaledb needs a superuser on a database that does not
have it yet. Downgrade keeps the extension.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0004'
down_revision: Union[str, Sequence[str], None] = '0003'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The two functions as revision 0003 created them, restored on downgrade
LEVEL_RULE_0003 = """
        CREATE OR REPLACE FUNCTION check_metric_level() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            metric_key   text;
            metric_level metriclevel;
        BEGIN
            SELECT key, level INTO metric_key, metric_level FROM metric WHERE id = NEW.metric_id;
            IF metric_level = 'site' AND NEW.asset_id IS NOT NULL THEN
                RAISE EXCEPTION 'Metric % is site-level: a % row cannot have an asset', metric_key, TG_TABLE_NAME
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_metric_level';
            ELSIF metric_level = 'asset' AND NEW.asset_id IS NULL THEN
                RAISE EXCEPTION 'Metric % is asset-level: a % row needs an asset', metric_key, TG_TABLE_NAME
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_metric_level';
            END IF;
            RETURN NEW;
        END $$
"""
LEVEL_CHANGE_0003 = """
        CREATE OR REPLACE FUNCTION check_metric_level_change() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (NEW.level = 'site' AND EXISTS (
                    SELECT 1 FROM metric_mapping WHERE metric_id = NEW.id AND asset_id IS NOT NULL))
               OR (NEW.level = 'asset' AND EXISTS (
                    SELECT 1 FROM metric_mapping WHERE metric_id = NEW.id AND asset_id IS NULL)) THEN
                RAISE EXCEPTION 'Metric % cannot become %-level: existing metric_mapping rows contradict it',
                    NEW.key, NEW.level
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_metric_level';
            END IF;
            RETURN NEW;
        END $$
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")

    op.create_table('measurement',
    sa.Column('time', sa.DateTime(timezone=True), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=True),
    sa.Column('metric_id', sa.Integer(), nullable=False),
    sa.Column('value', sa.Double(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.ForeignKeyConstraint(['asset_id', 'site_id'], ['assets.id', 'assets.site_id'], name='fk_measurement_asset_site'),
    sa.ForeignKeyConstraint(['metric_id'], ['metric.id'], ),
    sa.ForeignKeyConstraint(['site_id'], ['site.id'], )
    )
    # Our own time index below replaces TimescaleDB's default one
    op.execute("SELECT create_hypertable('measurement', by_range('time'), create_default_indexes => false)")
    op.create_index('ix_measurement_time', 'measurement', [sa.literal_column('time DESC')], unique=False)
    op.create_index('uq_measurement_point', 'measurement', ['metric_id', 'site_id', 'asset_id', 'time'], unique=True, postgresql_nulls_not_distinct=True)

    # ── Level rule (DATA_MODEL.md §3.1), as on metric_mapping ────────────────
    # The trigger's argument names the table in the error: TG_TABLE_NAME would
    # be the chunk the row lands in. metric_mapping's trigger passes none.
    op.execute("""
        CREATE OR REPLACE FUNCTION check_metric_level() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            table_name   text := COALESCE(TG_ARGV[0], TG_TABLE_NAME);
            metric_key   text;
            metric_level metriclevel;
        BEGIN
            SELECT key, level INTO metric_key, metric_level FROM metric WHERE id = NEW.metric_id;
            IF metric_level = 'site' AND NEW.asset_id IS NOT NULL THEN
                RAISE EXCEPTION 'Metric % is site-level: a % row cannot have an asset', metric_key, table_name
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_metric_level';
            ELSIF metric_level = 'asset' AND NEW.asset_id IS NULL THEN
                RAISE EXCEPTION 'Metric % is asset-level: a % row needs an asset', metric_key, table_name
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_metric_level';
            END IF;
            RETURN NEW;
        END $$
    """)
    op.execute("""
        CREATE TRIGGER measurement_check_level
        BEFORE INSERT OR UPDATE OF metric_id, asset_id ON measurement
        FOR EACH ROW EXECUTE FUNCTION check_metric_level('measurement')
    """)
    # A metric's level cannot change under measurements that contradict it either
    op.execute("""
        CREATE OR REPLACE FUNCTION check_metric_level_change() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            contradicting text;
        BEGIN
            IF NEW.level = 'site' THEN
                IF EXISTS (SELECT 1 FROM metric_mapping WHERE metric_id = NEW.id AND asset_id IS NOT NULL) THEN
                    contradicting := 'metric_mapping';
                ELSIF EXISTS (SELECT 1 FROM measurement WHERE metric_id = NEW.id AND asset_id IS NOT NULL) THEN
                    contradicting := 'measurement';
                END IF;
            ELSIF NEW.level = 'asset' THEN
                IF EXISTS (SELECT 1 FROM metric_mapping WHERE metric_id = NEW.id AND asset_id IS NULL) THEN
                    contradicting := 'metric_mapping';
                ELSIF EXISTS (SELECT 1 FROM measurement WHERE metric_id = NEW.id AND asset_id IS NULL) THEN
                    contradicting := 'measurement';
                END IF;
            END IF;
            IF contradicting IS NOT NULL THEN
                RAISE EXCEPTION 'Metric % cannot become %-level: existing % rows contradict it',
                    NEW.key, NEW.level, contradicting
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'ck_metric_level';
            END IF;
            RETURN NEW;
        END $$
    """)

    # ── measurement_hourly (DATA_MODEL.md §4) ────────────────────────────────
    # WITH NO DATA: allowed inside the migration's transaction, and the table is empty anyway
    op.execute("""
        CREATE MATERIALIZED VIEW measurement_hourly WITH (timescaledb.continuous) AS
        SELECT time_bucket('1 hour', time) AS bucket,
               site_id, asset_id, metric_id,
               avg(value)         AS avg_value,
               min(value)         AS min_value,
               max(value)         AS max_value,
               first(value, time) AS first_value,
               last(value, time)  AS last_value,
               count(*)           AS sample_count
        FROM measurement
        GROUP BY bucket, site_id, asset_id, metric_id
        WITH NO DATA
    """)
    # Real-time aggregation: queries add the rows not materialised yet, so the current hour shows
    op.execute("ALTER MATERIALIZED VIEW measurement_hourly SET (timescaledb.materialized_only = false)")
    op.execute("""
        SELECT add_continuous_aggregate_policy('measurement_hourly',
            start_offset      => INTERVAL '3 days',
            end_offset        => INTERVAL '1 hour',
            schedule_interval => INTERVAL '30 minutes')
    """)


def downgrade() -> None:
    """Downgrade schema. The timescaledb extension is kept."""
    # Dropping the aggregate also removes its refresh policy
    op.execute("DROP MATERIALIZED VIEW measurement_hourly")
    op.execute(LEVEL_CHANGE_0003)
    op.execute("DROP TRIGGER measurement_check_level ON measurement")
    op.execute(LEVEL_RULE_0003)
    op.drop_index('uq_measurement_point', table_name='measurement', postgresql_nulls_not_distinct=True)
    op.drop_index('ix_measurement_time', table_name='measurement')
    op.drop_table('measurement')
