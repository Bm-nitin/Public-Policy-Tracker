"""Run with POLICY_TRACKER_TEST_PG_BIN set to the PostgreSQL bin directory.

The tests start a temporary loopback cluster with synthetic data. They never
use DATABASE_URL and never run migrations against an existing installation.
"""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import socket
import subprocess

import pytest


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
POLICY_TABLE_REVISION = "e9e681dc3473"
PRE_RATE_LIMIT_REVISION = "c3e8a5f27d1b"
RATE_LIMIT_REVISION = "d4f1b7a92c6e"


def _pg_command(bin_dir, program, *args):
    executable = bin_dir / (program + ".exe" if os.name == "nt" else program)
    environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PG")}
    # On Windows the server spawned by pg_ctl can inherit an output pipe,
    # so subprocess would wait for it to close even after pg_ctl exited.
    output = subprocess.DEVNULL if program == "pg_ctl" else subprocess.PIPE
    result = subprocess.run(
        [str(executable), *map(str, args)],
        env=environment, text=True, errors="replace", stdout=output,
        stderr=subprocess.STDOUT, timeout=60,
    )
    assert result.returncode == 0, result.stdout or f"{program} exited with status {result.returncode}"
    return result.stdout


@pytest.fixture(scope="module")
def postgres_cluster(tmp_path_factory):
    bin_path = os.environ.get("POLICY_TRACKER_TEST_PG_BIN")
    if not bin_path:
        pytest.skip("Set POLICY_TRACKER_TEST_PG_BIN to run isolated PostgreSQL migration tests")

    import psycopg

    bin_dir = Path(bin_path).resolve(strict=True)
    root = tmp_path_factory.mktemp("policy-postgresql")
    data = root / "data"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    log = root / "postgres.log"
    try:
        _pg_command(bin_dir, "initdb", "-D", data, "-U", "phase19c_test", "-A", "trust", "--no-locale", "-E", "UTF8")
        # The fixture uses TCP on both platforms. Avoid the system socket
        # directory, which is usually writable only by PostgreSQL on Linux.
        _pg_command(bin_dir, "pg_ctl", "start", "-D", data, "-l", log, "-w", "-t", "20",
                    "-o", f"-h 127.0.0.1 -p {port} -c unix_socket_directories=")
        with psycopg.connect(
            host="127.0.0.1", hostaddr="127.0.0.1", port=port, dbname="postgres",
            user="phase19c_test", sslmode="disable", autocommit=True, connect_timeout=5,
        ) as admin:
            actual_directory = admin.execute("SHOW data_directory").fetchone()[0]
            assert Path(actual_directory).resolve() == data.resolve()
            yield admin, bin_dir, port
    finally:
        pid_file = data / "postmaster.pid"
        if pid_file.exists():
            _pg_command(bin_dir, "pg_ctl", "stop", "-D", data, "-m", "fast", "-w", "-t", "20")
        assert not pid_file.exists(), "PostgreSQL did not stop"
        if data.exists():
            assert data.resolve().parent == root.resolve()
            shutil.rmtree(data)


def _isolated_app(cluster, name):
    from flask import Flask
    from psycopg import sql
    from sqlalchemy.engine import URL

    from database import init_db
    import models  # noqa: F401 - load metadata for autogeneration

    admin, _, port = cluster
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    url = URL.create(
        "postgresql+psycopg", username="phase19c_test", host="127.0.0.1",
        port=port, database=name, query={"hostaddr": "127.0.0.1", "sslmode": "disable"},
    )
    app = Flask(__name__)
    init_db(app, url)
    return app


def _alembic(app, action, *args):
    with app.app_context():
        config = app.extensions["migrate"].migrate.get_config(directory=str(MIGRATIONS))
        action(config, *args)


@pytest.fixture(scope="module")
def fresh_app(postgres_cluster):
    from alembic import command

    app = _isolated_app(postgres_cluster, "fresh_schema")
    _alembic(app, command.upgrade, "head")
    return app


def _revision(connection):
    return connection.exec_driver_sql("SELECT version_num FROM alembic_version").scalar_one()


def _schema(connection):
    from sqlalchemy import inspect

    inspector = inspect(connection)
    return {
        name: (
            [(c["name"], str(c["type"]), c["nullable"], c["default"]) for c in inspector.get_columns(name)],
            inspector.get_pk_constraint(name),
            inspector.get_foreign_keys(name),
            inspector.get_unique_constraints(name),
            inspector.get_indexes(name),
        )
        for name in inspector.get_table_names()
    }


def _backup(cluster, name, path):
    _, bin_dir, port = cluster
    _pg_command(bin_dir, "pg_dump", "--host=127.0.0.1", "--port", port,
                "--username=phase19c_test", "--no-password", "--format=custom",
                "--file", path, "--dbname", name)
    assert "TABLE DATA public policies" in _pg_command(bin_dir, "pg_restore", "--list", path)


def test_fresh_database_matches_metadata(fresh_app):
    from alembic import command
    from alembic.script import ScriptDirectory
    from sqlalchemy import inspect

    from database import db

    _alembic(fresh_app, command.check)
    with fresh_app.app_context(), db.engine.connect() as connection:
        assert _revision(connection) == ScriptDirectory(str(MIGRATIONS)).get_current_head()
        assert set(inspect(connection).get_table_names()) == {*db.metadata.tables, "policies", "alembic_version"}
        assert connection.exec_driver_sql("SELECT count(*) FROM policies").scalar_one() == 0
        expected_indexes = {
            "policies": {"ix_policies_category", "ix_policies_name", "ix_policies_sector", "ix_policies_sub_category"},
            "policy_embeddings": {"ix_policy_embeddings_policy_id"},
            "rate_limit_events": {"ix_rate_limit_events_lookup", "ix_rate_limit_events_created_at"},
        }
        for table, expected in expected_indexes.items():
            assert expected <= {index["name"] for index in inspect(connection).get_indexes(table)}


@pytest.mark.parametrize("model_name, hash_column, unique_name", [
    ("EmailVerificationToken", "token_hash", "email_verification_tokens_token_hash_key"),
    ("PasswordResetToken", "token_hash", "password_reset_tokens_token_hash_key"),
    ("UserSession", "session_token_hash", "user_sessions_session_token_hash_key"),
])
def test_user_children_enforce_mapped_foreign_keys(fresh_app, model_name, hash_column, unique_name):
    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    import models
    from database import db

    child = getattr(models, model_name).__table__
    users = models.User.__table__
    fk, = child.c.user_id.foreign_keys
    assert fk.target_fullname == "users.id"
    assert fk.ondelete == "CASCADE"

    with fresh_app.app_context(), db.engine.connect() as connection:
        user_id = connection.execute(users.insert().values(
            name="Schema test", email="user@example.test", password_hash="fixture",
        ).returning(users.c.id)).scalar_one()
        values = {hash_column: "a" * 64, "expires_at": datetime.now(timezone.utc) + timedelta(hours=1)}
        for owner, sqlstate in [(-1, "23503"), (None, "23502")]:
            with pytest.raises(IntegrityError) as rejected, connection.begin_nested():
                connection.execute(child.insert().values(user_id=owner, **values))
            assert rejected.value.orig.sqlstate == sqlstate

        connection.execute(child.insert().values(user_id=user_id, **values))
        assert connection.execute(select(child.c.id).select_from(users.join(child))).scalar_one()
        with pytest.raises(IntegrityError) as duplicate, connection.begin_nested():
            connection.execute(child.insert().values(user_id=user_id, **values))
        assert duplicate.value.orig.diag.constraint_name == unique_name

        connection.execute(users.delete().where(users.c.id == user_id))
        assert connection.execute(select(child.c.id)).scalar_one_or_none() is None


def test_postgresql_user_defaults_and_constraints(fresh_app):
    from sqlalchemy.exc import IntegrityError

    from database import db

    with fresh_app.app_context(), db.engine.connect() as connection:
        insert = "INSERT INTO users (email, name, password_hash) VALUES (%s, %s, %s)"
        email = "defaults@example.test"
        verified, created, updated = connection.exec_driver_sql(
            insert + " RETURNING email_verified, created_at, updated_at", (email, "Test", "fixture"),
        ).one()
        assert verified is False
        assert created is not None and updated is not None
        for args, sqlstate in [((email, "Duplicate", "fixture"), "23505"),
                               (("missing@example.test", None, "fixture"), "23502")]:
            with pytest.raises(IntegrityError) as rejected, connection.begin_nested():
                connection.exec_driver_sql(insert, args)
            assert rejected.value.orig.sqlstate == sqlstate


def test_existing_upgrade_and_safe_downgrade_preserve_legacy_data(postgres_cluster, tmp_path):
    from alembic import command
    from sqlalchemy import inspect

    from database import db

    name = "upgrade_schema"
    app = _isolated_app(postgres_cluster, name)
    _alembic(app, command.upgrade, POLICY_TABLE_REVISION)
    columns = "id, name, sector, category, sub_category, change, impact, created_at, updated_at"
    with app.app_context(), db.engine.begin() as connection:
        assert _revision(connection) == POLICY_TABLE_REVISION
        connection.exec_driver_sql("""
            INSERT INTO policies (name, sector, category, sub_category, change, impact, source_json)
            VALUES ('Legacy A', 'energy', 'Category', 'Subcategory', 'Change', 'Impact', 'energy.json'),
                   ('Legacy B', 'education', 'Category', 'Subcategory', 'Change', 'Impact', 'education.json')
        """)
        before = connection.exec_driver_sql(f"SELECT {columns}, source_json FROM policies ORDER BY id").all()
        assert len(before) == 2

    _backup(postgres_cluster, name, tmp_path / "before-policy-upgrade.dump")
    _alembic(app, command.upgrade, PRE_RATE_LIMIT_REVISION)
    with app.app_context(), db.engine.connect() as connection:
        assert _revision(connection) == PRE_RATE_LIMIT_REVISION
        assert connection.exec_driver_sql(f"SELECT {columns}, source_file FROM policies ORDER BY id").all() == before
        before_rate_limits = _schema(connection)

    _backup(postgres_cluster, name, tmp_path / "before-rate-limit-upgrade.dump")
    _alembic(app, command.upgrade, RATE_LIMIT_REVISION)
    with app.app_context(), db.engine.connect() as connection:
        assert _revision(connection) == RATE_LIMIT_REVISION
        upgraded = _schema(connection)
        assert set(upgraded) - set(before_rate_limits) == {"rate_limit_events"}
        assert {table: upgraded[table] for table in before_rate_limits} == before_rate_limits
        assert connection.exec_driver_sql("SELECT count(*) FROM rate_limit_events").scalar_one() == 0
        assert connection.exec_driver_sql(f"SELECT {columns}, source_file FROM policies ORDER BY id").all() == before

    _backup(postgres_cluster, name, tmp_path / "before-rate-limit-downgrade.dump")
    _alembic(app, command.downgrade, PRE_RATE_LIMIT_REVISION)
    with app.app_context(), db.engine.connect() as connection:
        assert _revision(connection) == PRE_RATE_LIMIT_REVISION
        assert not inspect(connection).has_table("rate_limit_events")
        assert _schema(connection) == before_rate_limits
        assert connection.exec_driver_sql(f"SELECT {columns}, source_file FROM policies ORDER BY id").all() == before
    _alembic(app, command.upgrade, "head")
    _alembic(app, command.check)
