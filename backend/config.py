"""
Centralized backend configuration.

Phase 1 goal: give every backend module ONE place to read environment
config from, instead of each file doing its own os.getenv()/load_dotenv()
(as chatbot.py previously did on its own). This does not add any new
functionality (no DB, no auth, no email) - it only relocates and
generalizes config that already existed (Gemini key resolution, CORS
origin) so future phases (DB URL, session secret, rate-limit settings,
etc.) have a natural place to land.

All defaults below reproduce the exact behavior the app had before this
file existed - nothing changes unless a new variable is set in .env.
"""

import os
import secrets

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(BASE_DIR, ".env")

load_dotenv(dotenv_path=ENV_PATH)


def _resolve_gemini_api_key():
    """Same fallback chain chatbot.py used inline: GEMINI_API_KEY, then
    GOOGLE_API_KEY, then API_KEY. Preserved exactly for backward
    compatibility with existing local .env files and Render env vars."""
    key = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("API_KEY")
    )
    if key:
        key = key.strip().strip('"').strip("'")
    return key


def _resolve_cors_origins():
    """CORS_ORIGINS unset -> "*" (identical to the previous hardcoded
    behavior). Set to a comma-separated list in .env to restrict origins,
    e.g. CORS_ORIGINS=https://example.com,https://app.example.com"""
    raw = os.getenv("CORS_ORIGINS", "*").strip()
    if raw == "*" or raw == "":
        return "*"
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


class Config:
    # --- AI / Gemini ---
    GEMINI_API_KEY = _resolve_gemini_api_key()
    GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    # --- CORS ---
    CORS_ORIGINS = _resolve_cors_origins()

    # --- Networking ---
    HOST = os.getenv("HOST", "0.0.0.0")
    PORT = int(os.getenv("PORT", 5000))

    # --- Database (Phase 2) ---
    # Expected form: postgresql+psycopg://user:password@host:5432/dbname
    # Left unset by default - the app must remain fully importable and the
    # existing routes must keep working with no database configured at
    # all (see backend/database.py). Never logged/printed anywhere,
    # including in Config.validate() below - only whether it's set.
    DATABASE_URL = os.getenv("DATABASE_URL")

    # --- Email (Phase 4) ---
    # Used by backend/email_service.py. MAIL_PASSWORD is never printed or
    # logged anywhere in this codebase - Config.validate() below only
    # reports whether mail is configured, never any credential value.
    MAIL_HOST = os.getenv("MAIL_HOST")
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
    MAIL_USERNAME = os.getenv("MAIL_USERNAME")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
    MAIL_FROM = os.getenv("MAIL_FROM")
    MAIL_USE_TLS = os.getenv("MAIL_USE_TLS", "true").strip().lower() not in ("false", "0", "no")

    # --- Frontend (Phase 4) ---
    # Used to build verification links (FRONTEND_URL + "/verify-email?token=...").
    # Deliberately not defaulted to localhost/Render - see backend/email_service.py.
    FRONTEND_URL = os.getenv("FRONTEND_URL")

    # --- Sessions / Auth (Phase 5) ---
    # Our actual login-session security does NOT depend on SECRET_KEY -
    # see backend/sessions.py, which uses its own high-entropy random
    # token + server-side hash lookup (same pattern as email verification
    # tokens), independently revocable via the database. SECRET_KEY is
    # still set on the Flask app (app.secret_key) for compatibility with
    # Flask's own session/flash/CSRF-adjacent utilities, per the Phase 5
    # brief. If unset, a random ephemeral key is generated for THIS
    # PROCESS ONLY - never a predictable/hardcoded default - but see the
    # validate() warning below for why that's not suitable for a
    # multi-worker or production deployment.
    _env_secret_key = os.getenv("SECRET_KEY")
    SECRET_KEY = _env_secret_key or secrets.token_hex(32)
    SECRET_KEY_WAS_GENERATED = not bool(_env_secret_key)

    SESSION_COOKIE_NAME = os.getenv("SESSION_COOKIE_NAME", "ppt_session")
    SESSION_LIFETIME_HOURS = int(os.getenv("SESSION_LIFETIME_HOURS", 24 * 7))  # 7 days

    # Defaults are chosen for THIS app's actual deployment shape
    # (frontend and backend on different domains - see
    # CURRENT_ARCHITECTURE.md): a cross-site fetch() with credentials
    # only carries a cookie set with SameSite=None, and SameSite=None
    # requires Secure=True (browsers reject the combination otherwise).
    # For local plain-HTTP development where frontend and backend share
    # an origin/site, override both together in .env:
    #   SESSION_COOKIE_SAMESITE=Lax
    #   SESSION_COOKIE_SECURE=false
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "true").strip().lower() not in ("false", "0", "no")
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "None")

    @classmethod
    def validate(cls):
        """Non-fatal startup checks. Logs actionable warnings but never
        raises/exits - the app must keep starting and degrading gracefully
        with no Gemini key configured, exactly as it did before this file
        existed (chatbot.get_response() already has its own fallback
        message for that case)."""
        warnings = []

        if not cls.GEMINI_API_KEY:
            warnings.append(
                "GEMINI_API_KEY (or GOOGLE_API_KEY / API_KEY) is not set. "
                "The chatbot will run with the Gemini fallback disabled "
                "and return its built-in 'AI assistance is currently "
                "unavailable' message instead."
            )

        if cls.CORS_ORIGINS == "*":
            warnings.append(
                "CORS_ORIGINS is not set - defaulting to '*' (any origin "
                "allowed). Set CORS_ORIGINS in .env to restrict this "
                "before relying on it in production."
            )

        if not cls.DATABASE_URL:
            warnings.append(
                "DATABASE_URL is not set - database features are disabled "
                "for this run. All existing endpoints (/, /health, "
                "/policies, /chat) continue to work from the JSON policy "
                "files as before; set DATABASE_URL in .env "
                "(postgresql+psycopg://user:password@host:5432/dbname) "
                "once a database is available."
            )

        if not cls.MAIL_HOST:
            warnings.append(
                "MAIL_HOST is not set - verification emails cannot be "
                "sent. Registration will still create accounts (per "
                "documented Phase 4 policy), but the email step will "
                "fail until MAIL_HOST/MAIL_USERNAME/MAIL_PASSWORD/"
                "MAIL_FROM are configured in .env."
            )

        if not cls.FRONTEND_URL:
            warnings.append(
                "FRONTEND_URL is not set - verification links cannot be "
                "built. Set FRONTEND_URL in .env (e.g. "
                "https://your-frontend.example) before relying on email "
                "verification."
            )

        if cls.SECRET_KEY_WAS_GENERATED:
            warnings.append(
                "SECRET_KEY is not set - using a random ephemeral key "
                "generated for this process only (never a predictable "
                "default). This does NOT weaken login-session security "
                "(see backend/sessions.py - that's independently backed "
                "by the database), but it does mean the key differs "
                "across restarts and across separate worker processes "
                "(e.g. multiple gunicorn workers), which can affect "
                "Flask-native session/flash features. Set a persistent "
                "SECRET_KEY in .env before running more than one worker "
                "or in production."
            )

        if cls.SESSION_COOKIE_SAMESITE.lower() == "none" and not cls.SESSION_COOKIE_SECURE:
            warnings.append(
                "SESSION_COOKIE_SAMESITE=None requires SESSION_COOKIE_SECURE=true "
                "(browsers reject SameSite=None cookies without Secure) - "
                "the login cookie will silently fail to be set as configured. "
                "Either set SESSION_COOKIE_SECURE=true (recommended, requires "
                "HTTPS) or set SESSION_COOKIE_SAMESITE=Lax for same-site/local "
                "HTTP development."
            )

        if cls.CORS_ORIGINS == "*":
            warnings.append(
                "Because CORS_ORIGINS is '*', credentialed requests (the "
                "login/logout/me endpoints, which rely on cookies) will "
                "NOT enable CORS credentials support - browsers forbid "
                "combining a wildcard origin with credentials. Cross-origin "
                "login will not work until CORS_ORIGINS is set to your "
                "actual frontend origin(s)."
            )

        for warning in warnings:
            print(f"[CONFIG WARNING] {warning}")

        return warnings
