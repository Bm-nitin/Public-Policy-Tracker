"""create policies table

Revision ID: e9e681dc3473
Revises: 29385479c057
Create Date: 2026-09-11 00:00:00.000000

Phase 7: adds the policies table (schema only - no data). No other tables
are touched, and Phase 6's password_reset_tokens migration is left
unmodified - this only extends the chain (down_revision points at it).

Field choices are based on the actual data/*.json structure (all 151
current records have exactly: name, category, sub_category, change,
impact) - see backend/models.py's Policy docstring for the full
reasoning, including why fields suggested elsewhere (description,
eligibility, benefits, etc.) are NOT columns here.

(name, sector) is a composite UNIQUE constraint, not name alone - 8 real
policy names legitimately appear under two different sectors with
distinct content (verified by inspecting the source data), so name alone
is not a valid uniqueness key.

Data import (loading data/*.json into this table) is a separate, explicit
step - see backend/import_policies.py - not part of this schema
migration, per the Phase 7 brief ("do not put 151 policy INSERT
statements directly into an Alembic migration").

Fully reversible: downgrade() drops exactly what upgrade() created.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e9e681dc3473'
down_revision = '29385479c057'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'policies',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('sector', sa.String(length=64), nullable=False),
        sa.Column('category', sa.String(length=120), nullable=False),
        sa.Column('sub_category', sa.String(length=120), nullable=False),
        sa.Column('change', sa.Text(), nullable=False),
        sa.Column('impact', sa.Text(), nullable=False),
        sa.Column('source_json', sa.Text(), nullable=True),
        sa.Column(
            'created_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            'updated_at', sa.DateTime(timezone=True), nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint('name', 'sector', name='uq_policies_name_sector'),
    )
    op.create_index('ix_policies_name', 'policies', ['name'])
    op.create_index('ix_policies_sector', 'policies', ['sector'])


def downgrade():
    op.drop_index('ix_policies_sector', table_name='policies')
    op.drop_index('ix_policies_name', table_name='policies')
    op.drop_table('policies')
