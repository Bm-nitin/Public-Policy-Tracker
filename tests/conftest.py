"""
Shared pytest fixtures for the Phase 0.5 characterization/regression suite.

Design notes
------------
- backend/*.py uses flat imports (`from chatbot import get_response`, etc.),
  so the `backend/` directory must be on sys.path before those modules are
  imported. We do that here, once, for the whole test session.

- These tests must run without network access and without calling the real
  Gemini API (see task requirements). Every test that reaches the Gemini
  code path monkeypatches `chatbot.client` / `chatbot.call_generative_ai`
  directly - real network calls are never made from this suite.

- OFFLINE SANDBOX SHIM (read this before deleting it):
  `flask-cors` and `google-genai` are listed in requirements.txt but may not
  be installed in every environment this suite runs in (e.g. a network-
  restricted CI sandbox used to author/validate this suite). The try/except
  blocks below only install a minimal fake module into sys.modules, and only
  if the real package import fails. In any environment where
  `pip install -r requirements.txt` has actually been run (local dev,
  Render, a normal CI runner), the real packages import successfully and
  these shims are never touched. They exist purely so `pytest` can *collect
  and run* this suite in a bare sandbox - they do not change, wrap, or
  monkeypatch any backend/*.py source file.
"""

import os
import sys
import types

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
DATA_DIR = os.path.join(ROOT_DIR, "data")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# --- flask_cors shim (only if the real package isn't installed) -----------
try:
    import flask_cors  # noqa: F401
except ImportError:
    _fake_flask_cors = types.ModuleType("flask_cors")

    def _fake_cors(app, *args, **kwargs):
        return app

    _fake_flask_cors.CORS = _fake_cors
    sys.modules["flask_cors"] = _fake_flask_cors


# --- google-genai shim (only if the real package isn't installed) ---------
try:
    from google import genai  # noqa: F401
except ImportError:
    _fake_google = sys.modules.get("google") or types.ModuleType("google")
    _fake_genai = types.ModuleType("google.genai")

    class _OfflineFakeModels:
        def generate_content(self, model=None, contents=None):
            raise RuntimeError(
                "A real Gemini call was attempted through the offline test "
                "shim. Every test that reaches call_generative_ai() must "
                "monkeypatch chatbot.client (or chatbot.call_generative_ai "
                "directly) - see test_gemini_mocked.py for the pattern."
            )

    class _OfflineFakeClient:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.models = _OfflineFakeModels()

    _fake_genai.Client = _OfflineFakeClient
    _fake_google.genai = _fake_genai
    sys.modules["google"] = _fake_google
    sys.modules["google.genai"] = _fake_genai


# --- shared expression helpers (used by both shims below) ----------------
# Minimal stand-in for a real SQLAlchemy ColumnOperators expression (e.g.
# what Policy.name.ilike(...) actually returns) - a parameterized SQL
# fragment plus its bound value(s), combinable via OR. sqlite has no
# ILIKE, so column.ilike(pattern) (see _ShimColumn below) compiles to
# LOWER(col) LIKE LOWER(?) - a portable, correct case-insensitive
# substring match, genuinely parameterized (the pattern is always a bound
# parameter, never string-interpolated into the SQL fragment itself).
class _ShimBinaryExpression:
    def __init__(self, sql, params):
        self.sql = sql
        self.params = list(params)


def _shim_or(*expressions):
    expressions = [e for e in expressions if e is not None]
    if not expressions:
        return _ShimBinaryExpression("1=0", [])
    combined_sql = " OR ".join(f"({e.sql})" for e in expressions)
    combined_params = []
    for e in expressions:
        combined_params.extend(e.params)
    return _ShimBinaryExpression(combined_sql, combined_params)


# --- sqlalchemy shim (only if the real package isn't installed) ---------
# Only the one thing auth_routes.py imports directly: the IntegrityError
# exception class. Defined first so the flask_sqlalchemy shim below can
# raise this exact class.
try:
    import sqlalchemy  # noqa: F401
    from sqlalchemy.exc import IntegrityError as _RealIntegrityError  # noqa: F401
    _ShimIntegrityError = _RealIntegrityError
except ImportError:
    _fake_sqlalchemy = types.ModuleType("sqlalchemy")
    _fake_sqlalchemy_exc = types.ModuleType("sqlalchemy.exc")

    class _ShimIntegrityError(Exception):
        pass

    _fake_sqlalchemy_exc.IntegrityError = _ShimIntegrityError
    _fake_sqlalchemy.exc = _fake_sqlalchemy_exc
    # Phase 9: backend/retrieval.py uses `from sqlalchemy import or_` to
    # combine multiple ilike() conditions into one parameterized query.
    _fake_sqlalchemy.or_ = _shim_or
    sys.modules["sqlalchemy"] = _fake_sqlalchemy
    sys.modules["sqlalchemy.exc"] = _fake_sqlalchemy_exc


# --- flask_sqlalchemy shim (only if the real package isn't installed) ----
# IMPORTANT LIMITATION: unlike the flask_cors/google-genai shims above,
# this one does not reimplement SQLAlchemy's query builder, relationships,
# migrations, or dialect handling. What it DOES do, honestly: back
# db.Model/db.Column/db.session with Python's real stdlib sqlite3, so
# INSERTs, the UNIQUE constraint on users.email, and SELECT-by-filter
# genuinely execute as real SQL against a real (in-memory) SQL engine -
# not simulated in Python. This is enough to meaningfully exercise
# backend/models.py and backend/auth_routes.py's actual logic in this
# offline sandbox. It is NOT PostgreSQL and does not validate
# PostgreSQL-specific behavior (dialect-specific errors, concurrent
# transaction semantics, server-side defaults) - see test_database.py's
# existing caveins from Phase 2, which still apply. Any environment with
# the real flask-sqlalchemy package installed ignores this shim entirely.
try:
    import flask_sqlalchemy  # noqa: F401
except ImportError:
    import sqlite3 as _sqlite3
    from datetime import datetime as _datetime

    _fake_flask_sqlalchemy = types.ModuleType("flask_sqlalchemy")

    class _ShimColType:
        def __init__(self, sql_type, length=None):
            self.sql_type = sql_type  # "INTEGER" | "BIGINT" | "TEXT" | "BOOL" | "DATETIME"
            self.length = length

        def __call__(self, *args, **kwargs):
            length = kwargs.get("length")
            if args and isinstance(args[0], int):
                length = args[0]
            return _ShimColType(self.sql_type, length=length)

        def with_variant(self, other_type, dialect_name):
            """Mirrors real SQLAlchemy's Type.with_variant(): picks a
            different type for a specific dialect. This shim's physical
            backend is always sqlite, so a "sqlite" variant always
            applies here - exactly what would genuinely happen against a
            real sqlite connection too (this isn't a shim-only
            simplification, it's the real, documented behavior)."""
            if dialect_name == "sqlite":
                return other_type
            return self

    class _ShimColumn:
        def __init__(self, col_type, primary_key=False, unique=False,
                     nullable=True, default=None, onupdate=None, index=False):
            self.col_type = col_type
            self.primary_key = primary_key
            self.unique = unique
            # A primary key is implicitly NOT NULL, same as real
            # SQLAlchemy/PostgreSQL - not something the caller needs to
            # (or usually does) specify explicitly.
            self.nullable = False if primary_key else nullable
            self.default = default
            self.onupdate = onupdate
            self.index = index
            self.name = None

        def ilike(self, pattern):
            """Real SQLAlchemy: Policy.name.ilike('%token%') - a
            case-insensitive substring match, parameterized (the pattern
            is bound, never interpolated into the SQL string itself)."""
            return _ShimBinaryExpression(f"LOWER({self.name}) LIKE LOWER(?)", [pattern])

    _MODEL_REGISTRY = []

    class _ShimQuery:
        def __init__(self, model_cls, conn, filters=None,
                     order_by_col=None, limit_n=None, offset_n=None,
                     raw_filter=None):
            self.model_cls = model_cls
            self.conn = conn
            self.filters = dict(filters) if filters else {}
            self.order_by_col = order_by_col
            self.limit_n = limit_n
            self.offset_n = offset_n
            self.raw_filter = raw_filter

        def _clone(self, **overrides):
            kwargs = dict(
                filters=self.filters, order_by_col=self.order_by_col,
                limit_n=self.limit_n, offset_n=self.offset_n,
                raw_filter=self.raw_filter,
            )
            kwargs.update(overrides)
            return _ShimQuery(self.model_cls, self.conn, **kwargs)

        def filter_by(self, **kwargs):
            new_filters = dict(self.filters)
            new_filters.update(kwargs)
            return self._clone(filters=new_filters)

        def filter(self, expression):
            """Real SQLAlchemy: Query.filter(<expression>) - accepts a
            compound expression (e.g. or_(Policy.name.ilike(...), ...)),
            combined with any existing .filter_by() equality filters via
            AND. Used by backend/retrieval.py's candidate query, which
            needs an OR across several columns - something .filter_by()
            alone (AND-only equality) can't express."""
            if self.raw_filter is not None:
                combined = _ShimBinaryExpression(
                    f"({self.raw_filter.sql}) AND ({expression.sql})",
                    self.raw_filter.params + expression.params,
                )
            else:
                combined = expression
            return self._clone(raw_filter=combined)

        def order_by(self, column):
            col_name = getattr(column, "name", None) or str(column)
            return self._clone(order_by_col=col_name)

        def limit(self, n):
            return self._clone(limit_n=n)

        def offset(self, n):
            return self._clone(offset_n=n)

        def _where_clause(self):
            """Builds a WHERE clause + bound params, treating a None
            filter value as IS NULL rather than '= ?' - real SQLAlchemy's
            filter_by(col=None) does the same (a bound '= NULL' parameter
            would never match anything in standard SQL, including
            SQLite/Postgres, since NULL = NULL is unknown, not true).
            Combines equality filters (.filter_by()) with any compound
            expression (.filter()) via AND."""
            clauses, params = [], []
            for key, value in self.filters.items():
                if value is None:
                    clauses.append(f"{key} IS NULL")
                else:
                    clauses.append(f"{key} = ?")
                    params.append(value)
            if self.raw_filter is not None:
                clauses.append(f"({self.raw_filter.sql})")
                params.extend(self.raw_filter.params)
            return (" AND ".join(clauses) or "1=1"), params

        def _execute(self):
            cols = list(self.model_cls.__columns__.keys())
            where_sql, params = self._where_clause()
            sql = (
                f"SELECT {','.join(cols)} FROM {self.model_cls.__tablename__} "
                f"WHERE {where_sql}"
            )
            if self.order_by_col:
                sql += f" ORDER BY {self.order_by_col}"
            if self.limit_n is not None:
                sql += f" LIMIT {int(self.limit_n)}"
                if self.offset_n is not None:
                    sql += f" OFFSET {int(self.offset_n)}"
            cur = self.conn.execute(sql, params)
            rows = cur.fetchall()
            results = []
            for row in rows:
                obj = self.model_cls.__new__(self.model_cls)
                for col_name, value in zip(cols, row):
                    col_type = self.model_cls.__columns__[col_name].col_type.sql_type
                    if col_type == "BOOL" and value is not None:
                        value = bool(value)
                    elif col_type == "DATETIME" and isinstance(value, str):
                        value = _datetime.fromisoformat(value)
                    setattr(obj, col_name, value)
                results.append(obj)
            return results

        def first(self):
            results = self._execute()
            return results[0] if results else None

        def all(self):
            return self._execute()

        def count(self):
            where_sql, params = self._where_clause()
            cur = self.conn.execute(
                f"SELECT COUNT(*) FROM {self.model_cls.__tablename__} WHERE {where_sql}",
                params,
            )
            return cur.fetchone()[0]

        def update(self, values):
            """Bulk conditional UPDATE, mirroring real SQLAlchemy's
            Query.update({...}): executes an UPDATE ... WHERE <filters>
            immediately (within the current sqlite3 transaction - not
            committed here, same as real SQLAlchemy, which still needs an
            explicit session.commit() afterward) and returns the number
            of rows actually changed. This is what makes atomic
            conditional consumption possible (see reset_password() in
            auth_routes.py): filter_by(id=..., used_at=None).update(...)
            only affects a row if used_at is STILL NULL at the moment the
            UPDATE runs, closing the check-then-write race a separate
            SELECT-then-UPDATE pair would leave open."""
            cols = self.model_cls.__columns__
            set_clauses, set_params = [], []
            for key, value in values.items():
                col = cols.get(key)
                if col is not None and col.col_type.sql_type == "BOOL" and value is not None:
                    value = 1 if value else 0
                elif hasattr(value, "isoformat"):
                    value = value.isoformat()
                set_clauses.append(f"{key} = ?")
                set_params.append(value)

            where_sql, where_params = self._where_clause()
            cur = self.conn.execute(
                f"UPDATE {self.model_cls.__tablename__} SET {', '.join(set_clauses)} "
                f"WHERE {where_sql}",
                set_params + where_params,
            )
            return cur.rowcount

    class _ShimQueryDescriptor:
        def __get__(self, instance, owner):
            db_instance = owner.__db__
            if db_instance._conn is None:
                raise RuntimeError(
                    "Database not initialized - call init_db(app, url) "
                    "(or db.init_app in a real app context) before "
                    "querying."
                )
            return _ShimQuery(owner, db_instance._conn)

    class _ShimUniqueConstraint:
        """Minimal stand-in for sqlalchemy.UniqueConstraint, sufficient
        for a composite UNIQUE(*columns) - used by Policy's (name,
        sector) constraint (Phase 7). Genuinely enforced (see
        create_all() below), not just accepted-and-ignored - Phase 7's
        idempotent-import tests rely on the real database rejecting a
        true duplicate (name, sector) pair."""
        def __init__(self, *columns, name=None):
            self.columns = columns
            self.name = name

    class _ShimTableColumnType:
        """Minimal stand-in for a SQLAlchemy type object - just enough
        that str(column.type) gives a recognizable name, matching what
        real introspection code (e.g. `str(c.type)`) expects."""
        _DISPLAY_NAMES = {
            "INTEGER": "INTEGER", "BIGINT": "BIGINT", "BOOL": "BOOLEAN",
            "DATETIME": "DATETIME",
        }

        def __init__(self, sql_type, length=None):
            self.sql_type = sql_type
            self.length = length

        def __str__(self):
            if self.sql_type == "TEXT":
                return f"VARCHAR({self.length})" if self.length else "TEXT"
            return self._DISPLAY_NAMES.get(self.sql_type, self.sql_type)

    class _ShimTableColumn:
        """Minimal stand-in for a real SQLAlchemy Column as exposed via
        Model.__table__.columns - name/type/nullable/index, the exact
        surface real introspection scripts read."""
        def __init__(self, shim_column):
            self.name = shim_column.name
            self.nullable = shim_column.nullable
            self.index = bool(shim_column.index)
            self.type = _ShimTableColumnType(
                shim_column.col_type.sql_type, shim_column.col_type.length
            )

    class _ShimColumnCollection(list):
        """Minimal stand-in for SQLAlchemy's ColumnCollection. A plain
        list's `in`/`[]` only work by object identity/equality, so
        `"source_file" in [...]` would silently always be False (a
        string never equals a column object) regardless of whether the
        column actually exists - which would make a real check like
        `"source_json" not in Policy.__table__.columns` trivially true
        for the wrong reason. This collection checks/indexes by column
        NAME when given a string, matching real SQLAlchemy's actual
        `col_name in table.columns` / `table.columns[col_name]` usage."""

        def __contains__(self, item):
            if isinstance(item, str):
                return any(col.name == item for col in self)
            return super().__contains__(item)

        def __getitem__(self, key):
            if isinstance(key, str):
                for col in self:
                    if col.name == key:
                        return col
                raise KeyError(key)
            return super().__getitem__(key)

        def keys(self):
            return [col.name for col in self]

    class _ShimTable:
        def __init__(self, shim_columns_dict):
            self.columns = _ShimColumnCollection(
                _ShimTableColumn(col) for col in shim_columns_dict.values()
            )

    class _ShimModelMeta(type):
        def __new__(mcs, name, bases, namespace):
            columns = {}
            for key, value in list(namespace.items()):
                if isinstance(value, _ShimColumn):
                    value.name = key
                    columns[key] = value
            cls = super().__new__(mcs, name, bases, namespace)
            cls.__columns__ = columns
            cls.__table_args_shim__ = namespace.get("__table_args__", ())
            if namespace.get("__tablename__"):
                cls.__table__ = _ShimTable(columns)
                _MODEL_REGISTRY.append(cls)
            return cls

    def _make_model_base(db_instance):
        class _ShimModel(metaclass=_ShimModelMeta):
            __db__ = db_instance
            query = _ShimQueryDescriptor()

            def __init__(self, **kwargs):
                for col_name in self.__columns__:
                    setattr(self, col_name, kwargs.get(col_name))

        return _ShimModel

    class _ShimSession:
        def __init__(self, db_instance):
            self._db = db_instance
            self._pending = []

        def add(self, obj):
            self._pending.append(obj)

        def _insert_pending(self):
            """Executes INSERT for new objects (no primary key yet) and
            UPDATE for already-persisted objects (primary key already
            set - e.g. an object just returned by .query.filter_by()
            that the route then mutates and re-adds), matching real
            SQLAlchemy's unit-of-work behavior closely enough for this
            app's actual usage. Does NOT commit the underlying sqlite
            transaction - used by both flush() (assign PKs, keep
            transaction open) and commit() (flush, then finalize)."""
            conn = self._db._conn
            for obj in self._pending:
                cls = type(obj)
                pk_col_name = next(
                    (name for name, col in cls.__columns__.items() if col.primary_key),
                    None,
                )
                pk_value = getattr(obj, pk_col_name, None) if pk_col_name else None

                if pk_value is not None:
                    # UPDATE: object already has a primary key, so it was
                    # already inserted (e.g. loaded via .query) - this is
                    # a mutation, not a new row.
                    set_clauses, set_vals = [], []
                    for col_name, col in cls.__columns__.items():
                        if col.primary_key:
                            continue
                        value = getattr(obj, col_name, None)
                        if col.onupdate is not None:
                            value = col.onupdate() if callable(col.onupdate) else col.onupdate
                            setattr(obj, col_name, value)
                        if col.col_type.sql_type == "BOOL" and value is not None:
                            value = 1 if value else 0
                        elif hasattr(value, "isoformat"):
                            value = value.isoformat()
                        set_clauses.append(f"{col_name} = ?")
                        set_vals.append(value)
                    set_vals.append(pk_value)
                    conn.execute(
                        f"UPDATE {cls.__tablename__} SET {', '.join(set_clauses)} "
                        f"WHERE {pk_col_name} = ?",
                        set_vals,
                    )
                    continue

                # INSERT: brand new object, no primary key yet.
                insert_cols, insert_vals = [], []
                for col_name, col in cls.__columns__.items():
                    if col.primary_key:
                        continue
                    value = getattr(obj, col_name, None)
                    if value is None and col.default is not None:
                        value = col.default() if callable(col.default) else col.default
                        setattr(obj, col_name, value)
                    if col.col_type.sql_type == "BOOL" and value is not None:
                        value = 1 if value else 0
                    elif hasattr(value, "isoformat"):
                        value = value.isoformat()
                    insert_cols.append(col_name)
                    insert_vals.append(value)
                placeholders = ",".join("?" for _ in insert_cols)
                cur = conn.execute(
                    f"INSERT INTO {cls.__tablename__} "
                    f"({','.join(insert_cols)}) VALUES ({placeholders})",
                    insert_vals,
                )
                if pk_col_name:
                    setattr(obj, pk_col_name, cur.lastrowid)
            self._pending = []

        def flush(self):
            try:
                self._insert_pending()
            except _sqlite3.IntegrityError as exc:
                raise _ShimIntegrityError(str(exc)) from exc

        def commit(self):
            try:
                self._insert_pending()
                self._db._conn.commit()
            except _sqlite3.IntegrityError as exc:
                raise _ShimIntegrityError(str(exc)) from exc

        def rollback(self):
            self._pending = []
            self._db._conn.rollback()

    class _OfflineFakeSQLAlchemy:
        def __init__(self, *args, **kwargs):
            self._conn = None
            self.session = None
            self.Column = _ShimColumn
            self.Integer = _ShimColType("INTEGER")
            self.BigInteger = _ShimColType("BIGINT")
            self.String = lambda length=None: _ShimColType("TEXT", length=length)
            self.Text = _ShimColType("TEXT")
            self.Boolean = _ShimColType("BOOL")
            self.DateTime = lambda timezone=False: _ShimColType("DATETIME")
            self.UniqueConstraint = _ShimUniqueConstraint
            self.Model = _make_model_base(self)

        def init_app(self, app):
            # This offline shim only supports sqlite (any URI is treated
            # as "give me a fresh isolated in-memory database") - real
            # flask-sqlalchemy would actually connect to the configured
            # PostgreSQL URI here.
            self._conn = _sqlite3.connect(":memory:", check_same_thread=False)
            self.session = _ShimSession(self)
            app.extensions = getattr(app, "extensions", {})
            app.extensions["sqlalchemy"] = self

        def create_all(self):
            for cls in _MODEL_REGISTRY:
                col_defs = []
                for col_name, col in cls.__columns__.items():
                    sql_type = {
                        "INTEGER": "INTEGER", "BIGINT": "INTEGER", "TEXT": "TEXT", "BOOL": "INTEGER",
                        "DATETIME": "TEXT",
                    }[col.col_type.sql_type]
                    parts = [col_name, sql_type]
                    if col.primary_key:
                        parts.append("PRIMARY KEY AUTOINCREMENT")
                    else:
                        if not col.nullable:
                            parts.append("NOT NULL")
                        if col.unique:
                            parts.append("UNIQUE")
                    col_defs.append(" ".join(parts))

                table_constraints = []
                for arg in getattr(cls, "__table_args_shim__", ()):
                    if isinstance(arg, _ShimUniqueConstraint):
                        table_constraints.append(
                            f"UNIQUE ({', '.join(arg.columns)})"
                        )

                self._conn.execute(
                    f"CREATE TABLE IF NOT EXISTS {cls.__tablename__} "
                    f"({', '.join(col_defs + table_constraints)})"
                )
                for col_name, col in cls.__columns__.items():
                    # A UNIQUE column already gets an implicit index from
                    # sqlite; only add an explicit one for plain
                    # index=True, non-unique, non-PK columns (e.g.
                    # email_verification_tokens.user_id).
                    if col.index and not col.unique and not col.primary_key:
                        self._conn.execute(
                            f"CREATE INDEX IF NOT EXISTS ix_{cls.__tablename__}_{col_name} "
                            f"ON {cls.__tablename__} ({col_name})"
                        )
            self._conn.commit()

        def drop_all(self):
            for cls in _MODEL_REGISTRY:
                self._conn.execute(f"DROP TABLE IF EXISTS {cls.__tablename__}")
            self._conn.commit()

    _fake_flask_sqlalchemy.SQLAlchemy = _OfflineFakeSQLAlchemy
    sys.modules["flask_sqlalchemy"] = _fake_flask_sqlalchemy


# --- flask_migrate shim (only if the real package isn't installed) -------
# IMPORTANT SCOPE: this genuinely exercises the actual mechanism that was
# broken (Migrate.init_app() must call app.cli.add_command() so `flask db
# ...` exists at all) using a real Click command group - it does NOT
# simulate Alembic's actual upgrade/downgrade/revision logic, which
# requires the real alembic + flask-migrate packages and a real database
# and is never invoked by this shim or by any test. Its subcommands only
# echo a notice; they do not touch migrations/env.py or any database.
try:
    import flask_migrate  # noqa: F401
except ImportError:
    import click as _click

    _fake_flask_migrate = types.ModuleType("flask_migrate")

    @_click.group(name="db", help="Perform database migrations (offline shim - see tests/conftest.py).")
    def _shim_db_cli():
        pass

    for _cmd_name in ("init", "migrate", "revision", "upgrade", "downgrade",
                      "current", "history", "heads", "stamp", "show", "check"):
        def _make_cmd(name):
            @_click.command(name=name)
            def _cmd(*args, **kwargs):
                _click.echo(
                    f"(offline shim) 'flask db {name}' is not implemented here - "
                    f"this only proves the command is registered. Run this for "
                    f"real with flask-migrate installed and a real DATABASE_URL."
                )
            return _cmd
        _shim_db_cli.add_command(_make_cmd(_cmd_name))

    class _ShimMigrate:
        def __init__(self, app=None, db=None, **kwargs):
            self.db = db
            if app is not None and db is not None:
                self.init_app(app, db, **kwargs)

        def init_app(self, app, db=None, directory="migrations", **kwargs):
            self.db = db or self.db
            app.extensions = getattr(app, "extensions", {})
            app.extensions["migrate"] = self
            # This is the actual line that was missing in the real bug:
            # without it, `flask --app app db --help` has no "db" command
            # at all, exactly the reported symptom.
            app.cli.add_command(_shim_db_cli)

    _fake_flask_migrate.Migrate = _ShimMigrate
    sys.modules["flask_migrate"] = _fake_flask_migrate


# --- argon2-cffi shim (only if the real package isn't installed) ---------
# SANDBOX-ONLY SUBSTITUTE, NOT PRODUCTION-EQUIVALENT: this shim does NOT
# use Argon2id (it can't - the real algorithm isn't installable here with
# no network access). Instead it uses Python's real stdlib
# hashlib.pbkdf2_hmac, which is a genuine, correctly-implemented salted
# password hash (unique random salt per call, non-reversible, real
# constant-time comparison on verify) - just a different algorithm than
# Argon2id. This lets security.py's actual hashing *contract* (unique
# salt each time, can't recover the plaintext, correct password verifies,
# wrong password doesn't) be genuinely exercised here, rather than faked.
# It must never be mistaken for validating Argon2id specifically -
# requirements.txt pins the real argon2-cffi package for production.
try:
    import argon2  # noqa: F401
    from argon2 import exceptions as _argon2_exceptions  # noqa: F401
except ImportError:
    import base64 as _base64
    import hashlib as _hashlib
    import hmac as _hmac
    import os as _os

    _fake_argon2 = types.ModuleType("argon2")
    _fake_argon2_exceptions = types.ModuleType("argon2.exceptions")

    class VerifyMismatchError(Exception):
        pass

    class VerificationError(Exception):
        pass

    class InvalidHash(Exception):
        pass

    class _OfflinePasswordHasher:
        _PREFIX = "sandboxpbkdf2$"
        _ITERATIONS = 260_000

        def hash(self, password):
            salt = _os.urandom(16)
            digest = _hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, self._ITERATIONS
            )
            return (
                self._PREFIX
                + _base64.b64encode(salt).decode()
                + "$"
                + _base64.b64encode(digest).decode()
            )

        def verify(self, hash_str, password):
            if not isinstance(hash_str, str) or not hash_str.startswith(self._PREFIX):
                raise InvalidHash("not a recognizable sandbox pbkdf2 hash")
            try:
                _, salt_b64, digest_b64 = hash_str.split("$")
                salt = _base64.b64decode(salt_b64)
                expected = _base64.b64decode(digest_b64)
            except (ValueError, Exception) as exc:
                raise InvalidHash(f"malformed hash: {exc}") from exc
            actual = _hashlib.pbkdf2_hmac(
                "sha256", password.encode("utf-8"), salt, self._ITERATIONS
            )
            if not _hmac.compare_digest(actual, expected):
                raise VerifyMismatchError("password does not match")
            return True

    _fake_argon2.PasswordHasher = _OfflinePasswordHasher
    _fake_argon2_exceptions.VerifyMismatchError = VerifyMismatchError
    _fake_argon2_exceptions.VerificationError = VerificationError
    _fake_argon2_exceptions.InvalidHash = InvalidHash
    _fake_argon2.exceptions = _fake_argon2_exceptions
    sys.modules["argon2"] = _fake_argon2
    sys.modules["argon2.exceptions"] = _fake_argon2_exceptions


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def utils_module():
    import utils
    return utils


@pytest.fixture(scope="session")
def policy_loader_module():
    import policy_loader
    return policy_loader


@pytest.fixture(scope="session")
def chatbot_module():
    import chatbot
    return chatbot


@pytest.fixture(scope="session")
def flask_app_module():
    import app
    app.app.testing = True
    return app


@pytest.fixture
def app_client(flask_app_module):
    with flask_app_module.app.test_client() as client:
        yield client


@pytest.fixture
def no_db_client(flask_app_module, monkeypatch):
    """Test client for exercising the "no database configured" guard
    specifically - genuinely isolated from whatever the real .env
    actually contains, rather than relying on the developer's local
    environment happening to have DATABASE_URL unset (which is no longer
    a safe assumption once a real DATABASE_URL is configured for
    Phase 7+ work).

    Reuses the same shared app object app_client does (there's no
    create_app() factory in this codebase to build a fresh app per test
    - see backend/app.py), but forces config.Config.DATABASE_URL to None
    for the duration of the test via monkeypatch, which auto-reverts
    after the test regardless of pass/fail. This works because every
    route's "is a database configured at all" guard
    (`if not Config.DATABASE_URL: return ..., 503`) reads Config.DATABASE_URL
    fresh at request time - it is never cached or baked into the Flask
    app object at creation/import time - so patching the Config class
    attribute is sufficient on its own; no new Flask app, no re-running
    init_db(), no blueprint re-registration needed.

    Do NOT use this fixture for anything except a route's explicit
    "database not configured" guard test - every other test should keep
    using app_client (real .env) or db_test_app/registration_client/etc.
    (isolated in-memory test database) as appropriate, per the Phase 8.5
    fix's explicit requirement that normal tests remain unchanged."""
    import config
    monkeypatch.setattr(config.Config, "DATABASE_URL", None)
    with flask_app_module.app.test_client() as client:
        yield client


@pytest.fixture
def reset_policy_cache(policy_loader_module):
    """Ensure load_policies() re-reads from disk instead of returning the
    module-level cache. Restores the real cached data afterwards so later
    tests (and the chatbot module, which loaded it once at import time)
    keep working against the real dataset."""
    original_cache = policy_loader_module._cached_policies
    policy_loader_module._cached_policies = None
    yield
    policy_loader_module._cached_policies = original_cache


@pytest.fixture
def real_data_dir():
    return DATA_DIR


@pytest.fixture
def mock_gemini_client(monkeypatch, chatbot_module):
    """Factory fixture: call with the text you want generate_content() to
    return (or an exception instance to raise it instead)."""

    def _install(reply_text=None, raise_exc=None):
        class _FakeResponse:
            text = reply_text

        class _FakeModels:
            def generate_content(self, model=None, contents=None):
                if raise_exc is not None:
                    raise raise_exc
                return _FakeResponse()

        class _FakeClient:
            def __init__(self):
                self.models = _FakeModels()

        monkeypatch.setattr(chatbot_module, "client", _FakeClient())
        monkeypatch.setattr(chatbot_module, "API_KEY", "test-key-not-real")

    return _install


# --- Phase 3 fixtures -------------------------------------------------------

@pytest.fixture
def db_test_app():
    """A fresh Flask app + isolated in-memory database for a single test.

    Using a brand new Flask app (rather than the shared flask_app_module
    used by app_client) keeps each registration test's data completely
    isolated from every other test - no shared users table, no ordering
    dependence between tests.

    See the flask_sqlalchemy shim's docstring above for exactly what is
    and is not validated when this runs against the offline sandbox
    shim vs. a real installed flask-sqlalchemy + real database.
    """
    from flask import Flask

    import database
    import models  # noqa: F401 - import needed so User is registered

    test_app = Flask(__name__)
    test_app.config["TESTING"] = True
    database.init_db(test_app, "sqlite:///:memory:")
    with test_app.app_context():
        database.db.create_all()

    yield test_app

    with test_app.app_context():
        database.db.drop_all()


@pytest.fixture
def registration_client(db_test_app, monkeypatch):
    """Test client for POST /api/auth/register (and the Phase 4
    verify-email/resend-verification routes on the same blueprint) wired
    to db_test_app's isolated database. Monkeypatches Config.DATABASE_URL
    and Config.FRONTEND_URL to test values so the route's guards pass and
    a verification link can be built - the route checks the shared
    Config object regardless of which Flask app instance is handling the
    request."""
    import config
    from auth_routes import auth_bp

    monkeypatch.setattr(config.Config, "DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(config.Config, "FRONTEND_URL", "https://test.example")
    db_test_app.register_blueprint(auth_bp)

    with db_test_app.test_client() as client:
        yield client


@pytest.fixture
def policies_client(db_test_app, monkeypatch):
    """Test client for GET /api/policies* (Phase 8), wired to
    db_test_app's isolated database and pre-populated with the REAL
    151-record dataset via the actual import pipeline
    (import_policies_from_json against the real data/ directory) - not
    synthetic test fixtures, so these tests exercise the real data shape
    end to end."""
    import os

    import config
    from import_policies import import_policies_from_json
    from policies_routes import policies_bp

    monkeypatch.setattr(config.Config, "DATABASE_URL", "sqlite:///:memory:")
    db_test_app.register_blueprint(policies_bp)

    real_data_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    with db_test_app.app_context():
        report = import_policies_from_json(real_data_dir)
        assert report["aborted"] is False

    with db_test_app.test_client() as client:
        yield client


@pytest.fixture
def populated_db_app(db_test_app):
    """db_test_app pre-populated with the REAL 151-record dataset via the
    actual import pipeline - used by Phase 9 retrieval tests that call
    backend/retrieval.py's functions directly (not through HTTP), so they
    need an app context to query through but not a Flask test client."""
    import os

    from import_policies import import_policies_from_json

    real_data_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    with db_test_app.app_context():
        report = import_policies_from_json(real_data_dir)
        assert report["aborted"] is False

    return db_test_app


class _MockEmailRecorder(list):
    """A list of {"to", "subject", "body"} dicts for each email that would
    have been sent, plus a should_fail switch tests can flip to simulate
    an SMTP failure without touching the network."""

    def __init__(self):
        super().__init__()
        self.should_fail = False


@pytest.fixture
def mock_email_service(monkeypatch):
    """Replaces email_service.send_email with an in-memory recorder - no
    real SMTP connection, no network access, ever. Only patched for the
    duration of the test (via monkeypatch), not swapped globally - the
    real send_email (backed by stdlib smtplib) is what ships to
    production."""
    import email_service

    recorder = _MockEmailRecorder()

    def _fake_send_email(to_email, subject, body_text):
        if recorder.should_fail:
            raise email_service.EmailSendError("simulated SMTP failure for testing")
        recorder.append({"to": to_email, "subject": subject, "body": body_text})

    monkeypatch.setattr(email_service, "send_email", _fake_send_email)
    return recorder


@pytest.fixture
def valid_registration_payload():
    return {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "password": "correct-horse-battery-staple",
    }
