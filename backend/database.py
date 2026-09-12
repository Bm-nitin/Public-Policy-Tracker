"""
Phase 2: PostgreSQL database foundation.
Phase 7.5: Flask-Migrate CLI initialization fix.

This module introduces the Flask-SQLAlchemy extension object and (as of
the Phase 7.5 fix below) the Flask-Migrate extension object, plus a small
helper to wire both into the Flask app when (and only when) a database is
actually configured.

Root cause this fixes: migrations/env.py has always referenced
`current_app.extensions['migrate']` (the standard Flask-Migrate env.py
template - see that file), but nothing ever called
`Migrate().init_app(app, db)` to populate that extensions entry or
register the `flask db ...` CLI command group in the first place. That is
the exact reason `flask --app app db --help` reported "No such command
'db'" - Flask-SQLAlchemy alone does not register any CLI commands;
Flask-Migrate is what does, and it was installed (requirements.txt) but
never initialized.

Import safety (unchanged from Phase 2, still required):
- `db = SQLAlchemy()` and `migrate = Migrate()` merely construct unbound
  extension objects. Neither opens a connection, reads config, or
  touches the network - so this module, and anything that imports it, is
  always safe to import with no DATABASE_URL configured at all.
- `init_db(app, database_url)` is the only thing that actually wires a
  connection string into the app and initializes both extensions against
  it. app.py only calls it when Config.DATABASE_URL is set - if it
  isn't, neither `db` nor `migrate` is attached to any app, and the rest
  of the application (chatbot, policy loader, existing routes) works
  exactly as it did before this fix. This also means the `flask db ...`
  CLI commands only become available once DATABASE_URL is configured -
  consistent with every other database-dependent behavior in this app.
"""

from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()


def init_db(app, database_url):
    """Attach the SQLAlchemy and Migrate extensions to the Flask app
    using the given connection string. Only call this when database_url
    is truthy - see app.py, which checks Config.DATABASE_URL before
    calling this."""
    app.config.setdefault("SQLALCHEMY_DATABASE_URI", database_url)
    app.config.setdefault("SQLALCHEMY_TRACK_MODIFICATIONS", False)
    db.init_app(app)
    migrate.init_app(app, db)
    return db
