"""
Policy data service - JSON-backed.

ARCHITECTURE CHANGE (post-Phase-9): policy data no longer lives in
PostgreSQL at all. backend/models.py's Policy ORM model, the
`policies` table, and backend/import_policies.py's PostgreSQL importer
have all been retired - data/*.json (via backend/policy_loader.py,
which now also assigns each policy a stable integer id - see its module
docstring) is the one and only source of truth for policy data, for
every consumer: the legacy JSON `GET /policies` route in app.py, the
`GET /api/policies*` blueprint (backend/policies_routes.py, via this
module), and backend/retrieval.py.

PostgreSQL is unchanged for everything else: users, email verification
tokens, sessions, and password reset tokens still live there exactly as
before (see backend/models.py) - this migration touches ONLY the
policy-data storage layer, nothing authentication-related.

This module keeps the exact same public function names/signatures
backend/policies_routes.py already imports (get_paginated_policies,
get_policy_by_id, get_sector_counts, get_category_counts) so the
Phase 8 API's routing code needed zero changes for this migration - only
what these functions do internally changed (in-memory list operations
over backend/policy_loader.py's cached data instead of SQLAlchemy
queries against a Postgres table). Every function's return shape is
byte-for-byte identical to before.
"""

from policy_loader import get_policy_by_id as _get_policy_by_id_from_loader
from policy_loader import load_policies

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


def load_all_policies():
    """Every policy as a plain dict (name/category/sub_category/change/
    impact/sector/id) - policy_loader.load_policies()'s own shape,
    unmodified. No separate cache here (policy_loader.py already caches
    for the process lifetime) and no database - this always just returns
    the in-memory list."""
    return load_policies()


def get_policy_count():
    return len(load_policies())


def get_sector_counts():
    """Returns {sector: count} for every sector currently loaded -
    computed from the live in-memory data, never hardcoded (e.g. never
    assumes 151/15 - see backend/import_policies.py's validate_json_files()
    docstring for why that mattered even in the old DB-backed pipeline,
    and it matters just as much here)."""
    counts = {}
    for policy in load_policies():
        counts[policy["sector"]] = counts.get(policy["sector"], 0) + 1
    return counts


def get_category_counts(sector=None):
    """Returns {category: count}, optionally scoped to a single sector.
    Same "compute from the live data, never hardcode" approach as
    get_sector_counts() - used by GET /api/policies/categories."""
    counts = {}
    for policy in load_policies():
        if sector is not None and policy["sector"] != sector:
            continue
        counts[policy["category"]] = counts.get(policy["category"], 0) + 1
    return counts


def _policy_to_api_dict(policy):
    """The policy dict as-is - policy_loader.py's dicts already include
    `id` (see its module docstring) and never included source_file/
    timestamps in the first place (those were Postgres-row-only fields
    that no longer exist anywhere), so, unlike the old DB-backed version
    of this function, there is nothing left to add or strip here. Kept
    as its own function (rather than inlining `policy`) so
    get_paginated_policies()/get_policy_by_id() have one obvious place
    to change the API's per-policy shape again in the future without
    hunting through every caller."""
    return policy


def get_paginated_policies(page=1, per_page=DEFAULT_PER_PAGE,
                            sector=None, category=None, sub_category=None):
    """Returns {"items": [dict, ...], "total": int}. Filters the
    in-memory list directly (no SQL, no parameterization concerns - it's
    a plain Python equality check against literal dict values).
    Iterates load_policies() in its already-deterministic id order (see
    policy_loader.py), so pagination is stable page-to-page without
    needing an explicit sort step here. Caller (backend/policies_routes.py)
    is responsible for validating page/per_page are sane positive
    integers before calling this - this function trusts its arguments."""
    matching = [
        policy for policy in load_policies()
        if (sector is None or policy["sector"] == sector)
        and (category is None or policy["category"] == category)
        and (sub_category is None or policy["sub_category"] == sub_category)
    ]

    total = len(matching)
    start = (page - 1) * per_page
    end = start + per_page
    items = [_policy_to_api_dict(policy) for policy in matching[start:end]]

    return {"items": items, "total": total}


def get_policy_by_id(policy_id):
    """Returns the policy as an API-shaped dict (see
    _policy_to_api_dict()), or None if no policy with that id exists."""
    policy = _get_policy_by_id_from_loader(policy_id)
    if policy is None:
        return None
    return _policy_to_api_dict(policy)
