"""
Phase 17 - authenticated profile & account management.

    GET   /api/account/profile          read your own profile
    PATCH /api/account/profile          update your display name
    POST  /api/account/change-password  change your password

What the existing schema supports (and therefore what this module does):
  - users.name is a plain editable column -> editable here.
  - users.email is the login identity and is verified through the
    EmailVerificationToken flow, which has no "pending new email" concept
    -> NOT editable here (changing it safely needs a new verification
    flow, which is out of scope). It is rejected, not silently ignored.
  - users.password_hash is Argon2id via security.hash_password() /
    verify_password(); validators.validate_password() holds the shared
    length rules (registration and reset use the same one).
No migration, no new column, no new dependency.

Authorization model: sessions.login_required puts the user on g; g.current_user
is the ONLY source of "whose account is this". There is no user id in any
URL, query string or body that is read - and a body that tries to supply
one (id, user_id, email, ...) is rejected with 400 instead of being quietly
dropped, so a mass-assignment attempt is visible rather than ignored.

Passwords: never logged, never echoed, never stored in plaintext. Database
failures log only the exception CLASS NAME - SQLAlchemy's own message text
embeds the SQL parameters (here: a password hash or a name), which must not
reach logs or clients. Every response is JSON; failures are generic.

Status codes: a wrong CURRENT password is 400, deliberately not 401 - the
frontend (like dashboard.js) treats 401 as "your session is gone, go to
login", which a typo in a form field must not trigger.
"""

import logging
from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

import rate_limit
from config import Config
from database import db
from models import UserSession
from security import hash_password, verify_password
from sessions import hash_session_token, login_required
from validators import validate_name, validate_password
from verification_tokens import ensure_aware_utc

logger = logging.getLogger(__name__)

account_bp = Blueprint("account", __name__, url_prefix="/api/account")

_PROFILE_EDITABLE_FIELDS = {"name"}
_PASSWORD_FIELDS = {"current_password", "new_password"}
_GENERIC_SERVER_ERROR = "Something went wrong. Please try again."


@account_bp.after_request
def _no_store(response):
    # Account data (and the password-change response) must never be kept by
    # a shared cache or the browser's back/forward cache.
    response.headers["Cache-Control"] = "no-store"
    return response


def _profile_dict(user):
    """The user's own profile. Deliberately NOT User.to_public_dict(): that
    carries the internal numeric id, which this page has no use for. No
    password_hash, no last_login, no updated_at."""
    # SQLite hands timestamps back naive; normalize to aware UTC so the
    # ISO string always carries an offset and a browser can't misread it
    # as local time (same reason as conversations_service).
    created_at = ensure_aware_utc(user.created_at) if user.created_at is not None else None
    return {
        "name": user.name,
        "email": user.email,
        "email_verified": bool(user.email_verified),
        "created_at": created_at.isoformat() if created_at is not None else None,
    }


def _error(message, status, details=None):
    body = {"error": message}
    if details:
        body["details"] = details
    return jsonify(body), status


def _db_failure(action, exc):
    db.session.rollback()
    logger.error("account %s failed: %s", action, type(exc).__name__)
    return _error(_GENERIC_SERVER_ERROR, 500)


@account_bp.route("/profile", methods=["GET"])
@login_required
def get_profile():
    return jsonify({"profile": _profile_dict(g.current_user)}), 200


@account_bp.route("/profile", methods=["PATCH"])
@login_required
def update_profile():
    # silent=True: a missing / non-JSON body becomes None and is reported
    # as an ordinary validation error (also means a cross-site "simple"
    # text/plain POST can't smuggle a body in - it isn't parsed at all).
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Invalid profile data", 400, ["Request body must be a JSON object with name"])

    forbidden = sorted(k for k in data if k not in _PROFILE_EDITABLE_FIELDS)
    if forbidden:
        # Fixed text - never echo client-supplied key names back.
        return _error("Invalid profile data", 400, ["Only name can be changed here"])

    if "name" not in data:
        return _error("Invalid profile data", 400, ["name is required"])

    errors = validate_name(data["name"])
    if errors:
        return _error("Invalid profile data", 400, errors)

    user = g.current_user
    try:
        user.name = data["name"].strip()
        user.updated_at = datetime.now(timezone.utc)
        db.session.add(user)
        db.session.commit()
    except SQLAlchemyError as exc:
        return _db_failure("profile update", exc)

    return jsonify({"message": "Profile updated", "profile": _profile_dict(user)}), 200


@account_bp.route("/change-password", methods=["POST"])
@login_required
def change_password():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("Invalid password data", 400,
                      ["Request body must be a JSON object with current_password and new_password"])

    if any(k not in _PASSWORD_FIELDS for k in data):
        return _error("Invalid password data", 400, ["Only current_password and new_password are accepted"])

    current_password = data.get("current_password")
    new_password = data.get("new_password")

    errors = []
    if not isinstance(current_password, str) or not current_password:
        errors.append("current_password is required")
    if "new_password" not in data:
        errors.append("new_password is required")
    else:
        # Same rules as registration and password reset - one shared function.
        errors.extend(e.replace("password must", "new_password must", 1)
                      for e in validate_password(new_password))
    if errors:
        return _error("Invalid password data", 400, errors)

    user = g.current_user

    # Wrong-current-password attempts are limited per ACCOUNT (a stolen
    # session must not become an unlimited oracle for the real password).
    # Checked before the Argon2 verification; only failures are counted.
    blocked_for = rate_limit.check("chpw:user", str(user.id))
    if blocked_for:
        return rate_limit.too_many_requests(blocked_for)

    if not verify_password(current_password, user.password_hash):
        rate_limit.record("chpw:user", str(user.id))
        return _error("Current password is incorrect", 400)

    if new_password == current_password:
        return _error("Invalid password data", 400,
                      ["new_password must be different from your current password"])

    now = datetime.now(timezone.utc)
    raw_cookie = request.cookies.get(Config.SESSION_COOKIE_NAME)
    current_hash = hash_session_token(raw_cookie) if raw_cookie else None

    try:
        user.password_hash = hash_password(new_password)
        user.updated_at = now
        db.session.add(user)

        # Sign every OTHER device out (a changed password is the standard
        # response to "someone else may have my session") while keeping the
        # session making this request, so the user isn't logged out of the
        # page they are looking at. reset-password revokes all sessions
        # because its caller had none; this caller has one worth keeping.
        for row in UserSession.query.filter_by(user_id=user.id, revoked_at=None).all():
            if row.session_token_hash != current_hash:
                row.revoked_at = now
                db.session.add(row)

        db.session.commit()
    except SQLAlchemyError as exc:
        return _db_failure("password change", exc)

    return jsonify({
        "message": "Password updated. Any other signed-in devices have been signed out."
    }), 200
