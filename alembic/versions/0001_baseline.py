"""baseline: the MVP schema of develop at bdf56ba

Revision ID: 0001
Revises:
Create Date: 2026-11-17

Reproduces the schema that create_all() built before Alembic: assets
(including latitude, longitude and edge_id, absorbing
migrations/001_assets_gps_edge.sql), state_of_charge, dispatch_commands and
grid_signals. state_of_charge is a plain table here, not a hypertable.

Existing databases already have this schema: stamp them with
`alembic stamp 0001`, never upgrade them through this revision.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0001'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('assets',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('asset_type', sa.Enum('BATTERY', 'SOLAR', 'WIND', name='assettype'), nullable=False),
    sa.Column('eic_code', sa.String(length=20), nullable=True),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('max_charge_rate_mw', sa.Float(), nullable=False),
    sa.Column('max_discharge_rate_mw', sa.Float(), nullable=False),
    sa.Column('reactive_power_capacity_mvar', sa.Float(), nullable=True),
    sa.Column('max_capacity_mwh', sa.Float(), nullable=False),
    sa.Column('efficiency', sa.Float(), nullable=True),
    sa.Column('latitude', sa.Float(), nullable=True),
    sa.Column('longitude', sa.Float(), nullable=True),
    sa.Column('edge_id', sa.String(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('eic_code')
    )
    op.create_index(op.f('ix_assets_id'), 'assets', ['id'], unique=False)
    op.create_table('grid_signals',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=True),
    sa.Column('total_generation_mw', sa.Float(), nullable=True),
    sa.Column('renewable_mw', sa.Float(), nullable=True),
    sa.Column('renewable_pct', sa.Float(), nullable=True),
    sa.Column('imbalance_mw', sa.Float(), nullable=True),
    sa.Column('imbalance_trend', sa.String(length=20), nullable=True),
    sa.Column('fcr_activated_mw', sa.Float(), nullable=True),
    sa.Column('calculated_frequency_hz', sa.Float(), nullable=True),
    sa.Column('status', sa.String(length=50), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_grid_signals_id'), 'grid_signals', ['id'], unique=False)
    op.create_index(op.f('ix_grid_signals_timestamp'), 'grid_signals', ['timestamp'], unique=False)
    op.create_table('dispatch_commands',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('timestamp', sa.DateTime(), nullable=True),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('command_type', sa.String(length=50), nullable=False),
    sa.Column('power_target_mw', sa.Float(), nullable=False),
    sa.Column('duration_seconds', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_dispatch_commands_id'), 'dispatch_commands', ['id'], unique=False)
    op.create_index(op.f('ix_dispatch_commands_timestamp'), 'dispatch_commands', ['timestamp'], unique=False)
    op.create_table('state_of_charge',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('asset_id', sa.Integer(), nullable=False),
    sa.Column('operational_mode', sa.Enum('ACTIVE', 'CURTAILED', 'HOLDING', 'FAULT', name='gridconnectionstatus'), nullable=True),
    sa.Column('asset_status', sa.Enum('COMMUNICATING', 'UNREACHABLE', name='assetstatus'), nullable=True),
    sa.Column('energy_mwh', sa.Float(), nullable=False),
    sa.Column('voltage', sa.Float(), nullable=True),
    sa.Column('current_amps', sa.Float(), nullable=True),
    sa.Column('temperature_celsius', sa.Float(), nullable=True),
    sa.Column('power_mw', sa.Float(), nullable=True),
    sa.Column('reactive_power_mvar', sa.Float(), nullable=True),
    sa.Column('power_factor', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], ),
    sa.PrimaryKeyConstraint('id', 'timestamp')
    )
    op.create_index(op.f('ix_state_of_charge_timestamp'), 'state_of_charge', ['timestamp'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_state_of_charge_timestamp'), table_name='state_of_charge')
    op.drop_table('state_of_charge')
    op.drop_index(op.f('ix_dispatch_commands_timestamp'), table_name='dispatch_commands')
    op.drop_index(op.f('ix_dispatch_commands_id'), table_name='dispatch_commands')
    op.drop_table('dispatch_commands')
    op.drop_index(op.f('ix_grid_signals_timestamp'), table_name='grid_signals')
    op.drop_index(op.f('ix_grid_signals_id'), table_name='grid_signals')
    op.drop_table('grid_signals')
    op.drop_index(op.f('ix_assets_id'), table_name='assets')
    op.drop_table('assets')
    # drop_table leaves the enum types behind; drop them so upgrade can run again
    bind = op.get_bind()
    for name in ("assetstatus", "gridconnectionstatus", "assettype"):
        sa.Enum(name=name).drop(bind, checkfirst=True)
