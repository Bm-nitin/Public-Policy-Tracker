"""
Phase 7: database-backed policy retrieval.

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
