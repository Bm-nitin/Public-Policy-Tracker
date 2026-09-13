"""
Phase 8: read-only, database-backed Policy API.

Separate from auth_routes.py (which owns /api/auth/*) - this blueprint
owns /api/policies/* and is deliberately read-only (GET only; no create/
update/delete routes - the dataset is curated via data/*.json and loaded
through backend/import_policies.py, not through this API).

Does not touch the existing JSON-backed GET /policies route in app.py at
all - that route, and chatbot.py's retrieval behind it, are completely
unchanged. This is a new, separate, database-backed surface added
alongside it, not a replacement (that's Phase 9's job, explicitly out of
scope here).

Every response is JSON, including error responses - no route here ever
lets Flask's default HTML error page leak through, and no response ever
includes a SQL statement, credential, stack trace, or filesystem path.
"""

from flask import Blueprint, jsonify, request

from config import Config
from policy_service import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    get_category_counts,
    get_paginated_policies,
    get_policy_by_id,
    get_sector_counts,
)

policies_bp = Blueprint("policies_api", __name__, url_prefix="/api/policies")


def _database_unavailable_response():
    return jsonify({
        "error": "Policy API is temporarily unavailable: no database is configured."
    }), 503


def _parse_pagination_params():
    """Returns (page, per_page, error_response_or_None). Treats every
    query parameter as untrusted input - never assumes it's a valid int
    just because request.args.get() returned something."""
    page_raw = request.args.get("page", "1")
    per_page_raw = request.args.get("per_page", str(DEFAULT_PER_PAGE))

    try:
        page = int(page_raw)
    except (TypeError, ValueError):
        return None, None, (jsonify({"error": "Invalid page"}), 400)
    if page < 1:
        return None, None, (jsonify({"error": "Invalid page"}), 400)

    try:
        per_page = int(per_page_raw)
    except (TypeError, ValueError):
        return None, None, (jsonify({"error": "Invalid per_page"}), 400)
    if per_page < 1:
        return None, None, (jsonify({"error": "Invalid per_page"}), 400)
    if per_page > MAX_PER_PAGE:
        return None, None, (
            jsonify({"error": f"per_page must be {MAX_PER_PAGE} or fewer"}), 400
        )

    return page, per_page, None


@policies_bp.route("", methods=["GET"])
def list_policies():
    if not Config.DATABASE_URL:
        return _database_unavailable_response()

    page, per_page, error = _parse_pagination_params()
    if error:
        return error

    # Filter values are passed straight through to
    # policy_service.get_paginated_policies(), which uses SQLAlchemy's
    # filter_by() (parameterized) - never interpolated into a raw SQL
    # string. An unrecognized sector/category/sub_category value simply
    # yields an empty (but still valid, total=0) result set, not an
    # error - there is no fixed enum to validate against server-side.
    sector = request.args.get("sector")
    category = request.args.get("category")
    sub_category = request.args.get("sub_category")

    result = get_paginated_policies(
        page=page, per_page=per_page,
        sector=sector, category=category, sub_category=sub_category,
    )
    total = result["total"]
    pages = (total + per_page - 1) // per_page if total else 0

    return jsonify({
        "data": result["items"],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": pages,
        },
    }), 200


@policies_bp.route("/sectors", methods=["GET"])
def list_sectors():
    if not Config.DATABASE_URL:
        return _database_unavailable_response()

    counts = get_sector_counts()
    data = [{"sector": sector, "count": count} for sector, count in sorted(counts.items())]
    return jsonify({"data": data}), 200


@policies_bp.route("/categories", methods=["GET"])
def list_categories():
    if not Config.DATABASE_URL:
        return _database_unavailable_response()

    sector = request.args.get("sector")
    counts = get_category_counts(sector=sector)
    data = [{"category": category, "count": count} for category, count in sorted(counts.items())]
    return jsonify({"data": data}), 200


@policies_bp.route("/<policy_id>", methods=["GET"])
def get_policy(policy_id):
    """Deliberately NOT an <int:policy_id> route converter: Werkzeug
    would reject a non-numeric segment before this function ever runs,
    falling through to Flask's default HTML 404 page - exactly what the
    Phase 8 brief says not to do ("Invalid IDs should also produce an
    appropriate clean JSON error rather than an unexpected 500", and
    more broadly no HTML error page anywhere in this API). Validating
    inside the function instead means every failure mode - non-numeric
    id, and valid-but-nonexistent id - gets our own clean JSON
    response."""
    if not Config.DATABASE_URL:
        return _database_unavailable_response()

    try:
        policy_id_int = int(policy_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid policy id"}), 400

    policy = get_policy_by_id(policy_id_int)
    if policy is None:
        return jsonify({"error": "Policy not found"}), 404

    return jsonify({"data": policy}), 200
