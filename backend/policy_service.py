"""
Phase 7: database-backed policy retrieval.
Phase 8: pagination, filtering, and single-record/category lookups added
for the read-only Policy API (see backend/policies_routes.py).

Deliberately NOT wired into chatbot.py in this phase - the Phase 7 brief
is explicit: "DO NOT completely redesign retrieval... introduce a
database-backed policy loading/service layer that can coexist with the
current retrieval behavior... Phase 9 will redesign retrieval." So this
module exists in parallel to backend/policy_loader.py (JSON-backed,
unchanged, still what chatbot.py actually uses today), ready for Phase 9
to switch over.

load_policies_from_db() intentionally returns the exact same shape
policy_loader.load_policies() does - a flat list of dicts with keys name/
category/sub_category/change/impact/sector - so any code written against
the JSON loader's output already works unchanged against this, without
knowing or caring which one produced it.
"""

from models import Policy

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


def load_policies_from_db():
    """Returns every policy row as a plain dict (see Policy.to_dict()),
    in the same shape backend/policy_loader.py's load_policies()
    produces. Does not cache (unlike policy_loader.py's process-lifetime
    cache) - the database is already fast for this size of table, and an
    explicit cache would just be another place Phase 9's eventual
    retrieval redesign would need to reason about invalidating."""
    return [policy.to_dict() for policy in Policy.query.all()]


def get_policy_count():
    return len(Policy.query.all())


def get_sector_counts():
    """Returns {sector: count} for every sector currently in the
    database - useful for the import script's reporting and for tests
    that need to confirm the DB matches the source JSON without
    hardcoding expected numbers (see the Phase 7 brief's "do not hardcode
    151 as truth")."""
    counts = {}
    for policy in Policy.query.all():
        counts[policy.sector] = counts.get(policy.sector, 0) + 1
    return counts


def get_category_counts(sector=None):
    """Returns {category: count}, optionally scoped to a single sector.
    Same "compute from the live rows, never hardcode" approach as
    get_sector_counts() - used by GET /api/policies/categories."""
    query = Policy.query
    if sector is not None:
        query = query.filter_by(sector=sector)
    counts = {}
    for policy in query.all():
        counts[policy.category] = counts.get(policy.category, 0) + 1
    return counts


def _policy_to_api_dict(policy):
    """Policy.to_dict()'s shape plus `id`. to_dict() intentionally omits
    id (chatbot.py's existing code never needs it - see models.py), but
    an API response representing a specific database row should include
    its own identifier: by ordinary REST convention, and because
    GET /api/policies/<id> would otherwise be undiscoverable from a list
    response that never shows any id. This is the Phase 8 API's shape,
    not a change to to_dict() itself or to anything chatbot.py uses."""
    data = policy.to_dict()
    data["id"] = policy.id
    return data


def get_paginated_policies(page=1, per_page=DEFAULT_PER_PAGE,
                            sector=None, category=None, sub_category=None):
    """Returns {"items": [dict, ...], "total": int}. Filtering uses
    SQLAlchemy's filter_by() (parameterized under the hood - never raw
    SQL string interpolation of user input). Ordered by id for a stable,
    deterministic page-to-page sequence - without an explicit ORDER BY,
    row order across separate paginated queries is not guaranteed by
    SQL. Caller (backend/policies_routes.py) is responsible for
    validating page/per_page are sane positive integers before calling
    this - this function trusts its arguments."""
    query = Policy.query

    filters = {}
    if sector is not None:
        filters["sector"] = sector
    if category is not None:
        filters["category"] = category
    if sub_category is not None:
        filters["sub_category"] = sub_category
    if filters:
        query = query.filter_by(**filters)

    total = query.count()

    page_query = query.order_by(Policy.id).limit(per_page).offset((page - 1) * per_page)
    items = [_policy_to_api_dict(policy) for policy in page_query.all()]

    return {"items": items, "total": total}


def get_policy_by_id(policy_id):
    """Returns the policy as an API-shaped dict (see
    _policy_to_api_dict()), or None if no policy with that id exists."""
    policy = Policy.query.filter_by(id=policy_id).first()
    if policy is None:
        return None
    return _policy_to_api_dict(policy)
