"""
Registration and email verification endpoints.

Phase 3 added POST /api/auth/register. Phase 4 adds:
    GET  /api/auth/verify-email?token=<token>
    POST /api/auth/resend-verification

and extends register() to create a verification token and send the
verification email after the account is created.

Explicitly out of scope here (per the Phase 3/4 briefs - "those belong to
later phases"): login, sessions, JWT, cookies. Registration and
verification do not authenticate the caller.
"""

from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from sqlalchemy.exc import IntegrityError

from config import Config
from database import db
from email_service import EmailSendError, send_verification_email
from models import EmailVerificationToken, User
from security import hash_password
from validators import normalize_email, validate_registration_payload
from verification_tokens import ensure_aware_utc, generate_verification_token, hash_token

auth_bp = Blueprint("auth", __name__, url_prefix="/api/auth")

# Self-contained cooldown for resend-verification, NOT a general-purpose
# rate limiter - see resend_verification()'s docstring. Real rate-limiting
# infrastructure belongs to the later security-hardening phase per the
# Phase 4 brief.
RESEND_COOLDOWN_SECONDS = 60

# Returned for every "this token doesn't work" case (missing, malformed,
# unknown, expired, already used) so a caller can never learn which case
# applied - see verify_email().
_GENERIC_TOKEN_ERROR = ("Invalid or expired verification link", 400)


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
