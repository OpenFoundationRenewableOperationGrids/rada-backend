"""site hierarchy: portfolio, source_connection, site, discovered_component

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-17

Adds the site hierarchy of docs/DATA_MODEL.md §2, extends assets with a
required site_id and the V1.0 columns, adds METER and INVERTER to assettype,
and converts assets.created_at/updated_at to timestamptz (the MVP wrote them
in UTC: see docs/MIGRATIONS.md for the check to run first).

Data migration (DATA_MODEL.md §6.1), only when assets exist, so a new install
gets no Demo portfolio:
- a Demo portfolio (EUR);
- one site per distinct edge_id, named after the edge, with no
  source_connection yet (the link comes with BE-05 or the seed, never from a
  migration, since the Backend URL differs per environment);
- one site per other asset, named after it and carrying its position.

These one-site-per-asset sites are a transitional mapping of the MVP fleet,
so the map and the fleet dashboard keep working. They are replaced by the
demo portfolio of DATA_MODEL.md §7 when the seed is rewritten.

rated_power_w and capacity_wh are left empty: the MVP MW/MWh columns are
copied when they are retired (DATA_MODEL.md §2).

Downgrade drops the sites (they are derived from the assets) and refuses to
run while an asset or a discovered component uses METER or INVERTER.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0002'
down_revision: Union[str, Sequence[str], None] = '0001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# The existing type, referenced without CREATE TYPE
assettype = postgresql.ENUM(name='assettype', create_type=False)


def upgrade() -> None:
    """Upgrade schema."""
    # PostgreSQL 12+ allows this in a transaction, as long as the new values
    # are not used in the same transaction. Nothing in this revision uses them.
    op.execute("ALTER TYPE assettype ADD VALUE IF NOT EXISTS 'METER'")
    op.execute("ALTER TYPE assettype ADD VALUE IF NOT EXISTS 'INVERTER'")

    op.create_table('portfolio',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('external_ref', sa.Text(), nullable=True),
    sa.Column('currency', sa.CHAR(length=3), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_portfolio_currency_iso4217'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('source_connection',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('kind', sa.Enum('openems', name='sourcekind'), nullable=False),
    sa.Column('url', sa.Text(), nullable=False),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('site',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('portfolio_id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('source_connection_id', sa.Integer(), nullable=True),
    sa.Column('edge_id', sa.Text(), nullable=True),
    sa.Column('timezone', sa.Text(), nullable=False),
    sa.Column('currency', sa.CHAR(length=3), nullable=True),
    sa.Column('latitude', sa.Numeric(), nullable=True),
    sa.Column('longitude', sa.Numeric(), nullable=True),
    sa.Column('export_limit_w', sa.Integer(), nullable=True),
    sa.Column('external_ref', sa.Text(), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("currency ~ '^[A-Z]{3}$'", name='ck_site_currency_iso4217'),
    sa.ForeignKeyConstraint(['portfolio_id'], ['portfolio.id'], ),
    sa.ForeignKeyConstraint(['source_connection_id'], ['source_connection.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('source_connection_id', 'edge_id', name='uq_site_source_connection_edge')
    )
    op.create_index(op.f('ix_site_portfolio_id'), 'site', ['portfolio_id'], unique=False)
    op.create_index('uq_site_edge_without_connection', 'site', ['edge_id'], unique=True, postgresql_where=sa.text('source_connection_id IS NULL'))

    # site_id starts nullable: the existing rows get their site below
    op.add_column('assets', sa.Column('site_id', sa.Integer(), nullable=True))
    op.add_column('assets', sa.Column('source_component', sa.Text(), nullable=True))
    op.add_column('assets', sa.Column('serial_number', sa.Text(), nullable=True))
    op.add_column('assets', sa.Column('manufacturer', sa.Text(), nullable=True))
    op.add_column('assets', sa.Column('model', sa.Text(), nullable=True))
    op.add_column('assets', sa.Column('commissioning_date', sa.Date(), nullable=True))
    op.add_column('assets', sa.Column('warranty_end', sa.Date(), nullable=True))
    op.add_column('assets', sa.Column('rated_power_w', sa.Integer(), nullable=True))
    op.add_column('assets', sa.Column('capacity_wh', sa.Integer(), nullable=True))
    op.add_column('assets', sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.create_foreign_key('fk_assets_site', 'assets', 'site', ['site_id'], ['id'])
    op.create_index(op.f('ix_assets_site_id'), 'assets', ['site_id'], unique=False)
    op.create_unique_constraint('uq_assets_id_site', 'assets', ['id', 'site_id'])
    op.create_index('uq_assets_site_source_component', 'assets', ['site_id', 'source_component'], unique=True, postgresql_where=sa.text('source_component IS NOT NULL'))

    # Needs uq_assets_id_site above for its composite foreign key
    op.create_table('discovered_component',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('site_id', sa.Integer(), nullable=False),
    sa.Column('source_component', sa.Text(), nullable=False),
    sa.Column('alias', sa.Text(), nullable=True),
    sa.Column('factory_id', sa.Text(), nullable=True),
    sa.Column('natures', postgresql.ARRAY(sa.Text()), server_default=sa.text("'{}'"), nullable=False),
    sa.Column('suggested_asset_type', assettype, nullable=True),
    sa.Column('status', sa.Enum('pending', 'confirmed', 'ignored', name='discoverystatus'), server_default='pending', nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['asset_id', 'site_id'], ['assets.id', 'assets.site_id'], name='fk_discovered_component_asset_site'),
    sa.ForeignKeyConstraint(['site_id'], ['site.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_id', 'source_component', name='uq_discovered_component_site_component')
    )

    # ── Data migration: give every existing asset a site (§6.1) ──────────────
    op.execute("""
        INSERT INTO portfolio (name, currency)
        SELECT 'Demo', 'EUR' WHERE EXISTS (SELECT 1 FROM assets)
    """)
    # One site per edge. Its position is the first asset of the edge that has one.
    op.execute("""
        INSERT INTO site (portfolio_id, name, edge_id, timezone, latitude, longitude)
        SELECT p.id, e.edge_id, e.edge_id, 'Europe/Paris', e.latitude, e.longitude
        FROM (
            SELECT DISTINCT ON (edge_id) edge_id, latitude, longitude
            FROM assets
            WHERE edge_id IS NOT NULL
            ORDER BY edge_id, (latitude IS NULL OR longitude IS NULL), id
        ) e
        CROSS JOIN portfolio p
        WHERE p.name = 'Demo'
    """)
    op.execute("""
        UPDATE assets a SET site_id = s.id
        FROM site s
        WHERE s.source_connection_id IS NULL AND s.edge_id = a.edge_id
    """)
    # One site per other asset. The site ids are drawn first, so each asset
    # knows its site without relying on names being unique.
    op.execute("""
        CREATE TEMPORARY TABLE asset_site ON COMMIT DROP AS
        SELECT id AS asset_id, nextval(pg_get_serial_sequence('site', 'id')) AS site_id,
               name, latitude, longitude
        FROM assets
        WHERE edge_id IS NULL
    """)
    op.execute("""
        INSERT INTO site (id, portfolio_id, name, timezone, latitude, longitude)
        SELECT x.site_id, p.id, x.name, 'Europe/Paris', x.latitude, x.longitude
        FROM asset_site x
        CROSS JOIN portfolio p
        WHERE p.name = 'Demo'
    """)
    op.execute("""
        UPDATE assets a SET site_id = x.site_id
        FROM asset_site x
        WHERE x.asset_id = a.id
    """)
    op.execute("DROP TABLE asset_site")

    op.alter_column('assets', 'site_id', existing_type=sa.Integer(), nullable=False)

    # The MVP stored UTC wall-clock times; say so explicitly rather than rely
    # on the session time zone
    for column in ('created_at', 'updated_at'):
        op.alter_column('assets', column,
                   existing_type=postgresql.TIMESTAMP(),
                   type_=sa.DateTime(timezone=True),
                   existing_nullable=True,
                   postgresql_using=f"{column} AT TIME ZONE 'UTC'")


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM assets WHERE asset_type IN ('METER', 'INVERTER'))
               OR EXISTS (SELECT 1 FROM discovered_component
                          WHERE suggested_asset_type IN ('METER', 'INVERTER')) THEN
                RAISE EXCEPTION 'Cannot downgrade 0002: METER or INVERTER is in use';
            END IF;
        END $$
    """)

    for column in ('created_at', 'updated_at'):
        op.alter_column('assets', column,
                   existing_type=sa.DateTime(timezone=True),
                   type_=postgresql.TIMESTAMP(),
                   existing_nullable=True,
                   postgresql_using=f"{column} AT TIME ZONE 'UTC'")

    op.drop_table('discovered_component')
    op.drop_index('uq_assets_site_source_component', table_name='assets', postgresql_where=sa.text('source_component IS NOT NULL'))
    op.drop_constraint('uq_assets_id_site', 'assets', type_='unique')
    op.drop_index(op.f('ix_assets_site_id'), table_name='assets')
    op.drop_constraint('fk_assets_site', 'assets', type_='foreignkey')
    op.drop_column('assets', 'is_active')
    op.drop_column('assets', 'capacity_wh')
    op.drop_column('assets', 'rated_power_w')
    op.drop_column('assets', 'warranty_end')
    op.drop_column('assets', 'commissioning_date')
    op.drop_column('assets', 'model')
    op.drop_column('assets', 'manufacturer')
    op.drop_column('assets', 'serial_number')
    op.drop_column('assets', 'source_component')
    op.drop_column('assets', 'site_id')
    op.drop_index('uq_site_edge_without_connection', table_name='site', postgresql_where=sa.text('source_connection_id IS NULL'))
    op.drop_index(op.f('ix_site_portfolio_id'), table_name='site')
    op.drop_table('site')
    op.drop_table('source_connection')
    op.drop_table('portfolio')

    # drop_table leaves the enum types behind
    bind = op.get_bind()
    for name in ("discoverystatus", "sourcekind"):
        sa.Enum(name=name).drop(bind, checkfirst=True)

    # PostgreSQL cannot remove a value from an enum: rebuild assettype without them
    op.execute("ALTER TYPE assettype RENAME TO assettype_0002")
    op.execute("CREATE TYPE assettype AS ENUM ('BATTERY', 'SOLAR', 'WIND')")
    op.execute("ALTER TABLE assets ALTER COLUMN asset_type TYPE assettype USING asset_type::text::assettype")
    op.execute("DROP TYPE assettype_0002")
