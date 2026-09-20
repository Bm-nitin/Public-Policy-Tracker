"""create policy_embeddings table

Revision ID: a4c7e2f19b3d
Revises: e6a3a2e50453
Create Date: 2026-09-19 00:00:00.000000

Phase 10: adds a policy_embeddings table as a vector SEARCH INDEX for
semantic retrieval - NOT a second copy of policy data. data/*.json (via
backend/policy_loader.py) remains the sole source of truth for policy
content; this table stores only an embedding vector and bookkeeping
(policy_id, content_hash, model, timestamps) per policy - see
backend/models.py's PolicyEmbedding docstring for the full reasoning.

Deliberately isolated from every table any other migration in this chain
has created: no FK to `users`/`user_sessions`/`email_verification_tokens`/
`password_reset_tokens`, and no FK to the historical `policies` table
either (policy_id references policy_loader.py's stable JSON-derived id,
not a database row - there is no `policies` table left to reference; see
e6a3a2e50453 and backend/models.py's retirement note for that table).
This migration does not touch, alter, or depend on any of those tables
in any way.

`embedding` is stored as a plain TEXT column (JSON-encoded float list),
not a pgvector `vector` column - see backend/embeddings.py's module
docstring for why: this schema must not assume the pgvector extension is
available on every PostgreSQL instance/plan this app might run against
(see the Phase 10 design report's Render/pgvector compatibility
finding), and a Python-side brute-force cosine-similarity scan is
already fast enough at this dataset's size (151 rows) that no vector
index is needed yet. Migrating this one column to pgvector's `vector`
type later, if ever needed, is a schema change to this table alone.

Fully reversible: downgrade() drops exactly what upgrade() created.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a4c7e2f19b3d'
down_revision = 'e6a3a2e50453'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'policy_embeddings',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('policy_id', sa.Integer(), nullable=False),
        sa.Column('embedding', sa.Text(), nullable=False),
        sa.Column('embedding_dim', sa.Integer(), nullable=False),
        sa.Column('content_hash', sa.String(length=64), nullable=False),
        sa.Column('model', sa.String(length=120), nullable=False),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint('policy_id', name='uq_policy_embeddings_policy_id'),
    )
    op.create_index('ix_policy_embeddings_policy_id', 'policy_embeddings', ['policy_id'])


def downgrade():
    op.drop_index('ix_policy_embeddings_policy_id', table_name='policy_embeddings')
    op.drop_table('policy_embeddings')
