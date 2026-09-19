"""
JSON is the single source of truth for policy data (data/*.json) - there
is no PostgreSQL "policies" table any more (see backend/models.py and
backend/policy_service.py's module docstrings). This loader is now used
by BOTH the legacy JSON-backed `GET /policies` route in app.py AND, via
backend/policy_service.py, the `GET /api/policies*` blueprint and
backend/retrieval.py - one dataset, one loader, cached once per process.

Stable IDs (added here, not present in the source JSON itself): each
policy dict now also carries an integer "id". It is assigned once, the
first time load_policies() actually reads from disk, by walking
`sorted(os.listdir(data_dir))` (alphabetical by filename - NOT whatever
order the OS/filesystem happens to hand back from a bare os.listdir(),
which is unspecified and can differ between machines/deployments) and,
within each file, the JSON array's own order, numbering sequentially
from 1. This is the exact same ordering backend/import_policies.py's
now-retired PostgreSQL importer used when it assigned autoincrement
primary keys, so these ids line up with whatever ids a previously
Postgres-backed deployment already handed out to the frontend/bookmarks/
logs. It is deliberately NOT Python's hash() (explicitly disallowed -
hash() of a str is salted per-process by default (PYTHONHASHSEED), so
the "same" policy would get a different id on every restart) and NOT
derived from dict insertion/memory order (which is what plain os.listdir()
would non-deterministically produce). IDs are only as stable as
data/*.json itself: adding, removing, or reordering records changes
which id lands on which policy the next time the process restarts and
reloads - there is no persisted id-assignment table anywhere (there is
nowhere left to persist one, by design - see the architecture note in
CURRENT_ARCHITECTURE.md/the Phase 10-prep brief). This matches the
Phase 7 import pipeline's own behavior already (re-running the importer
against reordered JSON could likewise change which row got which
autoincrement id) - not a new limitation introduced here.
"""

import json
import os

_cached_policies = None


def load_policies():
    global _cached_policies

    if _cached_policies is not None:
        return _cached_policies

    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.normpath(os.path.join(base_dir, '..', 'data'))

        if not os.path.exists(data_dir):
            print("Data folder not found:", data_dir)
            return []

        all_policies = []
        next_id = 1

        # sorted(): see module docstring - required for deterministic,
        # stable-across-restarts id assignment. Do not change this back
        # to a bare os.listdir().
        for filename in sorted(os.listdir(data_dir)):
            if filename.endswith('.json'):
                file_path = os.path.join(data_dir, filename)

                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                except Exception as e:
                    print(f"Error reading {filename}: {e}")
                    continue

                if not isinstance(data, list):
                    continue

                # 🔥 IMPORTANT: assign sector from filename
                sector_name = filename.replace(".json", "").lower()

                for policy in data:
                    policy.setdefault('name', 'Unknown Policy')
                    policy.setdefault('category', 'Unknown')
                    policy.setdefault('sub_category', 'General')
                    policy.setdefault('change', 'No change info')
                    policy.setdefault('impact', 'No impact info')

                    policy["sector"] = sector_name   # ✅ KEY FIX
                    policy["id"] = next_id
                    next_id += 1

                all_policies.extend(data)

        print(f"Loaded {len(all_policies)} policies")

        _cached_policies = all_policies
        return all_policies

    except Exception as e:
        print("Error loading policies:", str(e))
        return []


def get_policy_by_id(policy_id):
    """Returns the policy dict with this id, or None. O(n) linear scan
    over the (process-lifetime-cached) in-memory list - fine at 151
    records; would be worth an id->policy dict if this dataset ever grew
    large enough for that to matter (it doesn't today)."""
    for policy in load_policies():
        if policy["id"] == policy_id:
            return policy
    return None