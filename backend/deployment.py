"""Production WSGI entry point for the single-origin web service."""

import os
from pathlib import Path
import re
from urllib.parse import urlsplit

from alembic.script import ScriptDirectory
from flask import abort, jsonify, request, send_from_directory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError

from config import Config
from database import db

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
LEGACY_API_ORIGIN = b"https://policy-tracker-b8a3.onrender.com"
FRONTEND_SUFFIXES = {".html", ".js", ".css"}


def _https_origin(value):
    try:
        origin = urlsplit(value or "")
        host = origin.hostname or ""
        port = origin.port
        if (
            origin.scheme == "https" and re.fullmatch(r"[a-z0-9.-]+", host)
            and all(label and label[0].isalnum() and label[-1].isalnum() for label in host.split("."))
            and origin.netloc.lower() == (host if port is None else f"{host}:{port}")
            and origin.path in {"", "/"} and not origin.query and not origin.fragment
        ):
            return f"https://{host}" if port in (None, 443) else f"https://{host}:{port}"
    except ValueError:
        pass
    raise RuntimeError("Set FRONTEND_URL to the public HTTPS origin or use RENDER_EXTERNAL_URL.")


def _configure_production():
    if Config.APP_ENV != "production":
        raise RuntimeError("The deployment entry point requires APP_ENV=production.")
    if Config.SECRET_KEY_WAS_GENERATED or len(Config.SECRET_KEY.strip()) < 32:
        raise RuntimeError("Set a persistent SECRET_KEY of at least 32 characters.")
    if Config.SESSION_COOKIE_SAMESITE != "Lax":
        raise RuntimeError("Set SESSION_COOKIE_SAMESITE=Lax for the single-origin deployment.")

    try:
        url = make_url(Config.DATABASE_URL or "")
    except (ArgumentError, ValueError):
        raise RuntimeError("DATABASE_URL must be a valid PostgreSQL connection URL.") from None
    if (
        url.drivername not in {"postgres", "postgresql", "postgresql+psycopg"}
        or not all((url.host, url.username, url.password, url.database))
        or url.query.keys() - {"sslmode", "sslrootcert", "application_name"}
        or any(not isinstance(value, str) for value in url.query.values())
        or url.query.get("sslmode") != "verify-full"
        or not url.query.get("sslrootcert")
    ):
        raise RuntimeError("DATABASE_URL must use PostgreSQL with sslmode=verify-full, sslrootcert, and no connection overrides.")

    Config.RENDER_EDGE_HEADERS = os.getenv("RENDER") == "true"
    if Config.RENDER_EDGE_HEADERS:
        pooler_host = os.getenv("SUPABASE_POOLER_HOST", "")
        if (
            not re.fullmatch(r"[a-z0-9-]+\.pooler\.supabase\.com", pooler_host)
            or url.host != pooler_host
            or url.port != 5432
            or not re.fullmatch(r"policy_tracker_app\.[a-z0-9]+", url.username)
        ):
            raise RuntimeError("Use the configured Supabase Session Pooler host, port 5432, and app login.")
        ca_file = Path(url.query["sslrootcert"])
        if not ca_file.is_absolute() or not ca_file.is_file() or not os.access(ca_file, os.R_OK):
            raise RuntimeError("The Supabase root certificate must be a readable absolute file path.")

    render_url = os.getenv("RENDER_EXTERNAL_URL")
    origin = _https_origin(Config.FRONTEND_URL or render_url)
    trusted_hosts = {urlsplit(origin).hostname}
    if render_url:
        trusted_hosts.add(urlsplit(_https_origin(render_url)).hostname)
    Config.FRONTEND_URL = origin
    Config.CORS_ORIGINS = [origin]
    return sorted(trusted_hosts)


def _frontend_asset(filename):
    suffix = Path(filename).suffix
    if suffix not in FRONTEND_SUFFIXES:
        abort(404)
    if suffix != ".js":
        return send_from_directory(FRONTEND, filename)

    # The original files also serve the legacy split deployment. Here the
    # browser calls the API at its current origin, including on custom domains.
    response = send_from_directory(FRONTEND, filename, conditional=False, etag=False)
    response.direct_passthrough = False
    response.set_data(response.get_data().replace(LEGACY_API_ORIGIN, b""))
    response.headers.pop("Last-Modified", None)
    return response


def create_app():
    trusted_hosts = _configure_production()
    from app import app

    app.config.update(DEBUG=False, TRUSTED_HOSTS=trusted_hosts, DEPLOYMENT_HTTPS=True)
    with app.app_context():
        migrate = app.extensions["migrate"].migrate
        scripts = ScriptDirectory.from_config(migrate.get_config(directory=str(ROOT / "migrations")))
        expected_revisions = set(scripts.get_heads())

    @app.get("/ready")
    def readiness():
        try:
            with db.engine.connect() as connection:
                connection.execute(text("SET LOCAL statement_timeout = '1000ms'"))
                revisions = set(connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars())
        except SQLAlchemyError as error:
            app.logger.warning("Readiness database check failed (%s)", type(error).__name__)
            return jsonify(status="unavailable"), 503
        if revisions != expected_revisions:
            return jsonify(status="unavailable"), 503
        return jsonify(status="ok")

    # Preserve the legacy API root for callers using the development entry point.
    app.view_functions["home"] = lambda: _frontend_asset("index.html")
    app.add_url_rule("/reset-password", view_func=lambda: _frontend_asset("reset-password.html"),
                     endpoint="reset_password_page")
    app.add_url_rule("/<path:filename>", view_func=_frontend_asset)

    @app.after_request
    def uncached_deployment_responses(response):
        if request.path in {"/health", "/ready", "/reset-password", "/reset-password.html", "/verify-email.html"}:
            response.headers["Cache-Control"] = "no-store"
        return response

    return app
