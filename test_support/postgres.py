"""Disposable loopback PostgreSQL fixtures shared by schema and browser tests."""

import os
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


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


@pytest.fixture(scope="session")
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


def isolated_database(cluster, name):
    from psycopg import sql
    from sqlalchemy.engine import URL

    admin, _, port = cluster
    admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return URL.create(
        "postgresql+psycopg", username="phase19c_test", host="127.0.0.1",
        port=port, database=name, query={"hostaddr": "127.0.0.1", "sslmode": "disable"},
    )


def _isolated_app(cluster, name):
    from flask import Flask

    from database import init_db
    import models  # noqa: F401 - load metadata for autogeneration

    app = Flask(__name__)
    init_db(app, isolated_database(cluster, name))
    return app


def _alembic(app, action, *args):
    with app.app_context():
        config = app.extensions["migrate"].migrate.get_config(directory=str(MIGRATIONS))
        action(config, *args)
