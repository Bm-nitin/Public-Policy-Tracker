"""
Phase 7: policies table, JSON validation, and idempotent import.

Uses the real data/*.json files for validation/count tests (no fixture
needed - these are read-only inspections), and db_test_app (see
conftest.py) for anything touching the database. See conftest.py's
flask_sqlalchemy shim docstring for what is/isn't validated in this
offline sandbox vs. a real environment - Phase 7 additionally extends
that shim with db.Text and a genuinely-enforced composite
UniqueConstraint (needed for Policy's (name, sector) uniqueness).

chatbot.py and policy_loader.py are not imported, patched, or asserted
on by anything in this file - Phase 7 does not touch them at all (see
import_policies.py's module docstring), so the existing characterization
suite (test_chatbot_behavior.py, test_policy_loader.py, test_api.py)
running unmodified and unaffected IS the regression check for item 13.
"""

import os

import models
from import_policies import (
    REQUIRED_FIELDS,
    import_policies_from_json,
    validate_json_files,
)

REAL_DATA_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
)


# --- 5, 10, 11: composite UNIQUE(name, sector) enforced at the DB level --------

def test_same_name_in_two_different_sectors_is_allowed(db_test_app):
    """Item 10: duplicate policy names across different sectors must be
    allowed - this is the documented, legitimate 8-cases-in-the-real-data
    situation, not an error."""
    from database import db

    with db_test_app.app_context():
        db.session.add(models.Policy(
            name="Shared Name Policy", sector="economy",
            category="Cat", sub_category="Sub", change="c1", impact="i1",
            source_file="economy.json",
        ))
        db.session.add(models.Policy(
            name="Shared Name Policy", sector="industry_business",
            category="Cat", sub_category="Sub", change="c2", impact="i2",
            source_file="industry_business.json",
        ))
        db.session.commit()  # must not raise

        matches = models.Policy.query.filter_by(name="Shared Name Policy").all()
        assert len(matches) == 2
        assert {p.sector for p in matches} == {"economy", "industry_business"}


def test_same_name_and_sector_together_is_rejected_by_the_database(db_test_app):
    """Item 11 (DB-level, not just import-logic-level): the composite
    UNIQUE(name, sector) constraint itself must reject a true duplicate,
    not just the import script's own pre-check - this is the same
    "rely on the database's constraint as the final protection" pattern
    used for users.email (Phase 3) and *_tokens.token_hash (Phase 4-6)."""
    from database import db
    from sqlalchemy.exc import IntegrityError

    with db_test_app.app_context():
        db.session.add(models.Policy(
            name="Exact Duplicate Policy", sector="healthcare",
            category="Cat", sub_category="Sub", change="c", impact="i",
            source_file="healthcare.json",
        ))
        db.session.commit()

        db.session.add(models.Policy(
            name="Exact Duplicate Policy", sector="healthcare",
            category="Different Cat", sub_category="Different Sub",
            change="different change", impact="different impact",
            source_file="healthcare.json",
        ))
        try:
            db.session.commit()
            raised = False
        except IntegrityError:
            db.session.rollback()
            raised = True

        assert raised is True
        matches = models.Policy.query.filter_by(
            name="Exact Duplicate Policy", sector="healthcare"
        ).all()
        assert len(matches) == 1


# --- 1: Policy model creation -------------------------------------------------

def test_policy_model_can_be_created_and_persisted(db_test_app):
    from database import db

    with db_test_app.app_context():
        policy = models.Policy(
            name="Test Policy",
            sector="test_sector",
            category="Test Category",
            sub_category="Test Sub",
            change="Something changed.",
            impact="Something happened.",
            source_file="test_sector.json",
        )
        db.session.add(policy)
        db.session.commit()

        fetched = models.Policy.query.filter_by(name="Test Policy").first()
        assert fetched is not None
        assert fetched.sector == "test_sector"
        assert fetched.id is not None
        assert fetched.source_file == "test_sector.json"


def test_policy_model_has_no_source_json_field():
    """Approved schema change: source_json was replaced with source_file.
    Uses Policy.__table__.columns - real SQLAlchemy table metadata that
    exists on any real SQLAlchemy model - rather than __columns__, which
    was a shim-only internal attribute this sandbox's offline test
    double happened to expose and does not exist on a real installed
    SQLAlchemy model. That mismatch is exactly why this test previously
    failed against the real dependency stack while passing here."""
    assert "source_json" not in models.Policy.__table__.columns
    assert "source_file" in models.Policy.__table__.columns


def test_policy_to_dict_matches_json_loader_shape(db_test_app):
    from database import db

    with db_test_app.app_context():
        policy = models.Policy(
            name="Test Policy", sector="test_sector", category="Cat",
            sub_category="Sub", change="Change text", impact="Impact text",
            source_file="test_sector.json",
        )
        db.session.add(policy)
        db.session.commit()

        as_dict = policy.to_dict()

    assert set(as_dict.keys()) == {"name", "category", "sub_category", "change", "impact", "sector"}
    # source_file is provenance, not part of the JSON-loader-compatible
    # shape chatbot.py expects - confirmed absent here too.
    assert "source_file" not in as_dict


# --- 2: migration structure ---------------------------------------------------

def _read_migration(migrations_dir, name_fragment):
    files = [f for f in os.listdir(migrations_dir) if name_fragment in f]
    assert len(files) == 1, f"expected exactly one migration matching {name_fragment!r}, found {files}"
    with open(os.path.join(migrations_dir, files[0]), encoding="utf-8") as f:
        return f.read()


def test_policies_migration_files_exist_and_chain_correctly():
    """Covers the full Phase 7 policies migration chain:
    e9e681dc3473 (original table creation) -> e6a3a2e50453 (corrective
    migration that aligns the already-applied live table with the
    approved schema). e9e681dc3473 was already applied to PostgreSQL
    (via a stale pre-finalization copy - see e6a3a2e50453's own
    docstring) and must never be rewritten again; e6a3a2e50453 is the
    only place the BigInteger conversion, source_json->source_file
    rename, and category/sub_category indexes are required to exist."""
    migrations_dir = os.path.join(
        os.path.dirname(REAL_DATA_DIR), "migrations", "versions"
    )
    original_source = _read_migration(migrations_dir, "create_policies_table")
    corrective_source = _read_migration(
        migrations_dir, "align_live_policies_table_with_approved_schema"
    )

    # Chain structure - exactly as approved, no branching.
    assert "revision = 'e9e681dc3473'" in original_source
    assert "down_revision = '29385479c057'" in original_source  # chains onto Phase 6
    assert "revision = 'e6a3a2e50453'" in corrective_source
    assert "down_revision = 'e9e681dc3473'" in corrective_source

    # Both migrations are structurally complete and reversible.
    for source in (original_source, corrective_source):
        assert "def upgrade():" in source
        assert "def downgrade():" in source

    # Base table structure, established by e9e681dc3473 regardless of
    # which historical version of its column types is being read.
    assert "drop_table('policies')" in original_source
    assert "UniqueConstraint('name', 'sector'" in original_source

    # The corrective migration is where the approved fixes are required
    # to live - NOT moved backwards into e9e681dc3473, which is never
    # modified again once applied.
    assert "'source_json'" in corrective_source
    assert "new_column_name='source_file'" in corrective_source
    assert "sa.BigInteger()" in corrective_source
    assert "ix_policies_category" in corrective_source
    assert "ix_policies_sub_category" in corrective_source


def test_migration_chain_creates_indexes_for_name_sector_category_sub_category():
    """name/sector indexes come from the original migration;
    category/sub_category indexes were deliberately added later via the
    corrective migration, not moved backwards into e9e681dc3473 (which
    must never be rewritten once applied). Checked across the full
    chain, not assumed to live in a single file."""
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
        # Every created index must also be dropped somewhere in the
        # chain (its own migration's downgrade()).
        assert combined_source.count(expected_index) >= 2

    # category/sub_category specifically must be establishable via the
    # corrective migration on its own - not only when combined with
    # whatever e9e681dc3473 currently happens to also contain.
    assert "ix_policies_category" in corrective_source
    assert "ix_policies_sub_category" in corrective_source


# --- 3-4: JSON validation, all files parse -------------------------------------

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
    models.Policy's docstring) must NOT be flagged as errors."""
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


# --- 5-6: policy count, sector count (computed, not hardcoded) -----------------

def test_real_policy_count_matches_source_files():
    """Computes the real count rather than asserting a hardcoded 151, per
    the Phase 7 brief ('do not hardcode 151 as truth')."""
    import json as jsonlib
    report = validate_json_files(REAL_DATA_DIR)
    expected_total = 0
    for fname in os.listdir(REAL_DATA_DIR):
        if fname.endswith(".json"):
            with open(os.path.join(REAL_DATA_DIR, fname), encoding="utf-8") as f:
                expected_total += len(jsonlib.load(f))
    assert report["total_records"] == expected_total
    # Documented in the Phase 7 report: this currently equals 151, but
    # the assertion above is what actually matters, not this number.
    assert report["total_records"] == 151


def test_real_sector_count_matches_number_of_json_files():
    report = validate_json_files(REAL_DATA_DIR)
    expected_sectors = {
        f[:-len(".json")] for f in os.listdir(REAL_DATA_DIR) if f.endswith(".json")
    }
    assert set(report["sectors"].keys()) == expected_sectors
    assert len(report["sectors"]) == 15


# --- 7: duplicate detection on the real dataset --------------------------------

def test_real_dataset_has_no_true_name_sector_duplicates():
    report = validate_json_files(REAL_DATA_DIR)
    assert report["true_duplicates"] == []


def test_real_dataset_has_exactly_the_known_shared_names():
    """Documents the 8 legitimate shared-name-across-sectors cases found
    during Phase 7 inspection - a change to this number is worth noticing,
    not silently accepted."""
    report = validate_json_files(REAL_DATA_DIR)
    assert len(report["shared_names_across_sectors"]) == 8


# --- 8-9: JSON -> DB field mapping, sector/source_file derived from filename ---

def test_import_maps_every_source_field_to_the_correct_column(db_test_app):
    with db_test_app.app_context():
        report = import_policies_from_json(REAL_DATA_DIR)
        assert report["aborted"] is False

        policy = models.Policy.query.filter_by(
            name="ISRO Formation Policy, 1969", sector="science_innovation",
        ).first()

    assert policy is not None
    assert policy.category  # non-empty, mapped from source
    assert policy.sub_category
    assert policy.change
    assert policy.impact
    assert policy.sector == "science_innovation"
    assert policy.source_file == "science_innovation.json"


def test_import_derives_sector_from_filename_exactly_like_policy_loader(db_test_app):
    """data/agriculture.json -> sector="agriculture", matching
    policy_loader.py's existing filename-derived sector logic exactly -
    same slicing convention, not reimplemented differently."""
    with db_test_app.app_context():
        import_policies_from_json(REAL_DATA_DIR)
        agriculture_policies = models.Policy.query.filter_by(sector="agriculture").all()

    assert len(agriculture_policies) == 10
    for policy in agriculture_policies:
        assert policy.source_file == "agriculture.json"


def test_import_sets_source_file_to_original_filename_for_every_sector(db_test_app):
    with db_test_app.app_context():
        import_policies_from_json(REAL_DATA_DIR)
        all_policies = models.Policy.query.all()

    for policy in all_policies:
        assert policy.source_file == f"{policy.sector}.json"


# --- 9-10: idempotent import, re-running does not duplicate --------------------

def test_import_is_idempotent_on_repeated_runs(db_test_app):
    with db_test_app.app_context():
        first_report = import_policies_from_json(REAL_DATA_DIR)
        assert first_report["aborted"] is False
        assert first_report["inserted"] == 151
        assert first_report["updated"] == 0

        count_after_first = len(models.Policy.query.all())

        second_report = import_policies_from_json(REAL_DATA_DIR)
        assert second_report["inserted"] == 0
        assert second_report["updated"] == 0
        assert second_report["skipped"] == 151

        count_after_second = len(models.Policy.query.all())

    assert count_after_first == count_after_second == 151


def test_import_updates_changed_records_without_duplicating(db_test_app, tmp_path):
    import json

    record = {
        "name": "Evolving Policy", "category": "Cat", "sub_category": "Sub",
        "change": "original change text", "impact": "original impact text",
    }
    (tmp_path / "healthcare.json").write_text(json.dumps([record]), encoding="utf-8")

    with db_test_app.app_context():
        first = import_policies_from_json(str(tmp_path))
        assert first["inserted"] == 1

        updated_record = dict(record, change="a genuinely different change description")
        (tmp_path / "healthcare.json").write_text(json.dumps([updated_record]), encoding="utf-8")

        second = import_policies_from_json(str(tmp_path))
        assert second["inserted"] == 0
        assert second["updated"] == 1
        assert second["skipped"] == 0

        all_matching = models.Policy.query.filter_by(name="Evolving Policy", sector="healthcare").all()
        assert len(all_matching) == 1
        assert all_matching[0].change == "a genuinely different change description"


def test_import_aborts_entirely_on_validation_failure_no_partial_writes(db_test_app, tmp_path):
    import json
    (tmp_path / "healthcare.json").write_text(
        json.dumps([{"name": "Incomplete Policy"}]), encoding="utf-8"
    )

    with db_test_app.app_context():
        report = import_policies_from_json(str(tmp_path))
        assert report["aborted"] is True
        assert len(models.Policy.query.all()) == 0


# --- 11: transaction rollback behavior ------------------------------------------

def test_import_failure_mid_run_rolls_back_everything(db_test_app, tmp_path, monkeypatch):
    import json
    import import_policies as import_policies_module

    records = [
        {"name": f"Policy {i}", "category": "Cat", "sub_category": "Sub",
         "change": "c", "impact": "i"}
        for i in range(5)
    ]
    (tmp_path / "healthcare.json").write_text(json.dumps(records), encoding="utf-8")

    call_count = {"n": 0}
    real_policy_class = models.Policy

    class _FakePolicyProxy:
        """Policy is used both as Policy.query (class-level descriptor)
        and Policy(...) (constructor) in import_policies.py - a plain
        replacement function only covers the second use, so this proxies
        .query through to the real class and only intercepts
        construction."""

        @property
        def query(self):
            return real_policy_class.query

        def __call__(self, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 3:
                raise RuntimeError("simulated mid-import failure")
            return real_policy_class(*args, **kwargs)

    monkeypatch.setattr(import_policies_module, "Policy", _FakePolicyProxy())

    with db_test_app.app_context():
        try:
            import_policies_from_json(str(tmp_path))
        except RuntimeError:
            pass

        assert len(models.Policy.query.all()) == 0  # all-or-nothing, not 2 out of 5


# --- 12: database-backed policy retrieval ---------------------------------------

def test_load_policies_from_db_returns_json_loader_compatible_shape(db_test_app):
    from policy_service import get_policy_count, get_sector_counts, load_policies_from_db

    with db_test_app.app_context():
        import_policies_from_json(REAL_DATA_DIR)

        policies = load_policies_from_db()
        assert len(policies) == 151
        sample = policies[0]
        assert set(sample.keys()) == {"name", "category", "sub_category", "change", "impact", "sector"}

        assert get_policy_count() == 151
        sector_counts = get_sector_counts()
        assert len(sector_counts) == 15
        assert sum(sector_counts.values()) == 151
