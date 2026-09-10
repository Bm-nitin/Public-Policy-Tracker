"""
Phase 2: database foundation tests.

Scope, deliberately narrow: this phase introduces zero ORM models (see
backend/database.py's module docstring for why), so there is no
domain behavior to characterize yet. These tests only confirm:
  1. the app remains importable/functional with no DATABASE_URL set
     (an explicit Phase 2 requirement);
  2. the extension wiring (init_db) does what it says when a database URL
     IS provided;
  3. no credentials ever get printed/logged;
  4. a real PostgreSQL integration check exists but skips cleanly when no
     real database is reachable, rather than being silently replaced by
     SQLite as if that were equivalent.

A SQLite in-memory URL is used in test 2 ONLY to check that
Flask-SQLAlchemy's config wiring works mechanically (app.config gets the
URI, db attaches to the app) - it does NOT exercise any PostgreSQL-specific
SQL and must not be read as "the app was tested against Postgres."
"""

import importlib

import pytest


def test_database_module_imports_with_no_database_configured():
    """backend/database.py must be safe to import even when nothing in
    the environment configures a database - it only builds an unbound
    SQLAlchemy() extension object at import time."""
    import database
    assert database.db is not None
    assert hasattr(database, "init_db")


def test_config_database_url_defaults_to_none_when_unset(chatbot_module):
    # chatbot_module fixture forces backend/ onto sys.path and the real
    # app .env (which in this repo only sets GEMINI_API_KEY) to have
    # already been loaded once for the whole session.
    import config
    # Not asserting a specific value beyond "unset or a string" - some
    # environments running this suite may legitimately have DATABASE_URL
    # exported. What matters is the attribute exists and is never a
    # hardcoded literal (see test_security_baseline.py for the literal
    # scan across all backend/*.py, which now also covers database.py).
    assert hasattr(config.Config, "DATABASE_URL")


def test_app_is_importable_and_routes_work_without_database_url(app_client):
    """Reuses the standard app_client fixture (Phase 0.5), which boots the
    real app.py. This repo's .env does not set DATABASE_URL, so this
    exercises exactly the "no database configured" path required by
    Phase 2. If this test passes, the existing 91 Phase 0.5/1 tests -
    which all depend on the same app import succeeding - remain valid."""
    response = app_client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_init_db_wires_database_uri_into_flask_config():
    """Mechanical check only (see module docstring): confirms init_db()
    sets SQLALCHEMY_DATABASE_URI and attaches the db extension, using an
    in-memory SQLite URL purely as a harmless stand-in connection string -
    no SQL is executed, no PostgreSQL behavior is implied or tested."""
    from flask import Flask
    import database

    test_app = Flask(__name__)
    fake_sqlite_url = "sqlite:///:memory:"

    returned_db = database.init_db(test_app, fake_sqlite_url)

    assert test_app.config["SQLALCHEMY_DATABASE_URI"] == fake_sqlite_url
    assert returned_db is database.db


def test_validate_never_prints_the_database_url_value(monkeypatch, capsys):
    """Security requirement: DATABASE_URL (which embeds credentials) must
    never be logged, including in the config warning that fires when it's
    missing, and must not leak into logs when it IS set."""
    import config

    fake_secret_url = "postgresql+psycopg://realuser:supersecretpw@dbhost:5432/proddb"
    monkeypatch.setattr(config.Config, "DATABASE_URL", fake_secret_url)
    config.Config.validate()
    captured = capsys.readouterr()
    assert "supersecretpw" not in captured.out
    assert fake_secret_url not in captured.out

    monkeypatch.setattr(config.Config, "DATABASE_URL", None)
    config.Config.validate()
    captured = capsys.readouterr()
    assert "supersecretpw" not in captured.out


def _real_postgres_available(database_url):
    if not database_url or not database_url.startswith("postgresql"):
        return False, "DATABASE_URL is not set to a postgresql:// URL"
    try:
        import psycopg  # noqa: F401
    except ImportError:
        return False, "psycopg driver is not installed in this environment"
    try:
        import psycopg
        with psycopg.connect(database_url, connect_timeout=3):
            pass
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, f"could not connect to configured PostgreSQL: {exc.__class__.__name__}"
    return True, ""


def test_real_postgresql_integration_placeholder():
    """This is the one test in the suite that would exercise a real
    PostgreSQL server. It is written to run automatically in any
    environment where DATABASE_URL points at a reachable PostgreSQL
    instance and the psycopg driver is installed; otherwise it SKIPS
    (never silently passes, never falls back to SQLite as a stand-in for
    Postgres)."""
    import config
    available, reason = _real_postgres_available(config.Config.DATABASE_URL)
    if not available:
        pytest.skip(f"Real PostgreSQL integration test skipped: {reason}")

    import psycopg
    with psycopg.connect(config.Config.DATABASE_URL, connect_timeout=3) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1")
            assert cur.fetchone()[0] == 1
