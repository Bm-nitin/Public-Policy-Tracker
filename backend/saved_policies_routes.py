"""
Phase 13 - authenticated saved (bookmarked) policy REST API.

Every route requires a valid session (sessions.login_required - see
backend/conversations_routes.py's identical Phase 12 convention, which
this module follows exactly). g.current_user.id is the ONLY source of
"whose saved policy is this" used anywhere in this file - a `user_id`
appearing in a request body (mass assignment) is never read, and a
policy_id in the URL/body is always just an opaque value to validate
and resolve, never trusted to belong to the requester without an
explicit ownership-filtered query.

Every response is JSON, including error responses - no HTML error page,
no raw exception text, no SQL, ever reaches a client from this module.
"""

from flask import Blueprint, g, jsonify, request

from saved_policies_service import (
    SavedPolicyNotFoundError,
    ValidationError,
    delete_saved_policy,
    get_saved_policy,
    list_saved_policies,
    save_policy,
)
from sessions import login_required

saved_policies_bp = Blueprint("saved_policies", __name__, url_prefix="/api/saved-policies")


def _parse_pagination_params():
    """Same validate-don't-trust approach as
    backend/conversations_routes.py's _parse_pagination_params()."""
    page_raw = request.args.get("page", "1")
    per_page_raw = request.args.get("per_page", "20")

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
    if per_page > 100:
        return None, None, (jsonify({"error": "per_page must be 100 or fewer"}), 400)

    return page, per_page, None


def _parse_policy_id_from_path(raw_id):
    try:
        return int(raw_id), None
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Invalid policy id"}), 400)


@saved_policies_bp.route("", methods=["POST"])
@login_required
def save_policy_route():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Malformed request body"}), 400

    # Mass-assignment guard: only `policy_id` is ever read from the
    # body - a client-supplied `user_id` (or anything else) has zero
    # effect on whose saved relationship gets created. See the Phase 13
    # brief's explicit mass-assignment test.
    policy_id = data.get("policy_id")

    try:
        saved, policy, created = save_policy(g.current_user.id, policy_id)
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except SavedPolicyNotFoundError:
        return jsonify({"error": "Policy not found"}), 404

    status = 201 if created else 200
    return jsonify({"data": {**saved.to_dict(), "policy": policy}}), status


@saved_policies_bp.route("", methods=["GET"])
@login_required
def list_saved_policies_route():
    page, per_page, error = _parse_pagination_params()
    if error:
        return error

    result = list_saved_policies(g.current_user.id, page=page, per_page=per_page)
    total = result["total"]
    pages = (total + per_page - 1) // per_page if total else 0

    return jsonify({
        "data": result["items"],
        "pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages},
    }), 200


@saved_policies_bp.route("/<policy_id>", methods=["GET"])
@login_required
def get_saved_policy_route(policy_id):
    parsed_id, error = _parse_policy_id_from_path(policy_id)
    if error:
        return error

    try:
        saved, policy = get_saved_policy(g.current_user.id, parsed_id)
    except SavedPolicyNotFoundError:
        return jsonify({"error": "Saved policy not found"}), 404

    return jsonify({"data": {**saved.to_dict(), "policy": policy}}), 200


@saved_policies_bp.route("/<policy_id>", methods=["DELETE"])
@login_required
def delete_saved_policy_route(policy_id):
    parsed_id, error = _parse_policy_id_from_path(policy_id)
    if error:
        return error

    try:
        delete_saved_policy(g.current_user.id, parsed_id)
    except SavedPolicyNotFoundError:
        return jsonify({"error": "Saved policy not found"}), 404

    return jsonify({"data": {"deleted": True}}), 200
