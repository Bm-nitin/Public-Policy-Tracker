"""create email_verification_tokens table

Revision ID: ea7818045cc0
Revises: ef9eb91bff78
Create Date: 2026-09-10 00:00:00.000000

Phase 4: adds the email_verification_tokens table. No other tables are
touched, and Phase 3's users migration is left unmodified - this only
extends the chain (down_revision points at it).

Only token_hash is ever stored (a SHA-256 hex digest, 64 chars) - the raw
token itself never reaches the database, per Phase 4's security
requirements.

Fully reversible: downgrade() drops exactly what upgrade() created.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ea7818045cc0'
down_revision = 'ef9eb91bff78'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'email_verification_tokens',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'user_id', sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('token_hash', sa.String(length=64), nullable=False, unique=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('used_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # token_hash's UNIQUE constraint already gives it an index; user_id is
    # looked up on every resend-verification request, so it gets an
    # explicit index too.
    op.create_index(
        'ix_email_verification_tokens_user_id',
        'email_verification_tokens',
        ['user_id'],
    )


def downgrade():
    op.drop_index('ix_email_verification_tokens_user_id', table_name='email_verification_tokens')
    op.drop_table('email_verification_tokens')
