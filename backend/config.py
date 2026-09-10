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

        for warning in warnings:
            print(f"[CONFIG WARNING] {warning}")

        return warnings
