"""create saved_policies table

Revision ID: c3e8a5f27d1b
Revises: b7d2f4a1c8e6
Create Date: 2026-09-21 00:00:00.000000

Phase 13: adds a saved_policies table - a bookmark relationship ("this
user saved this policy"), nothing else. data/*.json (via
backend/policy_loader.py) remains the sole source of truth for policy
content; this table stores ONLY user_id, policy_id, and created_at - no
name/category/sub_category/change/impact column exists here at all, so
there is nothing to duplicate or let go stale relative to the JSON
dataset (see backend/models.py's SavedPolicy docstring).

Deliberately isolated from every other table in this chain:
  - user_id -> users.id (the only foreign key here)
  - policy_id has NO foreign key - there is no `policies` table left to
    reference (see e6a3a2e50453 / backend/models.py's retirement note
    for that table); it is only ever compared against
    backend/policy_loader.py's stable, JSON-derived ids at the
    application layer.
  - Does not touch conversations/messages (Phase 12), policy_embeddings
    (Phase 10), or any authentication table.

(user_id, policy_id) is UNIQUE - the actual database-level duplicate-
save prevention (see backend/saved_policies_service.py for how
application code handles the race this guards against).

Indexes: user_id (list-own-saved-policies lookups), policy_id
(look-up-by-policy), created_at (default newest-first ordering). The
unique constraint above already provides an index covering (user_id,
policy_id) together for the single-relationship lookups
(get_saved_policy/delete_saved_policy), so no separate index is added
for that exact pair.

Fully reversible: downgrade() drops exactly what upgrade() created.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c3e8a5f27d1b'
down_revision = 'b7d2f4a1c8e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'saved_policies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('policy_id', sa.Integer(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint('user_id', 'policy_id', name='uq_saved_policies_user_policy'),
    )
    op.create_index('ix_saved_policies_user_id', 'saved_policies', ['user_id'])
    op.create_index('ix_saved_policies_policy_id', 'saved_policies', ['policy_id'])
    op.create_index('ix_saved_policies_created_at', 'saved_policies', ['created_at'])


def downgrade():
    op.drop_index('ix_saved_policies_created_at', table_name='saved_policies')
    op.drop_index('ix_saved_policies_policy_id', table_name='saved_policies')
    op.drop_index('ix_saved_policies_user_id', table_name='saved_policies')
    op.drop_table('saved_policies')
