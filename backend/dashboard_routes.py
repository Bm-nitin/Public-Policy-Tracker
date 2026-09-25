"""
Phase 14 - authenticated dashboard REST endpoint.

Single route (GET /api/dashboard - see the Phase 14 brief's explicit
"do not add unnecessary duplicate endpoints" requirement), requiring a
valid session (sessions.login_required - the same mechanism
backend/conversations_routes.py and backend/saved_policies_routes.py
already use). g.current_user.id is the ONLY source of "whose dashboard
is this" - a `user_id` in the query string is read nowhere in this
file, so it has no effect whatsoever (see
test_dashboard_ignores_user_id_query_parameter() in
tests/test_dashboard.py) - there is deliberately no
GET /api/dashboard?user_id=... code path to accidentally support.

Every response is JSON, including error responses - no HTML error page,
no raw exception text, no SQL, ever reaches a client from this module.
This blueprint has no existing app-wide JSON error handler to rely on
(unlike /chat in app.py, which wraps its own body in try/except Exception
- see that route) - Flask's own default error page for an uncaught
exception is HTML, not JSON, which would both violate this app's
"every response is JSON" convention and, depending on deployment
config, could be more revealing than intended. get_dashboard_route()
therefore wraps its own call into dashboard_service in a broad
try/except, mirroring /chat's exact pattern: log the real exception
server-side only, return a fixed, generic JSON message to the client.
"""

from flask import Blueprint, g, jsonify, request

from dashboard_service import ValidationError, get_dashboard_summary
from sessions import login_required

dashboard_bp = Blueprint("dashboard", __name__, url_prefix="/api/dashboard")


def _parse_int_query_param(name):
    """Returns (value_or_None, error_response_or_None). Absence is not
    an error (the caller's default applies) - only a PRESENT but
    malformed value is. Booleans never survive query-string parsing as
    Python bool (Flask's request.args are always plain strings), so the
    boolean-rejection case is exercised at the service layer
    (dashboard_service._validate_limit()) via direct unit tests instead
    - this function's job is just "did the client send a valid integer
    string, or not"."""
    raw = request.args.get(name)
    if raw is None:
        return None, None
    try:
        return int(raw), None
    except (TypeError, ValueError):
        return None, (jsonify({"error": f"Invalid {name}"}), 400)


@dashboard_bp.route("", methods=["GET"])
@login_required
def get_dashboard_route():
    # Query parameters accepted: only the three *_limit values below.
    # Anything else in the query string (including a `user_id`, per the
    # Phase 14 brief's explicit mass-assignment/IDOR requirement) is
    # simply never read - see module docstring.
    saved_limit, error = _parse_int_query_param("saved_limit")
    if error:
        return error
    conversation_limit, error = _parse_int_query_param("conversation_limit")
    if error:
        return error
    activity_limit, error = _parse_int_query_param("activity_limit")
    if error:
        return error

    try:
        summary = get_dashboard_summary(
            g.current_user.id,
            saved_limit=saved_limit,
            conversation_limit=conversation_limit,
            activity_limit=activity_limit,
        )
    except ValidationError as e:
        return jsonify({"error": str(e)}), 400
    except Exception:
        # Never let a database/internal problem leak SQL, table names,
        # connection strings, or a stack trace to the client - see
        # module docstring. Logged server-side only, same convention
        # app.py's /chat route already uses.
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Something went wrong"}), 500

    return jsonify(summary), 200
