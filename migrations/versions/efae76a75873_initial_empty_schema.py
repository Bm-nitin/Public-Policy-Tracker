"""initial empty schema

Revision ID: efae76a75873
Revises:
Create Date: 2026-09-10 00:00:00.000000

This is Phase 2's initial migration. It intentionally creates no tables.

Phase 2 objectives 7-12 forbid migrating the policy JSON into the database
and forbid creating users/auth/chat-history/saved-policies tables in this
phase - which leaves no feature that currently needs a table. Rather than
guess at a future schema ("do not blindly create the entire future
architecture" - Phase 2 brief), this revision exists only to prove the
migration system itself is wired up and version-controlled: running
`flask db upgrade` against a configured PostgreSQL database will create
Alembic's own `alembic_version` tracking table and stamp it at this
revision, with no other schema changes.

A future phase adds real model classes (e.g. to backend/database.py or a
new models.py) and a new revision on top of this one via
`flask db migrate`.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'efae76a75873'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
