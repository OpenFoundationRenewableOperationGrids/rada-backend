"""metric catalogue and finance: language, metric, metric_translation, metric_mapping, tariff, fixed_asset, fixed_asset_link

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-17

Adds the metric catalogue of docs/DATA_MODEL.md §2-§3 and the finance tables
of §5. Pure additions: no existing row is touched. The catalogue rows come
from seeds/metrics.toml through seed_metrics.py, never from this migration.

Rules the models cannot express, enforced here:
- the level rule (§3.1): trigger check_metric_level() on metric_mapping
  refuses an asset on a site-level metric and a missing asset on an
  asset-level one. The function is generic so measurement can reuse it;
- check_metric_level_change() on metric refuses a level change that existing
  rows contradict;
- tariff periods of one site cannot overlap (ex_tariff_no_overlap, both ends
  inclusive, an empty valid_to open-ended). It needs the btree_gist extension,
  which is created here and kept on downgrade.

Whether a fixed asset may be linked to an asset of another portfolio is an
open item (DATA_MODEL.md §5): nothing enforces it yet.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0003'
down_revision: Union[str, Sequence[str], None] = '0002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('language',
    sa.Column('code', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('is_default', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('code')
    )
    op.create_index('uq_language_single_default', 'language', ['is_default'], unique=True, postgresql_where=sa.text('is_default'))
    op.create_table('metric',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('key', sa.Text(), nullable=False),
    sa.Column('unit', sa.Text(), nullable=False),
    sa.Column('kind', sa.Enum('instantaneous', 'cumulative', 'state', name='metrickind'), nullable=False),
    sa.Column('level', sa.Enum('site', 'asset', 'both', name='metriclevel'), nullable=False),
    sa.Column('category', sa.Enum('core', 'optional', 'custom', name='metriccategory'), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("key ~ '^[a-z][a-z0-9_]*$'", name='ck_metric_key_format'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key')
    )
    op.create_table('fixed_asset',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('portfolio_id', sa.Integer(), nullable=False),
    sa.Column('reference', sa.Text(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('acquisition_date', sa.Date(), nullable=True),
    sa.Column('acquisition_cost', sa.Numeric(precision=14, scale=2), nullable=True),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('expected_life_months', sa.Integer(), nullable=True),
    sa.Column('external_system', sa.Text(), nullable=True),
    sa.Column('external_id', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_fixed_asset_currency_iso4217'),
    sa.ForeignKeyConstraint(['portfolio_id'], ['portfolio.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('portfolio_id', 'reference', name='uq_fixed_asset_portfolio_reference')
    )
    op.create_table('metric_translation',
    sa.Column('metric_id', sa.Integer(), nullable=False),
    sa.Column('language_code', sa.Text(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('sign_note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['language_code'], ['language.code'], ),
    sa.ForeignKeyConstraint(['metric_id'], ['metric.id'], ),
    sa.PrimaryKeyConstraint('metric_id', 'language_code')
    )
    # The tariff exclusion constraint compares site_id with = inside a GiST index
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table('tariff',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('valid_from', sa.Date(), nullable=False),
    sa.Column('valid_to', sa.Date(), nullable=True),
    sa.Column('import_price_per_kwh', sa.Numeric(precision=10, scale=6), nullable=False, comment='Price per kWh imported, in currency. Energy is stored in Wh.'),
    sa.Column('export_price_per_kwh', sa.Numeric(precision=10, scale=6), nullable=False, comment='Price per kWh exported, in currency. Energy is stored in Wh.'),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('label', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    postgresql.ExcludeConstraint((sa.column('site_id'), '='), (sa.text("daterange(valid_from, valid_to, '[]')"), '&&'), using='gist', name='ex_tariff_no_overlap'),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_tariff_currency_iso4217'),
    sa.CheckConstraint('valid_to IS NULL OR valid_to >= valid_from', name='ck_tariff_valid_period'),
    sa.ForeignKeyConstraint(['site_id'], ['site.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('fixed_asset_link',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('fixed_asset_id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ),
    sa.ForeignKeyConstraint(['fixed_asset_id'], ['fixed_asset.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('fixed_asset_id', 'asset_id', name='uq_fixed_asset_link_pair')
    )
    op.create_index(op.f('ix_fixed_asset_link_asset_id'), 'fixed_asset_link', ['asset_id'], unique=False)
    op.create_table('metric_mapping',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=True),
    sa.Column('metric_id', sa.Integer(), nullable=False),
    sa.Column('source', sa.Text(), nullable=False),
    sa.Column('source_address', sa.Text(), nullable=False),
    sa.Column('scale', sa.Numeric(), server_default=sa.text('1'), nullable=False),
    sa.Column('is_enabled', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['asset_id', 'site_id'], ['assets.id', 'assets.site_id'], name='fk_metric_mapping_asset_site'),
    sa.ForeignKeyConstraint(['metric_id'], ['metric.id'], ),
    sa.ForeignKeyConstraint(['site_id'], ['site.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'source', 'source_address', name='uq_metric_mapping_site_source_address')
    )

    # ── Level rule (DATA_MODEL.md §3.1): the models cannot express it ─────────
    # Generic on purpose: it only reads NEW.metric_id and NEW.asset_id, so the
    # measurement table of the next revision reuses it unchanged.
    op.execute("""
        CREATE FUNCTION check_metric_level() RETURNS trigger LANGUAGE plpgsql AS $$
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
    """)
    op.execute("""
        CREATE TRIGGER metric_mapping_check_level
        BEFORE INSERT OR UPDATE OF metric_id, asset_id ON metric_mapping
        FOR EACH ROW EXECUTE FUNCTION check_metric_level()
    """)
    # A metric's level cannot change under rows that contradict it. The next
    # revision replaces this function to check measurement too.
    op.execute("""
        CREATE FUNCTION check_metric_level_change() RETURNS trigger LANGUAGE plpgsql AS $$
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
    """)
    op.execute("""
        CREATE TRIGGER metric_check_level_change
        BEFORE UPDATE OF level ON metric
        FOR EACH ROW WHEN (OLD.level IS DISTINCT FROM NEW.level)
        EXECUTE FUNCTION check_metric_level_change()
    """)


def downgrade() -> None:
    """Downgrade schema. The btree_gist extension is kept: other objects may use it."""
    op.execute("DROP TRIGGER metric_check_level_change ON metric")
    op.execute("DROP FUNCTION check_metric_level_change()")
    op.execute("DROP TRIGGER metric_mapping_check_level ON metric_mapping")
    op.execute("DROP FUNCTION check_metric_level()")
    op.drop_table('metric_mapping')
    op.drop_index(op.f('ix_fixed_asset_link_asset_id'), table_name='fixed_asset_link')
    op.drop_table('fixed_asset_link')
    op.drop_table('tariff')
    op.drop_table('metric_translation')
    op.drop_table('fixed_asset')
    op.drop_table('metric')
    op.drop_index('uq_language_single_default', table_name='language', postgresql_where=sa.text('is_default'))
    op.drop_table('language')

    # drop_table leaves the enum types behind
    bind = op.get_bind()
    for name in ("metriccategory", "metriclevel", "metrickind"):
        sa.Enum(name=name).drop(bind, checkfirst=True)
