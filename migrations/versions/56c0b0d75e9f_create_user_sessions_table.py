"""create user_sessions table

Revision ID: 56c0b0d75e9f
Revises: ea7818045cc0
Create Date: 2026-09-11 00:00:00.000000

Phase 5: adds the user_sessions table backing server-side login sessions.
No other tables are touched, and Phase 4's email_verification_tokens
migration is left unmodified - this only extends the chain (down_revision
points at it).

Only session_token_hash is ever stored (a SHA-256 hex digest, 64 chars) -
the raw session token itself never reaches the database, exactly the same
pattern as email_verification_tokens.token_hash.

Fully reversible: downgrade() drops exactly what upgrade() created.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '56c0b0d75e9f'
down_revision = 'ea7818045cc0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'user_sessions',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id', sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('session_token_hash', sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('revoked_at', sa.DateTime(timezone=True), nullable=True),
    )
    # session_token_hash's UNIQUE constraint already gives it an index;
    # user_id is looked up on every logout, so it gets an explicit index
    # too (same reasoning as email_verification_tokens.user_id).
    op.create_index('ix_user_sessions_user_id', 'user_sessions', ['user_id'])


def downgrade():
    op.drop_index('ix_user_sessions_user_id', table_name='user_sessions')
    op.drop_table('user_sessions')
