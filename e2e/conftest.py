"""Real browser HTTP, Flask, and disposable PostgreSQL for the opt-in E2E suite."""

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import smtplib
import sys
from threading import Thread
from urllib.parse import parse_qs, quote, urlsplit

from alembic import command
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from flask import Flask, Response, send_from_directory
from playwright.sync_api import Error, expect
import pytest
from werkzeug.serving import WSGIRequestHandler, make_server

from e2e.workflows import AI_REPLY, COOKIE_NAME
from test_support.postgres import _alembic, isolated_database, postgres_cluster


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "frontend"
LIVE_API_ORIGIN = "https://policy-tracker-b8a3.onrender.com"
PASSED_REPORT = pytest.StashKey[bool]()
MASKED_ELEMENTS = (
    "input, textarea, [contenteditable], #header-account, #dashUserName, "
    "#dashUserEmail, #dashAvatar, #accEmail, #accSince"
)


def pytest_configure(config):
    config.addinivalue_line("markers", "cross_browser: targeted smoke coverage on all browser engines")
    config.addinivalue_line("markers", "public_trace: capture a trace on failure for credential-free workflows")


@dataclass(repr=False)
class Stack:
    frontend: str
    api: str
    other_origin: str
    app: Flask
    database: object
    mail: list = field(default_factory=list)
    private_values: set = field(default_factory=set)

    def keep_private(self, value):
        self.private_values.add(value)
        return value

    def redact(self, text):
        for value in sorted(self.private_values, key=len, reverse=True):
            text = text.replace(value, "[redacted]").replace(quote(value, safe=""), "[redacted]")
        return text

    def password(self):
        return self.keep_private(secrets.token_urlsafe(18))

    def capture_mail(self, recipient, subject, body):
        link = next(line for line in body.splitlines() if line.startswith(self.frontend + "/"))
        parts = urlsplit(link)
        token = parse_qs(parts.fragment or parts.query)["token"][0]
        self.keep_private(token)
        self.mail.append((recipient, subject, link))

    def mail_link(self, recipient, subject_contains):
        for address, subject, url in reversed(self.mail):
            if address == recipient and subject_contains.lower() in subject.lower():
                return url
        pytest.fail("The test mailbox did not receive the expected message")

    def sql(self, statement, parameters=()):
        with self.app.app_context(), self.database.engine.begin() as connection:
            result = connection.exec_driver_sql(statement, parameters)
            return result.tuples().all() if result.returns_rows else None


class QuietHandler(WSGIRequestHandler):
    """Request paths can include the reset token."""

    def log_request(self, code="-", size="-"):
        pass


def _tls_files(directory):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]), False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = directory / "cert.pem", directory / "key.pem"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    return str(cert_path), str(key_path)


def _frontend():
    app = Flask("e2e-frontend", static_folder=None)

    @app.get("/")
    @app.get("/<path:asset>")
    def asset_response(asset="index.html"):
        # Reset mail uses a clean URL. This is the test host's static rewrite.
        if asset == "reset-password":
            asset = "reset-password.html"
        response = send_from_directory(FRONTEND, asset)
        if asset.endswith(".js"):
            response.direct_passthrough = False
            response.set_data(response.get_data().replace(
                LIVE_API_ORIGIN.encode(), app.config["TEST_API_ORIGIN"].encode(),
            ))
        return response

    return app


def _server(app, tls):
    return make_server("127.0.0.1", 0, app, threaded=True, ssl_context=tls, request_handler=QuietHandler)


@contextmanager
def _serving(server):
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        assert not thread.is_alive(), "Loopback HTTP server did not stop"


def _fixture_policies(directory, patch):
    import policy_loader

    data = directory / "data"
    data.mkdir()
    records = (
        ("education", "Learning Access Grant", "Access", "Schools", "Funds school supplies."),
        ("energy", "Solar Access Grant", "Renewables", "Households", "Funds home solar equipment."),
    )
    for sector, name, category, sub_category, change in records:
        (data / f"{sector}.json").write_text(json.dumps([{
            "name": name, "category": category, "sub_category": sub_category,
            "change": change, "impact": "Improves access in the test community.",
        }]), encoding="utf-8")
    patch.setattr(policy_loader, "__file__", str(directory / "backend" / "policy_loader.py"))
    patch.setattr(policy_loader, "_cached_policies", None)


@pytest.fixture(scope="session")
def stack(postgres_cluster, tmp_path_factory):
    if not (FRONTEND / "verify-email.js").is_file():
        pytest.fail("The Phase 19B verification page and script are required for Phase 20")

    directory = tmp_path_factory.mktemp("e2e-http")
    connection_url = isolated_database(postgres_cluster, "browser_e2e")
    with ExitStack() as resources, pytest.MonkeyPatch.context() as patch:
        patch.setenv("PYTHON_DOTENV_DISABLED", "1")
        patch.setenv("DATABASE_URL", connection_url.render_as_string(hide_password=False))
        patch.setenv("APP_ENV", "")
        patch.setenv("PORT", "5000")
        for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "API_KEY", "MAIL_HOST"):
            patch.setenv(name, "")

        tls = _tls_files(directory)
        for path in tls:
            resources.callback(Path(path).unlink, missing_ok=True)
        frontend_app = _frontend()
        frontend = _server(frontend_app, tls)
        backend = _server(Response("Starting", status=503), tls)
        other = _server(Response("Untrusted test origin", mimetype="text/html"), tls)
        for server in (frontend, backend, other):
            resources.callback(server.server_close)

        frontend_url = f"https://127.0.0.1:{frontend.server_port}"
        backend_url = f"https://127.0.0.1:{backend.server_port}"
        other_url = f"https://127.0.0.1:{other.server_port}"
        frontend_app.config["TEST_API_ORIGIN"] = backend_url

        patch.syspath_prepend(str(ROOT / "backend"))
        if "app" in sys.modules:
            pytest.fail("Run e2e separately so Flask starts with the isolated configuration")
        from config import Config

        for name, value in {
            "APP_ENV": "production", "DATABASE_URL": connection_url.render_as_string(hide_password=False),
            "CORS_ORIGINS": [frontend_url], "FRONTEND_URL": frontend_url,
            "SESSION_COOKIE_SECURE": True, "SESSION_COOKIE_SAMESITE": "None",
            "SESSION_COOKIE_NAME": COOKIE_NAME, "SECRET_KEY": secrets.token_urlsafe(32),
            "SECRET_KEY_WAS_GENERATED": False, "TRUSTED_PROXY_COUNT": 0,
            "RATE_LIMIT_ENABLED": True, "GEMINI_API_KEY": None, "MAIL_HOST": None,
        }.items():
            patch.setattr(Config, name, value)

        _fixture_policies(directory, patch)
        import app as backend_module
        import auth_routes
        from database import db
        import email_service

        app = backend_module.app
        _alembic(app, command.upgrade, "head")
        _alembic(app, command.check)
        state = Stack(frontend_url, backend_url, other_url, app, db)
        patch.setattr(email_service, "send_email", state.capture_mail)

        def reject_smtp(*_args, **_kwargs):
            pytest.fail("E2E must use the test mailbox")

        patch.setattr(smtplib, "SMTP", reject_smtp)
        patch.setattr(backend_module, "get_response", lambda _message: AI_REPLY)
        secret_generators = (
            "generate_verification_token", "generate_password_reset_token", "generate_session_token",
        )
        for name in secret_generators:
            original = getattr(auth_routes, name)

            def track(*args, generator=original, **kwargs):
                result = generator(*args, **kwargs)
                state.keep_private(result[0])
                return result

            patch.setattr(auth_routes, name, track)

        backend.app = app
        for server in (backend, frontend, other):
            resources.enter_context(_serving(server))
        yield state


@pytest.fixture(autouse=True)
def clean_database(stack):
    stack.mail.clear()
    stack.private_values.clear()
    yield
    stack.sql("TRUNCATE TABLE users, rate_limit_events RESTART IDENTITY CASCADE")


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    report = yield
    state = item.funcargs.get("stack")
    if state is not None:
        if report.longrepr is not None and (clean := state.redact(str(report.longrepr))) != str(report.longrepr):
            report.longrepr = clean
        report.sections = [(title, state.redact(value)) for title, value in report.sections]
    if report.when == "call":
        item.stash[PASSED_REPORT] = report.passed
    return report


@pytest.fixture
def page(browser, browser_name, request, stack, clean_database):
    context = browser.new_context(
        ignore_https_errors=True, service_workers="block", viewport={"width": 1366, "height": 768},
    )
    trace_public = request.node.get_closest_marker("public_trace") is not None
    trace_started = False
    try:
        origins = tuple(f"{origin}/" for origin in (stack.frontend, stack.api, stack.other_origin))
        context.route("**/*", lambda route: (
            route.continue_() if route.request.url.startswith(origins) else route.abort()
        ))
        if trace_public:
            context.tracing.start(screenshots=True, snapshots=True, sources=False)
            trace_started = True
        tab = context.new_page()
        tab.set_default_timeout(10000)
        expect.set_options(timeout=7000)
        yield tab
    finally:
        try:
            failed = not request.node.stash.get(PASSED_REPORT, True)
            if failed and context.pages:
                target = Path(os.environ.get("POLICY_E2E_ARTIFACTS", "test-results/e2e"))
                target.mkdir(parents=True, exist_ok=True)
                case = hashlib.sha256(request.node.nodeid.encode()).hexdigest()[:12]
                tab = context.pages[0]
                masks = [tab.locator(MASKED_ELEMENTS)]
                # Also mask unexpected echoes of private values in page text.
                for value in tuple(stack.private_values):
                    masks.append(tab.get_by_text(value, exact=False))
                    encoded = quote(value, safe="")
                    if encoded != value:
                        masks.append(tab.get_by_text(encoded, exact=False))
                tab.screenshot(path=target / f"{browser_name}-{case}.png", mask=masks, animations="disabled")
                if trace_started:
                    context.tracing.stop(path=target / f"{browser_name}-{case}.zip")
                    trace_started = False
        except Error:
            # Preserve the original failure if the browser already exited.
            pass
        finally:
            try:
                if trace_started:
                    context.tracing.stop()
            finally:
                context.close()
