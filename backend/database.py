"""
Phase 2: PostgreSQL database foundation.

This module introduces exactly one thing: a Flask-SQLAlchemy extension
object and a small helper to wire it into the Flask app when (and only
when) a database is actually configured.

No models are defined here or anywhere else in this phase. Objectives 8-12
of Phase 2 explicitly forbid users, chat history, saved policies, and any
auth/email/token/OTP storage, and objective 7 forbids migrating the policy
JSON into the database. That leaves nothing that currently needs a table -
so the schema this phase introduces is intentionally empty; only the
migration-tracking infrastructure (see migrations/) is real. A future
phase can add SQLAlchemy model classes to this file (or a new models.py)
once there's an actual feature that needs one - see
"Do not blindly create the entire future architecture" in the Phase 2
brief.

Import safety (required by Phase 2 objectives):
- `db = SQLAlchemy()` merely constructs an (unbound) extension object. It
  does not open a connection, read config, or touch the network - so this
  module, and anything that imports it, is always safe to import with no
  DATABASE_URL configured at all.
- `init_db(app, database_url)` is the only thing that actually wires a
  connection string into the app. app.py only calls it when
  Config.DATABASE_URL is set (see app.py) - if it isn't, `db` stays
  attached to no app and unused, and the rest of the application
  (chatbot, policy loader, existing routes) works exactly as it did in
  Phase 0/0.5/1. Calling db.session or a query without configuring the
  database first raises a clear, immediate Flask-SQLAlchemy error
  ("... application not initialized" / RuntimeError) rather than a
  confusing import-time traceback, satisfying the Phase 2 requirement for
  "a clear configuration error or controlled behavior."
"""

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


def init_db(app, database_url):
    """Attach the SQLAlchemy extension to the Flask app using the given
    connection string. Only call this when database_url is truthy - see
    app.py, which checks Config.DATABASE_URL before calling this."""
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", database_url)
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    db.init_app(app)
    return db
