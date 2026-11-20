"""Regression checks for the production-only entry point."""

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def deployment_config(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1] / "backend"))
    import deployment

    config = deployment.Config
    for key, value in {
        "APP_ENV": "production",
        "SECRET_KEY": "local-test-key-for-deployment-checks-only",
        "SECRET_KEY_WAS_GENERATED": False,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_COOKIE_SECURE": True,
        "TRUSTED_PROXY_COUNT": 0,
        "RATE_LIMIT_ENABLED": True,
        "FRONTEND_URL": None,
        "CORS_ORIGINS": "*",
        "GEMINI_API_KEY": None,
        "MAIL_HOST": None,
        "DATABASE_URL": (
            "postgresql://app:dummy@127.0.0.1:1/postgres"
            "?sslmode=verify-full&sslrootcert=unused.pem"
        ),
    }.items():
        monkeypatch.setattr(config, key, value)
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://render.example")
    monkeypatch.delenv("RENDER", raising=False)
    monkeypatch.setattr(config, "RENDER_EDGE_HEADERS", False)
    return deployment


@pytest.mark.parametrize(("key", "value", "error"), [
    ("APP_ENV", "", "APP_ENV=production"),
    ("SECRET_KEY_WAS_GENERATED", True, "persistent SECRET_KEY"),
    ("SESSION_COOKIE_SAMESITE", "None", "SESSION_COOKIE_SAMESITE=Lax"),
    ("FRONTEND_URL", "http://render.example", "public HTTPS origin"),
    ("FRONTEND_URL", "https://render.example/path", "public HTTPS origin"),
    ("FRONTEND_URL", "https://.render.example", "public HTTPS origin"),
    ("DATABASE_URL", "sqlite:///development.db", "verify-full"),
    ("DATABASE_URL", "postgres://app:dummy@127.0.0.1/db?sslmode=require", "verify-full"),
    ("DATABASE_URL", "postgres://app:dummy@127.0.0.1/db?sslmode=verify-full&sslrootcert=dummy.pem&port=6543", "connection overrides"),
])
def test_production_rejects_unsafe_configuration(deployment_config, monkeypatch, key, value, error):
    monkeypatch.setattr(deployment_config.Config, key, value)
    with pytest.raises(RuntimeError, match=error) as exc:
        deployment_config.create_app()
    assert "app:dummy@" not in str(exc.value)


def test_render_requires_session_pooler_app_login_and_ca(deployment_config, monkeypatch, tmp_path):
    from sqlalchemy.engine import URL

    host = "aws-0-region.pooler.supabase.com"
    ca = tmp_path / "ca.pem"
    ca.touch()
    monkeypatch.setenv("RENDER", "true")
    monkeypatch.setenv("SUPABASE_POOLER_HOST", host)
    url = URL.create("postgresql", username="policy_tracker_app.testref", password="dummy",
                     host=host, port=5432, database="postgres",
                     query={"sslmode": "verify-full", "sslrootcert": str(ca)})
    monkeypatch.setattr(deployment_config.Config, "DATABASE_URL", url)
    assert deployment_config._configure_production() == ["render.example"]

    for invalid in (url.set(port=6543), url.set(username="postgres.testref"),
                    url.set(host="other.pooler.supabase.com")):
        monkeypatch.setattr(deployment_config.Config, "DATABASE_URL", invalid)
        with pytest.raises(RuntimeError, match="Session Pooler"):
            deployment_config._configure_production()

    monkeypatch.setattr(deployment_config.Config, "DATABASE_URL", url.update_query_dict({"sslrootcert": "missing.pem"}))
    with pytest.raises(RuntimeError, match="readable absolute file"):
        deployment_config._configure_production()


def test_render_uses_edge_client_ip_and_https_origin(deployment_config, monkeypatch):
    from flask import Flask
    from rate_limit import client_ip
    from security_http import _is_allowed_origin, init_security

    monkeypatch.setattr(deployment_config.Config, "RENDER_EDGE_HEADERS", True)
    monkeypatch.setattr(deployment_config.Config, "TRUSTED_PROXY_COUNT", 1)
    app = Flask(__name__)
    app.config["DEPLOYMENT_HTTPS"] = True
    init_security(app)

    @app.get("/edge")
    def edge():
        return {"ip": client_ip(), "https_origin": _is_allowed_origin("https://render.example"),
                "http_origin": _is_allowed_origin("http://render.example")}

    response = app.test_client().get("/edge", base_url="http://render.example", headers={
        "CF-Connecting-IP": "192.0.2.10", "X-Forwarded-For": "192.0.2.20",
    })
    assert response.json == {"ip": "192.0.2.10", "https_origin": True, "http_origin": False}
    invalid = app.test_client().get("/edge", base_url="http://render.example", headers={
        "CF-Connecting-IP": "invalid", "X-Forwarded-For": "192.0.2.20",
    })
    assert invalid.json["ip"] == "127.0.0.1"


def test_production_frontend_routes_and_health(deployment_config, monkeypatch):
    # Restore the prior legacy app once this process finishes the test.
    monkeypatch.setitem(sys.modules, "app", None)
    del sys.modules["app"]
    monkeypatch.setattr(deployment_config.Config, "FRONTEND_URL", "https://custom.example:443/")
    app = deployment_config.create_app()
    client = app.test_client()
    base = "https://render.example"
    assert app.debug is False
    assert client.get("/health", base_url=base).get_json() == {"status": "ok"}
    assert client.get("/health", base_url="https://custom.example").status_code == 200
    assert client.get("/health", base_url="https://unlisted.example").status_code == 400

    homepage = client.get("/", base_url=base)
    assert homepage.status_code == 200 and homepage.mimetype == "text/html"
    assert homepage.headers["Referrer-Policy"] == "no-referrer"
    assert "Strict-Transport-Security" in homepage.headers
    assert client.get("/policies", base_url=base).is_json
    assert client.get("/api/no-such-route.html", base_url=base).status_code == 404
    script = client.get("/auth.js", base_url=base)
    assert b'"/api/auth"' in script.data and deployment_config.LEGACY_API_ORIGIN not in script.data
    reset = client.get("/reset-password", base_url=base)
    assert reset.status_code == 200 and reset.headers["Cache-Control"] == "no-store"
    verification = client.get("/verify-email.html", base_url=base)
    assert verification.status_code == 200 and verification.headers["Cache-Control"] == "no-store"
    unavailable = client.get("/ready", base_url=base)
    assert unavailable.status_code == 503
    assert unavailable.get_json() == {"status": "unavailable"}
    assert unavailable.headers["Cache-Control"] == "no-store"

    with app.app_context():
        engine = deployment_config.db.engine
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.pool.size() == 2 and engine.pool.timeout() == 1
        assert engine.hide_parameters
        engine.dispose()

    connect = MagicMock()
    connection = connect.return_value.__enter__.return_value
    connection.execute.return_value.scalars.return_value = ["previous"]
    monkeypatch.setattr(engine, "connect", connect)
    assert client.get("/ready", base_url=base).status_code == 503
    connection.execute.return_value.scalars.return_value = ["d4f1b7a92c6e"]
    assert client.get("/ready", base_url=base).get_json() == {"status": "ok"}

    import rate_limit
    from sqlalchemy.exc import OperationalError

    monkeypatch.setattr(rate_limit, "_maybe_purge", MagicMock(side_effect=OperationalError(None, None, None)))
    response = client.post("/chat", base_url=base, json={"message": "A policy question"})
    assert response.status_code == 503
    assert response.get_json() == {"error": "Service temporarily unavailable"}
