"""security: app_user and api_key

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-24

Adds the security tables of docs/DATA_MODEL.md §5, used by BE-04 (docs/AUTH.md):

- app_user: a person who signs in, with an Argon2 password hash, the role
  admin or viewer, and the fields that prepare SSO (auth_method,
  external_subject; only 'local' is used in 1.0). Emails are stored
  lowercased (ck_app_user_email_lowercase), so the unique key is
  case-insensitive. A local user must have a password hash
  (ck_app_user_local_has_password). sessions_valid_after ends every session
  issued before it (logout, password change, deactivation).
- api_key: a named key per machine integration, with the scope ingest,
  export or admin. Only the SHA-256 of the key is stored, with its first
  characters (prefix) to recognise it in a list. Revoking a key fills
  revoked_at; the row is kept.

Pure additions: no row is created. There is no default user and no default
key; the first administrator is created by POST /auth/setup.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0005'
down_revision: Union[str, Sequence[str], None] = '0004'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('app_user',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.Text(), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=True),
    sa.Column('role', sa.Enum('admin', 'viewer', name='userrole'), nullable=False),
    sa.Column('auth_method', sa.Enum('local', 'sso', name='authmethod'), server_default='local', nullable=False),
    sa.Column('external_subject', sa.Text(), nullable=True),
    sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('sessions_valid_after', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('email = lower(email)', name='ck_app_user_email_lowercase'),
    sa.CheckConstraint("auth_method <> 'local' OR password_hash IS NOT NULL", name='ck_app_user_local_has_password'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('api_key',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.Text(), nullable=False),
    sa.Column('prefix', sa.Text(), nullable=False),
    sa.Column('key_hash', sa.Text(), nullable=False),
    sa.Column('scope', sa.Enum('ingest', 'export', 'admin', name='apikeyscope'), nullable=False),
    sa.Column('created_by_id', sa.Integer(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['created_by_id'], ['app_user.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('key_hash'),
    sa.UniqueConstraint('name')
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table('api_key')
    op.drop_table('app_user')

    # drop_table leaves the enum types behind
    bind = op.get_bind()
    for name in ("apikeyscope", "authmethod", "userrole"):
        sa.Enum(name=name).drop(bind, checkfirst=True)
