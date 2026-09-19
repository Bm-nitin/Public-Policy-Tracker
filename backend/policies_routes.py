"""
Read-only Policy API: /api/policies/*.

Separate from auth_routes.py (which owns /api/auth/*). Deliberately
read-only (GET only) - the dataset is curated via data/*.json.

JSON-backed (post-Phase-9 architecture change - see
backend/policy_service.py's module docstring): this blueprint no longer
requires PostgreSQL, a `policies` table, or any import step at all. It
works identically whether or not Config.DATABASE_URL is configured,
because policy data now comes entirely from backend/policy_loader.py's
cached data/*.json load, not from a database query. PostgreSQL is still
used elsewhere in this app (authentication - see backend/models.py) but
that is unrelated to this blueprint.

Does not touch the existing JSON-backed GET /policies route in app.py -
that route (and chatbot.py's retrieval behind it) now shares the same
underlying policy_loader.py-backed dataset as this blueprint, just via
its own separate route.

Every response is JSON, including error responses - no route here ever
lets Flask's default HTML error page leak through, and no response ever
includes a SQL statement, credential, stack trace, or filesystem path.
"""

from flask import Blueprint, jsonify, request

from policy_service import (
    DEFAULT_PER_PAGE,
    MAX_PER_PAGE,
    get_category_counts,
    get_paginated_policies,
    get_policy_by_id,
    get_sector_counts,
)

policies_bp = Blueprint("policies_api", __name__, url_prefix="/api/policies")


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
    page, per_page, error = _parse_pagination_params()
    if error:
        return error

    # Filter values are compared with plain Python equality against the
    # in-memory JSON-loaded dataset (see policy_service.py) - never SQL,
    # nothing to parameterize or inject. An unrecognized sector/category/
    # sub_category value simply yields an empty (but still valid,
    # total=0) result set, not an error - there is no fixed enum to
    # validate against server-side.
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
    counts = get_sector_counts()
    data = [{"sector": sector, "count": count} for sector, count in sorted(counts.items())]
    return jsonify({"data": data}), 200


@policies_bp.route("/categories", methods=["GET"])
def list_categories():
    sector = request.args.get("sector")
    counts = get_category_counts(sector=sector)
    data = [{"category": category, "count": count} for category, count in sorted(counts.items())]
    return jsonify({"data": data}), 200


@policies_bp.route("/<policy_id>", methods=["GET"])
def get_policy(policy_id):
    """Deliberately NOT an <int:policy_id> route converter: Werkzeug
    would reject a non-numeric segment before this function ever runs,
    falling through to Flask's default HTML 404 page - exactly what this
    API avoids everywhere (no HTML error page anywhere in this
    blueprint). Validating inside the function instead means every
    failure mode - non-numeric id, and valid-but-nonexistent id - gets
    our own clean JSON response."""
    try:
        policy_id_int = int(policy_id)
    except (TypeError, ValueError):
        return jsonify({"error": "Invalid policy id"}), 400

    policy = get_policy_by_id(policy_id_int)
    if policy is None:
        return jsonify({"error": "Policy not found"}), 404

    return jsonify({"data": policy}), 200
