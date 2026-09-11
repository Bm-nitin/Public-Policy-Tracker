"""
Registration, email verification, and session (login/logout) endpoints.

Phase 3 added POST /api/auth/register. Phase 4 added:
    GET  /api/auth/verify-email?token=<token>
    POST /api/auth/resend-verification

Phase 5 adds:
    POST /api/auth/login
    POST /api/auth/logout
    GET  /api/auth/me

See backend/sessions.py for the actual session token/cookie mechanics and
the reusable get_current_user()/login_required helper this phase adds.
"""

from datetime import datetime, timezone

from flask import Blueprint, g, jsonify, request
from sqlalchemy.exc import IntegrityError

from config import Config
from database import db
from email_service import EmailSendError, send_verification_email
from models import EmailVerificationToken, User, UserSession
from security import hash_password, verify_password
from sessions import (
    clear_session_cookie,
    generate_session_token,
    get_current_user,
    hash_session_token,
    login_required,
    set_session_cookie,
)
from validators import (
    MAX_EMAIL_LENGTH,
    MAX_PASSWORD_LENGTH,
    normalize_email,
    validate_registration_payload,
)
from verification_tokens import ensure_aware_utc, generate_verification_token, hash_token

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# Self-contained cooldown for resend-verification, NOT a general-purpose
# rate limiter - see resend_verification()'s docstring. Real rate-limiting
# infrastructure belongs to the later security-hardening phase per the
# Phase 4 brief. Login has no cooldown/lockout of its own yet for the
# same documented reason - a clear hook for it is login()'s single
# "generic auth failure" return point below, where a future phase can
# add a check without touching anything else.
RESEND_COOLDOWN_SECONDS = 60

# Returned for every "this token doesn't work" case (missing, malformed,
# unknown, expired, already used) so a caller can never learn which case
# applied - see verify_email().
_GENERIC_TOKEN_ERROR = ("Invalid or expired verification link", 400)

# Returned for every "these credentials don't work" case (unknown email,
# wrong password) - identical response so a caller can't distinguish
# "no such account" from "wrong password" (user enumeration). See
# login()'s docstring for how response *timing* is also equalized.
_GENERIC_LOGIN_ERROR = ("Invalid email or password", 401)

# A real Argon2id hash of a fixed, never-used password, computed once at
# import time. When no user is found for the submitted email, login()
# still calls verify_password() against THIS hash (which will always
# fail) instead of skipping straight to the generic error - so a request
# for an unregistered email costs roughly the same CPU time as one for a
# real email with a wrong password, closing an otherwise-real timing
# side channel for user enumeration.
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-never-used-for-timing-safety-only")


def _database_unavailable_response(feature):
    return jsonify({
        "error": f"{feature} is temporarily unavailable: no database is configured."
    }), 503


@auth_bp.route("/register", methods=["POST"])
def register():
    if not Config.DATABASE_URL:
        return _database_unavailable_response("Registration")

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
        db.session.flush()  # assigns user.id without finalizing the transaction

        raw_token, token_hash, expires_at = generate_verification_token()
        verification_token = EmailVerificationToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
        db.session.add(verification_token)
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

    # DOCUMENTED POLICY: email delivery failure does NOT roll back the
    # account. The user and their verification token both remain
    # committed - the account exists, email_verified stays false, and
    # POST /api/auth/resend-verification is the recovery path if the
    # first send fails (e.g. a transient SMTP outage). Rolling back a
    # successful registration because of an unrelated infrastructure
    # hiccup would be worse UX and would just recreate the same
    # uniqueness race on a retry.
    email_sent = True
    try:
        send_verification_email(user, raw_token)
    except EmailSendError:
        email_sent = False

    message = (
        "Registration successful. Please check your email to verify your account."
        if email_sent else
        "Registration successful, but the verification email could not be "
        "sent. Use /api/auth/resend-verification to request a new one."
    )

    return jsonify({
        "message": message,
        "user": user.to_public_dict(),
    }), 201


@auth_bp.route("/verify-email", methods=["GET"])
def verify_email():
    if not Config.DATABASE_URL:
        return _database_unavailable_response("Email verification")

    raw_token = request.args.get("token")
    if not raw_token:
        message, status = _GENERIC_TOKEN_ERROR
        return jsonify({"error": message}), status

    token_hash = hash_token(raw_token)
    token_row = EmailVerificationToken.query.filter_by(token_hash=token_hash).first()

    if token_row is None or not token_row.is_valid():
        # Covers: unknown token, already used, expired - identical
        # response for all three so a caller can't distinguish them.
        message, status = _GENERIC_TOKEN_ERROR
        return jsonify({"error": message}), status

    user = User.query.filter_by(id=token_row.user_id).first()
    if user is None:
        # Should not happen given the FK constraint, but never trust a
        # lookup blindly - same generic response, no internal detail.
        message, status = _GENERIC_TOKEN_ERROR
        return jsonify({"error": message}), status

    now = datetime.now(timezone.utc)

    if user.email_verified:
        # Already verified (e.g. a second valid link was clicked) - don't
        # re-verify or error, just consume this token and confirm.
        token_row.used_at = now
        db.session.add(token_row)
        db.session.commit()
        return jsonify({
            "message": "Email already verified",
            "user": user.to_public_dict(),
        }), 200

    user.email_verified = True
    token_row.used_at = now
    db.session.add(user)
    db.session.add(token_row)
    db.session.commit()

    return jsonify({
        "message": "Email verified successfully",
        "user": user.to_public_dict(),
    }), 200


@auth_bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    if not Config.DATABASE_URL:
        return _database_unavailable_response("Resending verification email")

    data = request.get_json(silent=True) or {}
    email = data.get("email")

    # Always the same response regardless of what's actually true, so a
    # caller can't use this endpoint to discover which emails are
    # registered or already verified.
    generic_response = jsonify({
        "message": "If an account with that email exists and is not yet "
                    "verified, a new verification email has been sent."
    }), 200

    if not isinstance(email, str) or not email.strip():
        return generic_response

    user = User.query.filter_by(email=normalize_email(email)).first()
    if user is None or user.email_verified:
        return generic_response

    existing_tokens = EmailVerificationToken.query.filter_by(user_id=user.id).all()
    active_tokens = [t for t in existing_tokens if t.used_at is None]

    now = datetime.now(timezone.utc)
    if active_tokens:
        most_recent_created_at = max(
            ensure_aware_utc(t.created_at) for t in active_tokens
        )
        seconds_since_last = (now - most_recent_created_at).total_seconds()
        if seconds_since_last < RESEND_COOLDOWN_SECONDS:
            # Cooldown in effect - same generic response, no indication
            # to the caller that this is what happened.
            return generic_response

    # Supersede any still-active tokens so only the newest one works.
    for token in active_tokens:
        token.used_at = now
        db.session.add(token)

    raw_token, token_hash, expires_at = generate_verification_token()
    new_token = EmailVerificationToken(
        user_id=user.id,
        token_hash=token_hash,
        expires_at=expires_at,
    )
    db.session.add(new_token)
    db.session.commit()

    try:
        send_verification_email(user, raw_token)
    except EmailSendError:
        # Same documented policy as registration: don't fail the request
        # or reveal anything - the token now exists either way and a
        # future resend attempt (after the cooldown) can try again.
        pass

    return generic_response


@auth_bp.route("/login", methods=["POST"])
def login():
    if not Config.DATABASE_URL:
        return _database_unavailable_response("Login")

    data = request.get_json(silent=True) or {}
    email = data.get("email")
    password = data.get("password")

    message, status = _GENERIC_LOGIN_ERROR
    generic_error = jsonify({"error": message}), status

    # Bounded input / safe parsing: reject non-strings and obviously
    # oversized input before touching the database or the hasher at all -
    # mirrors validators.py's approach for registration. Uses the same
    # length ceilings as registration rather than inventing new ones.
    if (
        not isinstance(email, str) or not email.strip() or len(email) > MAX_EMAIL_LENGTH
        or not isinstance(password, str) or not password or len(password) > MAX_PASSWORD_LENGTH
    ):
        return generic_error

    user = User.query.filter_by(email=normalize_email(email)).first()

    # See _DUMMY_PASSWORD_HASH's module-level comment: always run a real
    # Argon2 verification, even when no user was found, so response
    # timing doesn't reveal whether the email is registered.
    password_hash_to_check = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    password_matches = verify_password(password, password_hash_to_check)

    if user is None or not password_matches:
        return generic_error

    if not user.email_verified:
        # A distinct response IS given here, deliberately, per the Phase
        # 5 brief's carve-out ("the API may use a separate response only
        # if the existing product flow genuinely requires it"): Phase 4's
        # resend-verification flow requires the user to know they need to
        # verify before they can log in, and this only fires after a
        # CORRECT password match - so it doesn't tell an attacker
        # anything beyond what a correct guess already would have. It
        # does not distinguish "email exists" from "email doesn't exist"
        # for an incorrect guess (that's still the generic error above).
        return jsonify({
            "error": "Please verify your email address before logging in."
        }), 403

    raw_token, token_hash, expires_at = generate_session_token()
    session_row = UserSession(user_id=user.id, session_token_hash=token_hash, expires_at=expires_at)

    # Only updated on this, the success path - a wrong password or
    # unknown email never touches last_login.
    user.last_login = datetime.now(timezone.utc)

    db.session.add(session_row)
    db.session.add(user)
    db.session.commit()

    response = jsonify({
        "message": "Login successful",
        "user": user.to_public_dict(),
    })
    set_session_cookie(response, raw_token, expires_at)
    return response, 200


@auth_bp.route("/logout", methods=["POST"])
def logout():
    if not Config.DATABASE_URL:
        return _database_unavailable_response("Logout")

    raw_token = request.cookies.get(Config.SESSION_COOKIE_NAME)
    if raw_token:
        token_hash = hash_session_token(raw_token)
        session_row = UserSession.query.filter_by(session_token_hash=token_hash).first()
        if session_row is not None and session_row.revoked_at is None:
            session_row.revoked_at = datetime.now(timezone.utc)
            db.session.add(session_row)
            db.session.commit()

    # Idempotent by construction: no cookie, an already-revoked session,
    # or an unknown token all fall through to the same safe response and
    # the same cleared cookie - repeated logout never errors.
    response = jsonify({"message": "Logged out"})
    clear_session_cookie(response)
    return response, 200


@auth_bp.route("/me", methods=["GET"])
@login_required
def me():
    """Minimal session-introspection endpoint - not a business feature
    (no saved policies/history/dashboard, which are explicitly later
    phases), just the smallest possible real usage of
    sessions.login_required/get_current_user so the reusable auth helper
    this phase adds is actually exercised by a route, not just tested in
    isolation."""
    return jsonify({"user": g.current_user.to_public_dict()}), 200
