"""
data/*.json validation, historical migration-chain integrity, and the
JSON-backed policy_service surface.

ARCHITECTURE CHANGE (post-Phase-9): the PostgreSQL "policies" table, the
Policy ORM model, and import_policies.py's former import_policies_from_json()
have all been retired - see backend/policy_service.py's module docstring.
This file originally (Phase 7) covered all three; everything that
exercised them directly has been removed rather than left to bit-rot
against code that no longer exists. What survives, unchanged in
substance:
  - validate_json_files() is still exactly as relevant to a JSON-only
    pipeline as it was to a JSON-into-Postgres one (it never touched the
    database in the first place - see its own docstring) - data
    integrity of data/*.json itself didn't stop mattering.
  - The historical policies-table migrations (e9e681dc3473,
    e6a3a2e50453) still exist in migrations/versions/ unchanged (see
    the storage-migration report - rewriting/deleting historical
    migrations was explicitly out of scope) and are still checked here
    for structural integrity, even though nothing applies them to a
    fresh database's schema as part of this app's normal startup any
    more.
  - Coverage for "the JSON-backed policy_service functions return the
    right shape/counts" moved to the bottom of this file, replacing the
    old DB-backed version of that same check.

Uses the real data/*.json files throughout (no fixture needed - these
are read-only inspections).
"""

import os

from import_policies import REQUIRED_FIELDS, validate_json_files

REAL_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)


# --- historical migration chain integrity (structure only, never rewritten) ----

def _read_migration(migrations_dir, name_fragment):
    files = [f for f in os.listdir(migrations_dir) if name_fragment in f]
    assert len(files) == 1, f"expected exactly one migration matching {name_fragment!r}, found {files}"
    with open(os.path.join(migrations_dir, files[0]), encoding="utf-8") as f:
        return f.read()


def test_policies_migration_files_exist_and_chain_correctly():
    """Covers the full historical policies migration chain:
    e9e681dc3473 (original table creation) -> e6a3a2e50453 (corrective
    migration that aligns the already-applied live table with the
    approved schema). Neither file has been touched by the JSON-storage
    migration - historical migrations are never rewritten once applied,
    even after the model/table they created is retired."""
    migrations_dir = os.path.join(
        os.path.dirname(REAL_DATA_DIR), "migrations", "versions"
    )
    original_source = _read_migration(migrations_dir, "create_policies_table")
    corrective_source = _read_migration(
        migrations_dir, "align_live_policies_table_with_approved_schema"
    )

    # Chain structure - exactly as approved, no branching.
    assert "revision = 'e9e681dc3473'" in original_source
    assert "down_revision = '29385479c057'" in original_source
    assert "revision = 'e6a3a2e50453'" in corrective_source
    assert "down_revision = 'e9e681dc3473'" in corrective_source

    # Both migrations are structurally complete and reversible.
    for source in (original_source, corrective_source):
        assert "def upgrade():" in source
        assert "def downgrade():" in source

    assert "drop_table('policies')" in original_source
    assert "UniqueConstraint('name', 'sector'" in original_source

    assert "'source_json'" in corrective_source
    assert "new_column_name='source_file'" in corrective_source
    assert "sa.BigInteger()" in corrective_source
    assert "ix_policies_category" in corrective_source
    assert "ix_policies_sub_category" in corrective_source


def test_migration_chain_creates_indexes_for_name_sector_category_sub_category():
    migrations_dir = os.path.join(
        os.path.dirname(REAL_DATA_DIR), "migrations", "versions"
    )
    original_source = _read_migration(migrations_dir, "create_policies_table")
    corrective_source = _read_migration(
        migrations_dir, "align_live_policies_table_with_approved_schema"
    )
    combined_source = original_source + corrective_source

    for expected_index in (
        "ix_policies_name", "ix_policies_sector",
        "ix_policies_category", "ix_policies_sub_category",
    ):
        assert f"'{expected_index}'" in combined_source
        assert combined_source.count(expected_index) >= 2

    assert "ix_policies_category" in corrective_source
    assert "ix_policies_sub_category" in corrective_source


# --- JSON validation -------------------------------------------------------------

def test_all_real_policy_json_files_parse_without_error():
    report = validate_json_files(REAL_DATA_DIR)
    assert report["malformed_files"] == []
    assert report["missing_field_records"] == []


def test_validate_json_files_detects_malformed_json(tmp_path):
    (tmp_path / "broken.json").write_text("{ not valid json ][", encoding="utf-8")
    report = validate_json_files(str(tmp_path))
    assert report["ok"] is False
    assert len(report["malformed_files"]) == 1
    assert report["malformed_files"][0]["file"] == "broken.json"


def test_validate_json_files_detects_missing_required_fields(tmp_path):
    import json
    (tmp_path / "healthcare.json").write_text(
        json.dumps([{"name": "Only A Name"}]), encoding="utf-8"
    )
    report = validate_json_files(str(tmp_path))
    assert report["ok"] is False
    missing_fields = {entry["field"] for entry in report["missing_field_records"]}
    assert missing_fields == set(REQUIRED_FIELDS) - {"name"}


def test_validate_json_files_detects_true_name_sector_duplicates(tmp_path):
    import json
    record = {
        "name": "Same Policy Twice",
        "category": "Cat", "sub_category": "Sub",
        "change": "x", "impact": "y",
    }
    (tmp_path / "healthcare.json").write_text(json.dumps([record, record]), encoding="utf-8")

    report = validate_json_files(str(tmp_path))
    assert report["ok"] is False
    assert report["true_duplicates"] == [{"name": "Same Policy Twice", "sector": "healthcare"}]


def test_validate_json_files_treats_shared_names_across_sectors_as_informational_only(tmp_path):
    """The real dataset's 8 shared-name-across-sectors cases (see
    test_real_dataset_has_exactly_the_known_shared_names() below) must
    NOT be flagged as errors."""
    import json
    record = {
        "name": "Shared Policy Name",
        "category": "Cat", "sub_category": "Sub",
        "change": "x", "impact": "y",
    }
    (tmp_path / "economy.json").write_text(json.dumps([record]), encoding="utf-8")
    (tmp_path / "industry_business.json").write_text(json.dumps([record]), encoding="utf-8")

    report = validate_json_files(str(tmp_path))
    assert report["ok"] is True
    assert report["true_duplicates"] == []
    assert report["shared_names_across_sectors"] == {
        "Shared Policy Name": ["economy", "industry_business"]
    }


# --- policy count, sector count (computed, never hardcoded) ----------------------

def test_real_policy_count_matches_source_files():
    import json as jsonlib
    report = validate_json_files(REAL_DATA_DIR)
    expected_total = 0
    for fname in os.listdir(REAL_DATA_DIR):
        if fname.endswith(".json"):
            with open(os.path.join(REAL_DATA_DIR, fname), encoding="utf-8") as f:
                expected_total += len(jsonlib.load(f))
    assert report["total_records"] == expected_total
    assert report["total_records"] == 151


def test_real_sector_count_matches_number_of_json_files():
    report = validate_json_files(REAL_DATA_DIR)
    expected_sectors = {
        f[:-len(".json")] for f in os.listdir(REAL_DATA_DIR) if f.endswith(".json")
    }
    assert set(report["sectors"].keys()) == expected_sectors
    assert len(report["sectors"]) == 15


# --- duplicate detection on the real dataset --------------------------------------

def test_real_dataset_has_no_true_name_sector_duplicates():
    report = validate_json_files(REAL_DATA_DIR)
    assert report["true_duplicates"] == []


def test_real_dataset_has_exactly_the_known_shared_names():
    report = validate_json_files(REAL_DATA_DIR)
    assert len(report["shared_names_across_sectors"]) == 8


# --- JSON-backed policy_service surface (replaces the retired DB-backed check) --

def test_policy_service_load_all_policies_returns_json_loader_compatible_shape():
    from policy_service import get_policy_count, get_sector_counts, load_all_policies

    policies = load_all_policies()
    assert len(policies) == 151
    sample = policies[0]
    assert {"name", "category", "sub_category", "change", "impact", "sector", "id"} <= set(sample.keys())

    assert get_policy_count() == 151
    sector_counts = get_sector_counts()
    assert len(sector_counts) == 15
    assert sum(sector_counts.values()) == 151


def test_policy_ids_are_stable_deterministic_integers_not_hash_based():
    """IDs must never be Python's hash() (salted per-process by default -
    see backend/policy_loader.py's module docstring) - confirmed here by
    checking they're the plain, predictable 1..N sequence the loader's
    sorted-filename + JSON-array-order assignment produces, not some
    large/negative/non-reproducible hash-shaped number."""
    from policy_service import load_all_policies

    ids = [p["id"] for p in load_all_policies()]
    assert ids == list(range(1, len(ids) + 1))
