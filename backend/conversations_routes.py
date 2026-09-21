"""
Phase 12 - authenticated conversation/message REST API.

Every route here requires a valid session (sessions.login_required -
the same decorator/identity mechanism auth_routes.py's /me endpoint
already uses; see sessions.py's module docstring: identity always comes
from a server-validated session cookie, never a client-supplied user id
anywhere in the request). g.current_user.id is the ONLY source of
"whose conversation is this" used anywhere in this file - a
conversation_id in the URL is always just an opaque number to look up
and verify ownership of (backend/conversations_service.py does the
actual verification; this module never queries Conversation/Message
directly).

A conversation_id that doesn't exist and one that exists but belongs to
someone else are both mapped to the exact same 404 - see
conversations_service.ConversationNotFoundError's docstring for why.

Every response is JSON, including error responses - no HTML error page,
no raw exception text, no SQL, ever reaches a client from this module
(mirrors backend/policies_routes.py's existing convention).
"""

from flask import Blueprint, g, jsonify, request

from conversations_service import (
    MAX_CONVERSATIONS_PER_PAGE,
    MAX_MESSAGES_PER_PAGE,
    ConversationNotFoundError,
    DEFAULT_CONVERSATIONS_PER_PAGE,
    DEFAULT_MESSAGES_PER_PAGE,
    ValidationError,
    add_message,
    create_conversation,
    delete_conversation,
    get_owned_conversation,
    list_conversations,
    list_messages,
)
from sessions import login_required

conversations_bp = Blueprint("conversations", __name__, url_prefix="/api/conversations")


def _parse_pagination_params(default_per_page, max_per_page):
    """Same validate-don't-trust approach as
    backend/policies_routes.py's _parse_pagination_params() - reimplemented
    here (rather than imported) because the two blueprints intentionally
    have different defaults/maximums for conversations vs. messages, and
    sharing one function with parameters for that would be more indirection
    than the four lines of duplication it would save."""
    page_raw = request.args.get("page", "1")
    per_page_raw = request.args.get("per_page", str(default_per_page))

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
    if per_page > max_per_page:
        return None, None, (jsonify({"error": f"per_page must be {max_per_page} or fewer"}), 400)

    return page, per_page, None


def _parse_conversation_id(raw_id):
    try:
        return int(raw_id), None
    except (TypeError, ValueError):
        return None, (jsonify({"error": "Invalid conversation id"}), 400)


@conversations_bp.route("", methods=["POST"])
@login_required
def create_conversation_route():
    data = request.get_json(silent=True) or {}
    # Mass-assignment guard: only `title`/`first_message` are ever read
    # from the body - nothing else in the payload (e.g. a client-
    # supplied `user_id` or `id`) has any effect on what gets created.
    title = data.get("title")
    first_message = data.get("first_message")

    try:
        conversation = create_conversation(
            g.current_user.id, title=title, first_message=first_message
        )
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"data": conversation.to_dict()}), 201


@conversations_bp.route("", methods=["GET"])
@login_required
def list_conversations_route():
    page, per_page, error = _parse_pagination_params(
        DEFAULT_CONVERSATIONS_PER_PAGE, MAX_CONVERSATIONS_PER_PAGE
    )
    if error:
        return error

    result = list_conversations(g.current_user.id, page=page, per_page=per_page)
    total = result["total"]
    pages = (total + per_page - 1) // per_page if total else 0

    return jsonify({
        "data": result["items"],
        "pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages},
    }), 200


@conversations_bp.route("/<conversation_id>", methods=["GET"])
@login_required
def get_conversation_route(conversation_id):
    parsed_id, error = _parse_conversation_id(conversation_id)
    if error:
        return error

    try:
        conversation = get_owned_conversation(g.current_user.id, parsed_id)
    except ConversationNotFoundError:
        return jsonify({"error": "Conversation not found"}), 404

    return jsonify({"data": conversation.to_dict()}), 200


@conversations_bp.route("/<conversation_id>", methods=["DELETE"])
@login_required
def delete_conversation_route(conversation_id):
    parsed_id, error = _parse_conversation_id(conversation_id)
    if error:
        return error

    try:
        delete_conversation(g.current_user.id, parsed_id)
    except ConversationNotFoundError:
        return jsonify({"error": "Conversation not found"}), 404

    return jsonify({"data": {"deleted": True}}), 200


@conversations_bp.route("/<conversation_id>/messages", methods=["GET"])
@login_required
def list_messages_route(conversation_id):
    parsed_id, error = _parse_conversation_id(conversation_id)
    if error:
        return error

    page, per_page, error = _parse_pagination_params(
        DEFAULT_MESSAGES_PER_PAGE, MAX_MESSAGES_PER_PAGE
    )
    if error:
        return error

    try:
        result = list_messages(g.current_user.id, parsed_id, page=page, per_page=per_page)
    except ConversationNotFoundError:
        return jsonify({"error": "Conversation not found"}), 404

    total = result["total"]
    pages = (total + per_page - 1) // per_page if total else 0

    return jsonify({
        "data": result["items"],
        "pagination": {"page": page, "per_page": per_page, "total": total, "pages": pages},
    }), 200


@conversations_bp.route("/<conversation_id>/messages", methods=["POST"])
@login_required
def add_message_route(conversation_id):
    parsed_id, error = _parse_conversation_id(conversation_id)
    if error:
        return error

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Malformed request body"}), 400

    # Mass-assignment guard, same as create_conversation_route(): only
    # role/content/metadata are ever read.
    role = data.get("role")
    content = data.get("content")
    metadata = data.get("metadata")

    try:
        message = add_message(g.current_user.id, parsed_id, role, content, metadata=metadata)
    except ConversationNotFoundError:
        return jsonify({"error": "Conversation not found"}), 404
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"data": message.to_dict()}), 201
