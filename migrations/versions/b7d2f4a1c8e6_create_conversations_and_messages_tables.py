"""create conversations and messages tables

Revision ID: b7d2f4a1c8e6
Revises: a4c7e2f19b3d
Create Date: 2026-09-20 00:00:00.000000

Phase 12: persistent authenticated chat history. Adds exactly two new
tables - `conversations` (User 1 -> N Conversation) and `messages`
(Conversation 1 -> N Message) - and touches nothing else. In
particular:

  - Does not alter `users`, `user_sessions`, `email_verification_tokens`,
    or `password_reset_tokens` in any way.
  - Does not alter `policy_embeddings` (Phase 10's embedding index) or
    the historical `policies` table migrations.
  - Does not duplicate policy content anywhere - `messages.metadata`
    (see backend/models.py's Message.metadata_json docstring) is for
    lightweight bookkeeping only (e.g. which policy ids informed an
    assistant reply), never a second copy of name/category/
    sub_category/change/impact. data/*.json remains the sole source of
    truth for that, unchanged by this migration.

Foreign keys:
  - conversations.user_id -> users.id
  - messages.conversation_id -> conversations.id, ON DELETE CASCADE (see
    backend/models.py's Message docstring - this is the DB-level
    backstop; backend/conversations_routes.py also deletes a
    conversation's messages explicitly before/alongside the conversation
    itself so no orphaned message can be left even on a database that,
    for whatever reason, doesn't enforce the FK - e.g. this project's
    offline test shim, which never enforces FK constraints at all; see
    tests/conftest.py's _ShimForeignKey docstring)

Indexes (per the Phase 12 brief's minimum requirements):
  - conversations.user_id
  - conversations.updated_at
  - messages.conversation_id
  - messages.created_at

Fully reversible: downgrade() drops exactly what upgrade() created, in
dependency order (messages before conversations).
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7d2f4a1c8e6'
down_revision = 'a4c7e2f19b3d'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'conversations',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=False),
        sa.Column('title', sa.String(length=200), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index('ix_conversations_user_id', 'conversations', ['user_id'])
    op.create_index('ix_conversations_updated_at', 'conversations', ['updated_at'])

    op.create_table(
        'messages',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'conversation_id', sa.Integer(),
            sa.ForeignKey('conversations.id', ondelete='CASCADE'), nullable=False,
        ),
        sa.Column('role', sa.String(length=20), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column('metadata_json', sa.Text(), nullable=True),
    )
    op.create_index('ix_messages_conversation_id', 'messages', ['conversation_id'])
    op.create_index('ix_messages_created_at', 'messages', ['created_at'])


def downgrade():
    op.drop_index('ix_messages_created_at', table_name='messages')
    op.drop_index('ix_messages_conversation_id', table_name='messages')
    op.drop_table('messages')

    op.drop_index('ix_conversations_updated_at', table_name='conversations')
    op.drop_index('ix_conversations_user_id', table_name='conversations')
    op.drop_table('conversations')
