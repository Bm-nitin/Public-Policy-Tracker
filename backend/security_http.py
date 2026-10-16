"""
Phase 18 - request/browser-level security for the Flask app.

init_security(app) installs, in one place:

  1. TRUSTED PROXY handling (werkzeug ProxyFix) - only when
     Config.TRUSTED_PROXY_COUNT > 0; see rate_limit.py for why.
  2. REQUEST SIZE LIMIT - a declared Content-Length above
     Config.MAX_REQUEST_BYTES is rejected with 413 before the body is read;
     MAX_CONTENT_LENGTH is also set so werkzeug enforces the same ceiling
     for bodies that arrive without a Content-Length (chunked).
  3. CSRF defence for cookie authentication - see _reject_cross_site().
  4. RESPONSE HEADERS - nosniff, no-referrer, an API-only CSP, HSTS (when the
     deployment is HTTPS-only) and Cache-Control: no-store on every private
     endpoint.

CSRF, and why this design: the session cookie is SameSite=None because the
static frontend and the API are on different sites, so a browser WILL
attach it to a request made by any other website. Cross-origin JSON
requests are already stopped by CORS preflight, but a "simple" request
(an auto-submitted HTML form POST, no preflight) that needs no JSON body -
POST /api/auth/logout, POST /api/conversations - was accepted, so a hostile
page could log a visitor out or create conversations as them (confirmed by
test before this change). Fix: an unsafe request that carries our session
cookie and a browser-supplied Origin (or Referer) from anywhere other than
the configured frontend origin(s) is rejected with 403. Browsers always send
Origin on cross-origin POST/PATCH/DELETE and a page cannot forge it; a
request with neither header is not a browser acting for an attacker (they
cannot make a victim's browser omit both), so it passes. Requests WITHOUT
the session cookie are untouched, so the public endpoints (login, register,
/chat ...) keep working from any origin exactly as before.
"""

import logging
from urllib.parse import urlsplit

from flask import jsonify, request
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config

logger = logging.getLogger(__name__)

_UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Everything that returns or changes per-user data: never cache.
_PRIVATE_PREFIXES = ("/api/auth/", "/api/account/", "/api/dashboard", "/api/conversations", "/api/saved-policies", "/chat")


def _origin_of(url):
    """'https://a.example/x?y' -> 'https://a.example'; junk -> None."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}".lower()


def allowed_origins():
    """Origins permitted to make cookie-authenticated unsafe requests: the
    configured CORS origin(s) plus FRONTEND_URL. Same-origin is handled
    separately. CORS_ORIGINS='*' contributes nothing here."""
    allowed = set()
    if Config.CORS_ORIGINS != "*":
        for origin in Config.CORS_ORIGINS:
            normalised = _origin_of(origin) or origin.rstrip("/").lower()
            allowed.add(normalised)
    if Config.FRONTEND_URL:
        normalised = _origin_of(Config.FRONTEND_URL)
        if normalised:
            allowed.add(normalised)
    return allowed


def _is_allowed_origin(origin):
    if origin in allowed_origins():
        return True
    own = urlsplit(request.host_url).netloc.lower()   # same-origin (scheme-insensitive: TLS may end at a proxy)
    parts = urlsplit(origin)
    return bool(parts.netloc) and parts.netloc.lower() == own


def _reject_cross_site():
    if request.method not in _UNSAFE_METHODS:
        return None
    if Config.SESSION_COOKIE_NAME not in request.cookies:
        return None                                   # no ambient credential to abuse
    origin = request.headers.get("Origin")
    if origin is None:
        referer = request.headers.get("Referer")
        if not referer:
            return None                               # not a browser acting for a third party
        origin = _origin_of(referer)
        if origin is None:
            return None
    if _is_allowed_origin(origin):
        return None
    logger.warning("rejected cross-site %s %s (origin not allowed)", request.method, request.path)
    return jsonify({"error": "Cross-site request rejected"}), 403


def _reject_oversized():
    if request.method in _UNSAFE_METHODS:
        length = request.content_length
        if length is not None and length > Config.MAX_REQUEST_BYTES:
            return jsonify({"error": "Request too large"}), 413
    return None


def init_security(app):
    app.config["MAX_CONTENT_LENGTH"] = Config.MAX_REQUEST_BYTES

    if Config.TRUSTED_PROXY_COUNT > 0:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=Config.TRUSTED_PROXY_COUNT, x_proto=0, x_host=0, x_port=0, x_prefix=0)

    @app.before_request
    def _guard():
        return _reject_oversized() or _reject_cross_site()

    @app.errorhandler(RequestEntityTooLarge)
    def _too_large(_exc):
        return jsonify({"error": "Request too large"}), 413

    @app.after_request
    def _headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if response.mimetype == "application/json":
            # JSON never needs to load or run anything, nor be framed.
            response.headers.setdefault("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'")
        if Config.SESSION_COOKIE_SECURE:
            response.headers.setdefault("Strict-Transport-Security", "max-age=15552000")
        if request.path.startswith(_PRIVATE_PREFIXES):
            response.headers["Cache-Control"] = "no-store"
        return response
