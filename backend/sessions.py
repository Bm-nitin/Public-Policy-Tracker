"""
Phase 5: server-side sessions.

Token generation/hashing mirrors backend/verification_tokens.py
deliberately: a cryptographically random, high-entropy token is
generated; only its SHA-256 hash is ever stored (see UserSession in
models.py); the raw token is the only thing that ever leaves the server,
here as an HttpOnly cookie value instead of an email link. Same reasoning
applies for using SHA-256 rather than Argon2id: the token itself already
has 256 bits of entropy, so a fast deterministic hash + indexed DB lookup
is correct and standard, not a shortcut.

This file also owns:
  - set_session_cookie() / clear_session_cookie(): the ONLY place cookie
    flags (HttpOnly, Secure, SameSite, expiry) are set - centralized here
    rather than scattered across routes, per the Phase 5 brief.
  - get_current_user(): the reusable "who is making this request"
    helper. Identity is derived ONLY from a server-validated session
    token read from the cookie - never from any client-supplied user id
    in a request body/header, which would allow trivial impersonation.
  - login_required: a decorator for future authenticated routes. Not
    applied to any new business-feature route in this phase (none
    exist yet) - see auth_routes.py's /me endpoint for its one use here,
    added specifically to exercise and test this helper.
"""

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from functools import wraps

from flask import g, jsonify, request

from config import Config

SESSION_TOKEN_BYTES = 32  # 256 bits of entropy


def generate_session_token():
    """Returns (raw_token, token_hash, expires_at). See module docstring
    for why SHA-256 (not Argon2id) is correct here."""
    raw_token = secrets.token_urlsafe(SESSION_TOKEN_BYTES)
    token_hash = hash_session_token(raw_token)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=Config.SESSION_LIFETIME_HOURS)
    return raw_token, token_hash, expires_at


def hash_session_token(raw_token):
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def set_session_cookie(response, raw_token, expires_at):
    """The only place SESSION_COOKIE_* config is turned into an actual
    Set-Cookie header. The cookie value is the raw token - opaque,
    unguessable, and meaningless without the server-side UserSession row
    it's hashed against; it carries no user data itself."""
    response.set_cookie(
        Config.SESSION_COOKIE_NAME,
        raw_token,
        httponly=True,
        secure=Config.SESSION_COOKIE_SECURE,
        samesite=Config.SESSION_COOKIE_SAMESITE,
        expires=expires_at,
        path="/",
    )


def clear_session_cookie(response):
    response.delete_cookie(
        Config.SESSION_COOKIE_NAME,
        httponly=True,
        secure=Config.SESSION_COOKIE_SECURE,
        samesite=Config.SESSION_COOKIE_SAMESITE,
        path="/",
    )


def get_current_user():
    """Reads the session cookie, validates it against the database, and
    returns the associated User - or None if there's no valid
    authenticated session. Imports models locally to avoid a circular
    import (models.py does not import sessions.py, but auth_routes.py
    imports both, and models.py is the more foundational of the two)."""
    if not Config.DATABASE_URL:
        # No database configured at all - there is no way any session
        # could be valid, so treat this the same as "no session" rather
        # than letting a database call fail below. Routes that need a
        # more specific "service unavailable" message check
        # Config.DATABASE_URL themselves before calling this (see
        # login()/logout() in auth_routes.py).
        return None

    from models import User, UserSession

    raw_token = request.cookies.get(Config.SESSION_COOKIE_NAME)
    if not raw_token:
        return None

    token_hash = hash_session_token(raw_token)
    session_row = UserSession.query.filter_by(session_token_hash=token_hash).first()
    if session_row is None or not session_row.is_valid():
        return None

    return User.query.filter_by(id=session_row.user_id).first()


def login_required(view_func):
    """Decorator for future authenticated routes. Rejects with 401 if
    there's no valid session; otherwise stashes the user on flask.g for
    the view to use, and never trusts any other source of identity."""

    @wraps(view_func)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if user is None:
            return jsonify({"error": "Authentication required"}), 401
        g.current_user = user
        return view_func(*args, **kwargs)

    return wrapped
