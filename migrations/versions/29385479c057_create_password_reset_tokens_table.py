"""create password_reset_tokens table

Revision ID: 29385479c057
Revises: 56c0b0d75e9f
Create Date: 2026-09-11 00:00:00.000000

Phase 6: adds the password_reset_tokens table. No other tables are
touched, and Phase 5's user_sessions migration is left unmodified - this
only extends the chain (down_revision points at it).

Only token_hash is ever stored (a SHA-256 hex digest, 64 chars) - the raw
reset token itself never reaches the database, the same pattern already
used for email_verification_tokens and user_sessions.

Fully reversible: downgrade() drops exactly what upgrade() created.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '29385479c057'
down_revision = '56c0b0d75e9f'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'password_reset_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id', sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('token_hash', sa.String(length=64), nullable=False, unique=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
    )
    # token_hash's UNIQUE constraint already gives it an index; user_id is
    # looked up on every forgot-password request, so it gets an explicit
    # index too (same reasoning as the two previous token tables).
    op.create_index('ix_password_reset_tokens_user_id', 'password_reset_tokens', ['user_id'])


def downgrade():
    op.drop_index('ix_password_reset_tokens_user_id', table_name='password_reset_tokens')
    op.drop_table('password_reset_tokens')
