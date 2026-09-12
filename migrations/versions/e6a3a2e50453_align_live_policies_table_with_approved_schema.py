"""corrective: align live policies table with approved Phase 7 schema

Revision ID: e6a3a2e50453
Revises: e9e681dc3473
Create Date: 2026-09-12 00:00:00.000000

CONTEXT (do not remove - explains why this migration exists):

The live PostgreSQL development database was migrated using a STALE,
pre-finalization copy of e9e681dc3473_create_policies_table.py - one that
predated the schema-review round where source_json -> source_file and
id INTEGER -> BIGINT were approved, and where indexes on category/
sub_category were added. e9e681dc3473 was edited in place at the time
under the (mistaken, in hindsight) assumption that it had never been
applied anywhere - it had, via a copy that was out of sync with this
sandbox. Alembic tracks applied revisions by ID only, not by content
hash, so the live database is now stamped "e9e681dc3473" despite having
been built from different column definitions than what that file
currently contains.

Per explicit instruction, e9e681dc3473 is NOT modified by this migration
(or ever again, now that it's been applied) - this is a separate,
forward-only corrective revision on top of it. Confirmed before writing
this migration that backend/models.py's Policy class already reflects
the target (approved) schema - only the live database needed correcting,
not the model.

The live `policies` table is confirmed to contain 0 rows, so every
operation below is safe with no data to lose, transform, or backfill.

Changes made (exactly the 5 requested, no others):
  1. Rename source_json -> source_file
  2. source_file: TEXT -> VARCHAR(255), and set NOT NULL
  3. id: INTEGER -> BIGINT
  4. CREATE INDEX ix_policies_category ON policies(category)
  5. CREATE INDEX ix_policies_sub_category ON policies(sub_category)

Preserved, untouched by this migration: the primary key on id,
ix_policies_name, ix_policies_sector, uq_policies_name_sector, every
other existing column, and created_at/updated_at.

KNOWN RESIDUAL LIMITATION (not fixed here, out of scope per instructions):
e9e681dc3473's own downgrade() still references ix_policies_category and
ix_policies_sub_category as if its own upgrade() had created them - it
did not (that's the entire reason this corrective migration exists). A
downgrade chain that runs THIS migration's downgrade() and then continues
on into e9e681dc3473's downgrade() will fail at that second step, because
this migration's downgrade() already removes those two indexes, and
e9e681dc3473's downgrade() will try to drop them again. Reconciling that
would require touching e9e681dc3473, which this task explicitly
prohibits. Flagged for a future decision, not addressed now.
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e6a3a2e50453'
down_revision = 'e9e681dc3473'
branch_labels = None
depends_on = None


def upgrade():
    # 1. Rename source_json -> source_file (still TEXT/nullable at this
    #    point - type and nullability are corrected in the next two
    #    steps, kept separate for clarity and safe, reversible ordering).
    op.alter_column(
        'policies', 'source_json',
        new_column_name='source_file',
    )

    # 2. source_file: TEXT -> VARCHAR(255), then NOT NULL. Safe with 0
    #    rows in the table - no existing value can violate either change.
    op.alter_column(
        'policies', 'source_file',
        existing_type=sa.Text(),
        type_=sa.String(length=255),
    )
    op.alter_column(
        'policies', 'source_file',
        existing_type=sa.String(length=255),
        nullable=False,
    )

    # 3. id: INTEGER -> BIGINT. A safe widening conversion in PostgreSQL
    #    (every INTEGER value is a valid BIGINT value) - safe regardless
    #    of row count, and doubly safe here with 0 rows. Does not touch
    #    the primary key constraint itself, which is preserved.
    op.alter_column(
        'policies', 'id',
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
    )

    # 4-5. The two indexes e9e681dc3473 was always meant to create but
    #      didn't, in the stale copy that was actually applied.
    op.create_index('ix_policies_category', 'policies', ['category'])
    op.create_index('ix_policies_sub_category', 'policies', ['sub_category'])


def downgrade():
    # Reverses only what upgrade() above did, in reverse order - does
    # not touch ix_policies_name, ix_policies_sector,
    # uq_policies_name_sector, the primary key, or any other column.
    op.drop_index('ix_policies_sub_category', table_name='policies')
    op.drop_index('ix_policies_category', table_name='policies')

    op.alter_column(
        'policies', 'id',
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
    )

    op.alter_column(
        'policies', 'source_file',
        existing_type=sa.String(length=255),
        nullable=True,
    )
    op.alter_column(
        'policies', 'source_file',
        existing_type=sa.String(length=255),
        type_=sa.Text(),
    )

    op.alter_column(
        'policies', 'source_file',
        new_column_name='source_json',
    )
