"""
Phase 3: registration endpoint.

POST /api/auth/register - the only route this phase adds. Chosen over a
bare /register to namespace it under /api/auth, leaving room for
/api/auth/login etc. in later phases without colliding with the existing
top-level routes (/, /health, /chat, /policies - see app.py).

Explicitly out of scope here (per the Phase 3 brief - "those belong to
later phases"): login, sessions, JWT, cookies, email verification/tokens.
Registration creates an unverified account and returns immediately - it
does not authenticate the caller.
"""

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from config import Config
from database import db
from models import User
from security import hash_password
from validators import validate_registration_payload

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")


@auth_bp.route("/register", methods=["POST"])
def register():
    if not Config.DATABASE_URL:
        # Controlled, explicit response rather than a confusing crash when
        # db.session is used with no database configured (see
        # database.py's docstring on import-time safety).
        return jsonify({
            "error": "Registration is temporarily unavailable: no database is configured."
        }), 503

    # silent=True: an unparsable/missing JSON body becomes None here and
    # is reported as a normal validation error below, rather than the
    # framework raising and this route needing its own broad
    # except-Exception-as-500 (see CURRENT_ARCHITECTURE.md for the
    # existing /chat endpoint's version of that issue, which this new
    # route does not repeat).
    data = request.get_json(silent=True)

    errors, cleaned = validate_registration_payload(data)
    if errors:
        return jsonify({"error": "Invalid registration data", "details": errors}), 400

    password_hash = hash_password(cleaned["password"])

    user = User(
        name=cleaned["name"],
        email=cleaned["email"],
        password_hash=password_hash,
        email_verified=False,
    )

    try:
        db.session.add(user)
        db.session.commit()
    except IntegrityError:
        # Final line of defense against a duplicate email, including the
        # race-condition window between an application-level existence
        # check and the insert - the database's UNIQUE constraint on
        # users.email is what actually prevents the duplicate; this only
        # turns that into a clean HTTP response instead of a 500.
        db.session.rollback()
        return jsonify({
            "error": "An account with this email already exists"
        }), 409

    return jsonify({
        "message": "Registration successful",
        "user": user.to_public_dict(),
    }), 201
