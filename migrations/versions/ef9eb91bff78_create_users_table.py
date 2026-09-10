"""create users table

Revision ID: ef9eb91bff78
Revises: efae76a75873
Create Date: 2026-09-10 00:00:00.000000

Phase 3: adds the `users` table backing secure registration. No other
tables/columns are touched, and Phase 2's initial (empty) revision is left
unmodified - this migration only extends the chain (down_revision points
at it).

Fully reversible: downgrade() drops exactly what upgrade() created, and
nothing else.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'ef9eb91bff78'
down_revision = 'efae76a75873'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False, unique=True),
        sa.Column('password_hash', sa.String(length=255), nullable=False),
        sa.Column(
            'email_verified', sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('last_login', sa.DateTime(timezone=True), nullable=True),
    )
    # Postgres automatically creates a unique index backing the UNIQUE
    # constraint above, so no separate op.create_index() call is added
    # here - it would just be a redundant second index on the same column.


def downgrade():
    op.drop_table('users')
