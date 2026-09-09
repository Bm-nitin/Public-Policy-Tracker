"""
Area 3: Policy loading (backend/policy_loader.py)

Covers real-data loading plus edge cases (malformed JSON, missing data
directory, caching) using temporary directories so we never touch the real
data/ folder.
"""

import json
import os

import pytest


# ---------------------------------------------------------------------------
# Real data
# ---------------------------------------------------------------------------

def test_load_policies_returns_a_list(policy_loader_module):
    policies = policy_loader_module.load_policies()
    assert isinstance(policies, list)
    assert len(policies) > 0


def test_load_policies_loads_all_15_sector_files(policy_loader_module, real_data_dir):
    policies = policy_loader_module.load_policies()
    expected_sectors = {
        f.replace(".json", "")
        for f in os.listdir(real_data_dir)
        if f.endswith(".json")
    }
    found_sectors = {p["sector"] for p in policies}
    assert found_sectors == expected_sectors
    assert len(expected_sectors) == 15


def test_load_policies_assigns_sector_from_filename(policy_loader_module):
    policies = policy_loader_module.load_policies()
    for policy in policies:
        assert policy["sector"] != ""
        assert isinstance(policy["sector"], str)


def test_load_policies_every_record_has_required_default_fields(policy_loader_module):
    required_fields = {"name", "category", "sub_category", "change", "impact", "sector"}
    policies = policy_loader_module.load_policies()
    for policy in policies:
        assert required_fields.issubset(policy.keys())


def test_load_policies_total_count_matches_sum_of_files(policy_loader_module, real_data_dir):
    expected_total = 0
    for filename in os.listdir(real_data_dir):
        if filename.endswith(".json"):
            with open(os.path.join(real_data_dir, filename), encoding="utf-8") as f:
                expected_total += len(json.load(f))

    policies = policy_loader_module.load_policies()
    assert len(policies) == expected_total


# ---------------------------------------------------------------------------
# Edge cases (temp dirs; reset_policy_cache keeps the real cache intact
# for tests that run after these)
# ---------------------------------------------------------------------------

def _point_module_at_fake_backend_dir(monkeypatch, module, tmp_path):
    """policy_loader.load_policies() derives data_dir from
    os.path.dirname(os.path.abspath(__file__)) + '..' + 'data'. We
    monkeypatch the module's __file__ to a fake backend/policy_loader.py
    path inside tmp_path, so it looks for tmp_path/data instead of the
    real data/ directory - no production code is touched."""
    fake_backend_dir = tmp_path / "backend"
    fake_backend_dir.mkdir()
    monkeypatch.setattr(module, "__file__", str(fake_backend_dir / "policy_loader.py"))


def test_malformed_json_file_is_skipped_not_crashed(
    policy_loader_module, monkeypatch, tmp_path, reset_policy_cache
):
    _point_module_at_fake_backend_dir(monkeypatch, policy_loader_module, tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    (data_dir / "valid.json").write_text(
        json.dumps([{"name": "Valid Policy"}]), encoding="utf-8"
    )
    (data_dir / "broken.json").write_text("{ this is not valid json ][", encoding="utf-8")

    policies = policy_loader_module.load_policies()

    assert len(policies) == 1
    assert policies[0]["name"] == "Valid Policy"
    assert policies[0]["sector"] == "valid"


def test_non_list_json_content_is_skipped(
    policy_loader_module, monkeypatch, tmp_path, reset_policy_cache
):
    _point_module_at_fake_backend_dir(monkeypatch, policy_loader_module, tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    # A JSON file whose top-level value is an object, not a list, is
    # currently skipped by `if not isinstance(data, list): continue`.
    (data_dir / "object_shaped.json").write_text(
        json.dumps({"not": "a list"}), encoding="utf-8"
    )
    (data_dir / "valid.json").write_text(
        json.dumps([{"name": "Valid Policy"}]), encoding="utf-8"
    )

    policies = policy_loader_module.load_policies()
    assert len(policies) == 1
    assert policies[0]["name"] == "Valid Policy"


def test_missing_data_directory_returns_empty_list(
    policy_loader_module, monkeypatch, tmp_path, reset_policy_cache
):
    _point_module_at_fake_backend_dir(monkeypatch, policy_loader_module, tmp_path)
    # Deliberately do NOT create tmp_path / "data"
    policies = policy_loader_module.load_policies()
    assert policies == []


def test_missing_default_fields_are_filled_in(
    policy_loader_module, monkeypatch, tmp_path, reset_policy_cache
):
    _point_module_at_fake_backend_dir(monkeypatch, policy_loader_module, tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()

    (data_dir / "sparse.json").write_text(
        json.dumps([{}]), encoding="utf-8"  # a policy record with no fields at all
    )

    policies = policy_loader_module.load_policies()
    assert len(policies) == 1
    policy = policies[0]
    assert policy["name"] == "Unknown Policy"
    assert policy["category"] == "Unknown"
    assert policy["sub_category"] == "General"
    assert policy["change"] == "No change info"
    assert policy["impact"] == "No impact info"
    assert policy["sector"] == "sparse"


def test_load_policies_caches_after_first_call(
    policy_loader_module, monkeypatch, tmp_path, reset_policy_cache
):
    """Characterizes current (non-invalidating) cache behavior: a second
    call returns the exact same list object, and changes made to the data
    directory on disk after the first call are NOT picked up without a
    process restart / manual cache reset. This is documented as-is, not
    fixed, per Phase 0.5 scope."""
    _point_module_at_fake_backend_dir(monkeypatch, policy_loader_module, tmp_path)
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "sector_a.json").write_text(
        json.dumps([{"name": "First Policy"}]), encoding="utf-8"
    )

    first_call = policy_loader_module.load_policies()
    assert len(first_call) == 1

    # Add a second file after the first load - should NOT be picked up.
    (data_dir / "sector_b.json").write_text(
        json.dumps([{"name": "Second Policy"}]), encoding="utf-8"
    )
    second_call = policy_loader_module.load_policies()

    assert second_call is first_call  # same cached object
    assert len(second_call) == 1  # still 1, new file ignored
