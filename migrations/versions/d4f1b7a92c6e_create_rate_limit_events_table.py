"""create rate_limit_events table

Revision ID: d4f1b7a92c6e
Revises: c3e8a5f27d1b
Create Date: 2026-10-12 00:00:00.000000

Phase 18: database-backed rate limiting (backend/rate_limit.py). One small
append-only table; nothing else is touched - no existing table, column or
index changes, and no data is migrated.

Why a table and not an in-process counter: the backend runs under gunicorn
(possibly multiple workers/instances) and restarts on every deploy, so a
per-process counter would reset constantly and be per-worker. PostgreSQL is
already shared by every worker, so it is the one store that is correct
without adding Redis.

key_hash is a SHA-256 digest of "<bucket>|<ip / email / user id>" - no raw
email address or IP is stored. Rows older than the longest rate-limit
window are purged opportunistically by the application.

Fully reversible: downgrade() drops exactly what upgrade() created.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4f1b7a92c6e'
down_revision = 'c3e8a5f27d1b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'rate_limit_events',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('bucket', sa.String(length=40), nullable=False),
        sa.Column('key_hash', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_rate_limit_events_lookup', 'rate_limit_events', ['bucket', 'key_hash', 'created_at'], unique=False)
    op.create_index(op.f('ix_rate_limit_events_created_at'), 'rate_limit_events', ['created_at'], unique=False)


def downgrade():
    op.drop_index(op.f('ix_rate_limit_events_created_at'), table_name='rate_limit_events')
    op.drop_index('ix_rate_limit_events_lookup', table_name='rate_limit_events')
    op.drop_table('rate_limit_events')
