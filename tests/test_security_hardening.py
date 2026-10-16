"""
Phase 18 - security hardening tests.

These assert security PROPERTIES (a blocked client gets no password oracle,
a block cannot be extended by hammering, X-Forwarded-For cannot be spoofed,
a hostile origin cannot make a victim's browser change state, user text
never reaches the logs ...), not just status codes. Time is controlled by
replacing rate_limit._now - there are no sleeps and nothing depends on the
wall clock. No network; real Argon2/PBKDF2 hashes via security.hash_password.
"""

import contextlib
import io
import logging
from datetime import datetime, timedelta, timezone

import pytest

PW = "Correct-Horse-9"
FRONTEND = "https://frontend.example"
EVIL = "https://evil.example"


# =================================================================
# fixtures / helpers
# =================================================================

@pytest.fixture
def sec_app(db_test_app, monkeypatch):
    """A DB-backed app with the auth, account and conversation blueprints,
    the real /chat route, and init_security() installed - rate limiting ON
    and the clock frozen (tests move it via clock['now'])."""
    import app as app_module
    import config
    import rate_limit
    from account_routes import account_bp
    from auth_routes import auth_bp
    from conversations_routes import conversations_bp
    from security_http import init_security

    monkeypatch.setattr(config.Config, "DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(config.Config, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(config.Config, "CORS_ORIGINS", [FRONTEND])
    monkeypatch.setattr(config.Config, "FRONTEND_URL", None)
    monkeypatch.setattr(config.Config, "TRUSTED_PROXY_COUNT", 0)

    clock = {"now": datetime(2026, 10, 12, 12, 0, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(rate_limit, "_now", lambda: clock["now"])
    rate_limit._last_purge[0] = None
    rate_limit._last_error_log[0] = None

    init_security(db_test_app)
    db_test_app.register_blueprint(auth_bp)
    db_test_app.register_blueprint(account_bp)
    db_test_app.register_blueprint(conversations_bp)
    db_test_app.add_url_rule("/chat", "chat", app_module.chat, methods=["POST"])
    monkeypatch.setattr(app_module, "get_response", lambda text: "stub reply")
    db_test_app.clock = clock
    return db_test_app


def _make_user(app, email, name="Test", password=PW, verified=True):
    from database import db
    from models import User
    from security import hash_password

    with app.app_context():
        user = User(name=name, email=email, password_hash=hash_password(password), email_verified=verified)
        db.session.add(user)
        db.session.commit()
        return user.id


def _cookie_login(app, client, user_id):
    import config
    from database import db
    from models import UserSession
    from sessions import generate_session_token

    raw, token_hash, expires_at = generate_session_token()
    with app.app_context():
        db.session.add(UserSession(user_id=user_id, session_token_hash=token_hash, expires_at=expires_at))
        db.session.commit()
    client.set_cookie(config.Config.SESSION_COOKIE_NAME, raw)
    return raw


def _login(client, email, password, ip="10.0.0.1", **kwargs):
    return client.post("/api/auth/login", json={"email": email, "password": password},
                       environ_overrides={"REMOTE_ADDR": ip}, **kwargs)


def _fail_logins(client, email, n, ip="10.0.0.1"):
    return [_login(client, email, f"wrong-{i}-password", ip).status_code for i in range(n)]


def _advance(app, seconds):
    app.clock["now"] = app.clock["now"] + timedelta(seconds=seconds)


def _capture_stdout(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        result = fn()
    return result, buf.getvalue()


# =================================================================
# rate limiting - login
# =================================================================

def test_login_is_blocked_after_five_failures_with_a_consistent_429(sec_app):
    _make_user(sec_app, "victim@example.com")
    client = sec_app.test_client()

    assert _fail_logins(client, "victim@example.com", 5) == [401] * 5
    blocked = _login(client, "victim@example.com", "wrong-again")
    assert blocked.status_code == 429
    body = blocked.get_json()
    assert body["error"] == "Too many requests. Please try again later."
    assert 1 <= body["retry_after"] <= 900
    assert blocked.headers["Retry-After"] == str(body["retry_after"])
    assert "Set-Cookie" not in blocked.headers


def test_blocked_login_never_reaches_password_verification(sec_app, monkeypatch):
    """The security property that matters: once blocked, even the CORRECT
    password is refused and no Argon2 verification runs (no oracle, no CPU
    cost for the attacker to impose)."""
    import auth_routes

    _make_user(sec_app, "victim@example.com")
    client = sec_app.test_client()
    _fail_logins(client, "victim@example.com", 5)

    def must_not_run(*args, **kwargs):
        raise AssertionError("verify_password was called for a rate-limited client")

    monkeypatch.setattr(auth_routes, "verify_password", must_not_run)
    response = _login(client, "victim@example.com", PW)          # the right password
    assert response.status_code == 429
    assert "Set-Cookie" not in response.headers


def test_limits_apply_per_address_and_per_account_not_globally(sec_app):
    _make_user(sec_app, "victim@example.com")
    _make_user(sec_app, "other@example.com")
    client = sec_app.test_client()
    _fail_logins(client, "victim@example.com", 5, ip="10.0.0.1")
    assert _login(client, "victim@example.com", "x", ip="10.0.0.1").status_code == 429

    # the real owner, from a different address, is not locked out
    assert _login(client, "victim@example.com", PW, ip="203.0.113.9").status_code == 200
    # the same address can still try a different account
    assert _login(client, "other@example.com", PW, ip="10.0.0.1").status_code == 200


def test_one_address_spraying_many_accounts_is_stopped(sec_app):
    client = sec_app.test_client()
    codes = [_login(client, f"user{i}@example.com", "guess-guess-1", ip="198.51.100.7").status_code for i in range(21)]
    assert codes[:20] == [401] * 20
    assert codes[20] == 429


def test_unknown_and_real_accounts_are_throttled_identically(sec_app):
    """No account enumeration through the limiter: the 429 arrives after
    the same number of failures whether or not the account exists."""
    _make_user(sec_app, "real@example.com")
    client = sec_app.test_client()
    real = [_login(client, "real@example.com", f"bad-{i}-pw", ip="10.1.1.1").status_code for i in range(7)]
    fake = [_login(client, "ghost@example.com", f"bad-{i}-pw", ip="10.2.2.2").status_code for i in range(7)]
    assert real == fake == [401] * 5 + [429] * 2


def test_block_ends_exactly_when_the_window_expires(sec_app):
    _make_user(sec_app, "victim@example.com")
    client = sec_app.test_client()
    _fail_logins(client, "victim@example.com", 5)

    _advance(sec_app, 100)
    blocked = _login(client, "victim@example.com", PW)
    assert blocked.status_code == 429
    assert blocked.get_json()["retry_after"] == 800              # 900s window - 100s elapsed

    _advance(sec_app, 799)
    assert _login(client, "victim@example.com", PW).status_code == 429   # 1s early
    _advance(sec_app, 2)
    assert _login(client, "victim@example.com", PW).status_code == 200


def test_hammering_while_blocked_does_not_extend_the_block(sec_app):
    """No self-inflicted denial of service: blocked attempts are not
    counted, so an attacker cannot keep a victim locked out by continuing."""
    _make_user(sec_app, "victim@example.com")
    client = sec_app.test_client()
    _fail_logins(client, "victim@example.com", 5)
    _advance(sec_app, 600)
    assert [_login(client, "victim@example.com", "spam").status_code for _ in range(60)] == [429] * 60
    _advance(sec_app, 301)                                        # 901s after the first failure
    assert _login(client, "victim@example.com", PW).status_code == 200


def test_successful_logins_are_not_counted(sec_app):
    _make_user(sec_app, "user@example.com")
    client = sec_app.test_client()
    assert [_login(client, "user@example.com", PW).status_code for _ in range(12)] == [200] * 12


def test_a_single_address_can_never_trip_the_account_wide_limit(sec_app):
    """Account-wide cap (30/h) is above what ONE address can reach (5 per
    15 min = 20/h), so one attacker cannot lock the real user out."""
    _make_user(sec_app, "victim@example.com")
    client = sec_app.test_client()
    for window in range(4):                                       # 4 x 15 min = 1 hour of non-stop guessing
        _fail_logins(client, "victim@example.com", 8, ip="192.0.2.50")   # 5 count, 3 blocked
        # checked DURING the attack (not after it ages out): the real owner
        # signs in from their own address after every attacker burst
        assert _login(client, "victim@example.com", PW, ip=f"203.0.113.{100 + window}").status_code == 200, window
        _advance(sec_app, 901)


def test_distributed_guessing_against_one_account_is_slowed_and_recovers(sec_app):
    """Many addresses x 5 guesses each = the distributed attack the
    account-wide limit exists for. Documented residual risk: the real user
    is throttled too until the hour passes - temporary, never permanent."""
    _make_user(sec_app, "victim@example.com")
    client = sec_app.test_client()
    for i in range(6):
        assert _fail_logins(client, "victim@example.com", 5, ip=f"203.0.113.{i + 1}") == [401] * 5
    assert _login(client, "victim@example.com", PW, ip="203.0.113.200").status_code == 429
    _advance(sec_app, 3601)
    assert _login(client, "victim@example.com", PW, ip="203.0.113.200").status_code == 200


def test_counters_live_in_the_database_not_in_process_memory(sec_app):
    """A restart / another gunicorn worker sees the same counts: wipe every
    module-level cache and use a brand-new client - still blocked."""
    import rate_limit

    _make_user(sec_app, "victim@example.com")
    _fail_logins(sec_app.test_client(), "victim@example.com", 5)
    rate_limit._last_purge[0] = None
    rate_limit._last_error_log[0] = None
    assert _login(sec_app.test_client(), "victim@example.com", PW).status_code == 429


def test_rate_limit_rows_contain_hashes_only_no_email_or_address(sec_app):
    from models import RateLimitEvent

    _make_user(sec_app, "victim@example.com")
    _fail_logins(sec_app.test_client(), "victim@example.com", 2, ip="203.0.113.5")
    with sec_app.app_context():
        rows = RateLimitEvent.query.all()
        assert rows
        for row in rows:
            blob = f"{row.bucket} {row.key_hash}".lower()
            assert "victim" not in blob and "example.com" not in blob and "203.0.113.5" not in blob
            assert len(row.key_hash) == 64 and all(c in "0123456789abcdef" for c in row.key_hash)


def test_old_rate_limit_rows_are_purged(sec_app):
    import rate_limit
    from database import db
    from models import RateLimitEvent

    with sec_app.app_context():
        old = sec_app.clock["now"] - timedelta(hours=5)
        for i in range(5):
            db.session.add(RateLimitEvent(bucket="login:ip", key_hash=f"{i:064d}", created_at=old))
        db.session.commit()
        assert len(RateLimitEvent.query.all()) == 5
        rate_limit.check("login:ip", "anything")                 # triggers the periodic purge
        assert RateLimitEvent.query.all() == []


# =================================================================
# rate limiting - other sensitive endpoints
# =================================================================

def test_registration_is_limited_per_address(sec_app):
    client = sec_app.test_client()
    codes = [client.post("/api/auth/register", json={}, environ_overrides={"REMOTE_ADDR": "10.9.9.9"}).status_code for _ in range(11)]
    assert codes[:10] == [400] * 10 and codes[10] == 429
    assert client.post("/api/auth/register", json={}, environ_overrides={"REMOTE_ADDR": "10.9.9.8"}).status_code == 400


def test_forgot_password_limits_per_target_email_and_per_address_without_enumeration(sec_app):
    _make_user(sec_app, "real@example.com")
    client = sec_app.test_client()

    def forgot(email, ip):
        return client.post("/api/auth/forgot-password", json={"email": email}, environ_overrides={"REMOTE_ADDR": ip}).status_code

    real = [forgot("real@example.com", "10.3.3.3") for _ in range(4)]
    fake = [forgot("ghost@example.com", "10.4.4.4") for _ in range(4)]
    assert real == fake == [200, 200, 200, 429]                  # identical for real and non-existent accounts
    assert forgot("someone-else@example.com", "10.3.3.3") == 200  # other target, same address: fine
    codes = [forgot(f"u{i}@example.com", "10.5.5.5") for i in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429          # per-address ceiling


def test_resend_verification_limits_per_target_email(sec_app):
    client = sec_app.test_client()
    codes = [client.post("/api/auth/resend-verification", json={"email": "x@example.com"},
                         environ_overrides={"REMOTE_ADDR": "10.6.6.6"}).status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_reset_password_and_verify_email_are_limited_per_address(sec_app):
    client = sec_app.test_client()
    codes = [client.post("/api/auth/reset-password", json={"token": "nope", "password": "Whatever-1234"},
                         environ_overrides={"REMOTE_ADDR": "10.7.7.7"}).status_code for _ in range(21)]
    assert set(codes[:20]) == {400} and codes[20] == 429
    codes = [client.get("/api/auth/verify-email?token=nope", environ_overrides={"REMOTE_ADDR": "10.8.8.8"}).status_code for _ in range(61)]
    assert 429 not in codes[:60] and codes[60] == 429


def test_password_change_wrong_current_password_is_limited_per_account(sec_app):
    alice = _make_user(sec_app, "alice@example.com", name="Alice")
    bob = _make_user(sec_app, "bob@example.com", name="Bob")
    a, b = sec_app.test_client(), sec_app.test_client()
    _cookie_login(sec_app, a, alice)
    _cookie_login(sec_app, b, bob)

    body = {"current_password": "not-my-password", "new_password": "Brand-New-Pass-77"}
    assert [a.post("/api/account/change-password", json=body).status_code for _ in range(5)] == [400] * 5
    blocked = a.post("/api/account/change-password", json={"current_password": PW, "new_password": "Brand-New-Pass-77"})
    assert blocked.status_code == 429                              # even the right password: no oracle
    assert blocked.headers["Retry-After"]
    # another account is unaffected, and its success is not counted
    assert b.post("/api/account/change-password", json={"current_password": PW, "new_password": "Brand-New-Pass-88"}).status_code == 200


def test_chat_is_rate_limited_per_address_per_minute_and_per_hour(sec_app):
    client = sec_app.test_client()

    def chat(ip="10.11.11.11"):
        return client.post("/chat", json={"message": "hello"}, environ_overrides={"REMOTE_ADDR": ip})

    assert [chat().status_code for _ in range(20)] == [200] * 20
    blocked = chat()
    assert blocked.status_code == 429
    assert blocked.get_json()["reply"]                             # the existing frontend shows .reply
    assert blocked.get_json()["retry_after"] >= 1
    assert chat("10.11.11.12").status_code == 200                  # another address unaffected

    _advance(sec_app, 61)                                          # per-minute window reset
    assert chat().status_code == 200
    for _ in range(9):                                             # reach the 200/hour ceiling
        _advance(sec_app, 61)
        for _ in range(20):
            chat()
    _advance(sec_app, 61)
    assert chat().status_code == 429                               # the hourly rule, not the minute rule


# =================================================================
# rate limiting - behaviour of the limiter itself
# =================================================================

def test_limiter_fails_open_and_logs_only_the_exception_class(sec_app, monkeypatch):
    """A broken/missing rate_limit_events table (e.g. migration not yet
    applied) must not take login down - and the log must not leak SQL."""
    import rate_limit
    from sqlalchemy.exc import SQLAlchemyError

    _make_user(sec_app, "user@example.com")

    def broken(*args, **kwargs):
        raise SQLAlchemyError("SELECT ... FROM rate_limit_events WHERE key_hash='SECRETHASH' postgresql://u:p@h/db")

    monkeypatch.setattr(rate_limit, "_fresh_times", broken)
    records = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Collect()
    log = logging.getLogger("rate_limit")
    log.addHandler(handler)
    try:
        response = _login(sec_app.test_client(), "user@example.com", PW)
    finally:
        log.removeHandler(handler)
    assert response.status_code == 200
    joined = " ".join(records)
    assert "SQLAlchemyError" in joined or "Error" in joined
    assert "SECRETHASH" not in joined and "postgresql" not in joined


def test_limiter_can_be_disabled_and_is_inert_without_a_database(sec_app, monkeypatch):
    import config

    client = sec_app.test_client()
    monkeypatch.setattr(config.Config, "RATE_LIMIT_ENABLED", False)
    assert 429 not in _fail_logins(client, "nobody@example.com", 30, ip="10.20.20.20")
    monkeypatch.setattr(config.Config, "RATE_LIMIT_ENABLED", True)
    import rate_limit
    monkeypatch.setattr(config.Config, "DATABASE_URL", None)
    assert rate_limit.enabled() is False


def test_x_forwarded_for_is_ignored_unless_a_trusted_proxy_count_is_configured(db_test_app, monkeypatch):
    """A client cannot dodge a per-address limit by inventing an
    X-Forwarded-For - and behind N trusted proxies only the entry our own
    proxy appended (Nth from the right) counts, never what the client wrote."""
    import config
    import rate_limit
    from security_http import init_security

    def build(count):
        from flask import Flask
        monkeypatch.setattr(config.Config, "TRUSTED_PROXY_COUNT", count)
        app = Flask("ip-test")
        init_security(app)
        app.add_url_rule("/ip", "ip", lambda: rate_limit.client_ip())
        return app.test_client()

    spoof = {"X-Forwarded-For": "6.6.6.6, 9.9.9.9"}
    assert build(0).get("/ip", headers=spoof, environ_overrides={"REMOTE_ADDR": "1.1.1.1"}).get_data(as_text=True) == "1.1.1.1"
    assert build(1).get("/ip", headers=spoof, environ_overrides={"REMOTE_ADDR": "1.1.1.1"}).get_data(as_text=True) == "9.9.9.9"
    two = {"X-Forwarded-For": "6.6.6.6, 9.9.9.9, 8.8.8.8"}
    assert build(2).get("/ip", headers=two, environ_overrides={"REMOTE_ADDR": "1.1.1.1"}).get_data(as_text=True) == "9.9.9.9"
    # a client-supplied extra entry on the LEFT can never change the answer
    assert build(1).get("/ip", headers={"X-Forwarded-For": "1.2.3.4, 5.6.7.8, 9.9.9.9"},
                        environ_overrides={"REMOTE_ADDR": "1.1.1.1"}).get_data(as_text=True) == "9.9.9.9"


def test_ipv6_clients_are_bucketed_by_their_64(sec_app):
    import rate_limit

    def ip_for(addr):
        with sec_app.test_request_context("/", environ_overrides={"REMOTE_ADDR": addr}):
            return rate_limit.client_ip()

    assert ip_for("2001:db8:1:2::1") == ip_for("2001:db8:1:2:ffff::9") == "2001:db8:1:2::/64"
    assert ip_for("2001:db8:1:3::1") != ip_for("2001:db8:1:2::1")
    assert ip_for("203.0.113.5") == "203.0.113.5"


# =================================================================
# input robustness
# =================================================================

def test_non_object_json_bodies_never_crash_public_auth_endpoints(sec_app):
    """JSON arrays/strings/numbers reached `data.get(...)` and raised
    AttributeError -> HTTP 500 on four endpoints (reproduced before the fix)."""
    client = sec_app.test_client()
    for path in ("/api/auth/login", "/api/auth/register", "/api/auth/resend-verification",
                 "/api/auth/forgot-password", "/api/auth/reset-password"):
        for body in (["x"], "text", 5, [1, 2], True):
            response = client.post(path, json=body, environ_overrides={"REMOTE_ADDR": "10.30.30.30"})
            assert response.status_code < 500, (path, body, response.status_code)
            assert response.status_code in (200, 400, 401, 429), (path, body, response.status_code)


def test_oversized_requests_are_rejected_before_parsing(monkeypatch):
    """Built from scratch through init_security() with a 1000-byte ceiling,
    so it exercises the real wiring (nothing is set by hand): a declared
    Content-Length over the ceiling is refused before any handler runs, and
    so is a body that arrives WITHOUT a Content-Length (chunked), which only
    werkzeug's MAX_CONTENT_LENGTH can catch."""
    import io

    import config
    from flask import Flask, jsonify, request
    from security_http import init_security

    monkeypatch.setattr(config.Config, "MAX_REQUEST_BYTES", 1000)
    app = Flask("size-test")
    init_security(app)
    reached = []

    @app.route("/echo", methods=["POST"])
    def echo():
        reached.append(1)
        return jsonify({"ok": isinstance(request.get_json(silent=True), dict)})

    assert app.config["MAX_CONTENT_LENGTH"] == 1000                 # derived from Config, not set by the test
    client = app.test_client()
    big = b'{"pad":"' + b"x" * 5000 + b'"}'

    declared = client.post("/echo", data=big, content_type="application/json")
    assert declared.status_code == 413
    assert declared.get_json() == {"error": "Request too large"}
    assert reached == []                                            # handler never ran

    chunked = client.post("/echo", input_stream=io.BytesIO(big), content_type="application/json")
    assert chunked.status_code == 413
    assert chunked.get_json() == {"error": "Request too large"}
    assert reached == []

    small = client.post("/echo", json={"pad": "ok"})
    assert small.status_code == 200 and reached == [1]
    assert client.get("/echo").status_code == 405                   # GET is untouched by the guard (just not allowed here)


def test_chat_rejects_non_string_and_overlong_messages(sec_app, monkeypatch):
    import config

    client = sec_app.test_client()
    assert client.post("/chat", json={"message": 12345}).status_code == 400
    assert client.post("/chat", json={"message": ["a"]}).status_code == 400
    assert client.post("/chat", json={"message": {"a": 1}}).status_code == 400
    limit = config.Config.MAX_CHAT_MESSAGE_CHARS
    too_long = client.post("/chat", json={"message": "a" * (limit + 1)})
    assert too_long.status_code == 400 and too_long.get_json() == {"reply": "Message too long"}
    assert client.post("/chat", json={"message": "a" * limit}).status_code == 200


# =================================================================
# privacy of logs
# =================================================================

def test_chat_does_not_write_user_text_or_forged_lines_to_the_logs(sec_app):
    client = sec_app.test_client()
    secret = "my question about Jane Doe's medical policy"
    response, out = _capture_stdout(lambda: client.post(
        "/chat", json={"message": secret + "\n[OUTPUT] forged log line"}))
    assert response.status_code == 200
    assert "Jane Doe" not in out and "forged" not in out
    assert "input_chars=" in out


def test_chat_persistence_failure_logs_only_the_exception_class(sec_app, monkeypatch):
    import conversations_service
    from sqlalchemy.exc import SQLAlchemyError

    uid = _make_user(sec_app, "chatter@example.com")
    client = sec_app.test_client()
    _cookie_login(sec_app, client, uid)
    conv_id = client.post("/api/conversations", json={"title": "t"}).get_json()["data"]["id"]

    def boom(*args, **kwargs):
        raise SQLAlchemyError("INSERT INTO messages (content) VALUES ('private chat text about Jane Doe')")

    monkeypatch.setattr(conversations_service, "add_message", boom)
    response, out = _capture_stdout(lambda: client.post(
        "/chat", json={"message": "private chat text about Jane Doe", "conversation_id": conv_id}))
    assert response.status_code == 200                              # the reply is still delivered
    assert "Jane Doe" not in out and "INSERT" not in out
    assert "SQLAlchemyError" in out or "Error" in out


# =================================================================
# CSRF: cookie-authenticated requests from other origins
# =================================================================

def _victim(app):
    uid = _make_user(app, "victim@example.com", name="Victim")
    client = app.test_client()
    _cookie_login(app, client, uid)
    return uid, client


def test_hostile_origin_cannot_log_the_victim_out_or_create_conversations(sec_app):
    uid, victim = _victim(sec_app)
    hostile = {"Origin": EVIL}

    response = victim.post("/api/auth/logout", headers=hostile)
    assert response.status_code == 403
    assert response.get_json() == {"error": "Cross-site request rejected"}
    assert victim.get("/api/auth/me").status_code == 200            # session untouched

    # a form-style POST (text/plain, no JSON) - the classic CSRF shape
    assert victim.post("/api/conversations", headers=hostile, data="", content_type="text/plain").status_code == 403
    assert victim.get("/api/conversations").get_json()["data"] == [] or victim.get("/api/conversations").status_code == 200
    assert len(victim.get("/api/conversations").get_json()["data"]) == 0


def test_hostile_origin_cannot_change_profile_or_password(sec_app):
    from models import User

    uid, victim = _victim(sec_app)
    hostile = {"Origin": EVIL}
    assert victim.patch("/api/account/profile", json={"name": "Pwned"}, headers=hostile).status_code == 403
    assert victim.post("/api/account/change-password", headers=hostile,
                       json={"current_password": PW, "new_password": "Attacker-Chosen-1"}).status_code == 403
    with sec_app.app_context():
        assert User.query.filter_by(id=uid).first().name == "Victim"


def test_the_configured_frontend_origin_still_works(sec_app):
    uid, victim = _victim(sec_app)
    ok = {"Origin": FRONTEND}
    assert victim.patch("/api/account/profile", json={"name": "Renamed"}, headers=ok).status_code == 200
    assert victim.post("/api/conversations", json={"title": "t"}, headers=ok).status_code == 201
    assert victim.post("/api/auth/logout", headers=ok).status_code == 200


def test_frontend_url_setting_is_also_an_allowed_origin(sec_app, monkeypatch):
    import config

    monkeypatch.setattr(config.Config, "FRONTEND_URL", "https://app.example.org/")
    uid, victim = _victim(sec_app)
    assert victim.patch("/api/account/profile", json={"name": "N"}, headers={"Origin": "https://app.example.org"}).status_code == 200


def test_referer_is_checked_when_origin_is_absent_and_null_origin_is_rejected(sec_app):
    uid, victim = _victim(sec_app)
    assert victim.post("/api/auth/logout", headers={"Referer": EVIL + "/attack.html"}).status_code == 403
    assert victim.post("/api/auth/logout", headers={"Origin": "null"}).status_code == 403
    assert victim.post("/api/account/profile", headers={"Referer": FRONTEND + "/profile.html"}).status_code in (200, 404, 405)
    assert victim.get("/api/auth/me").status_code == 200


def test_requests_without_origin_or_referer_pass_they_are_not_a_browser_acting_for_an_attacker(sec_app):
    uid, victim = _victim(sec_app)
    assert victim.patch("/api/account/profile", json={"name": "Scripted"}).status_code == 200


def test_csrf_check_only_applies_to_unsafe_methods_and_to_cookie_bearing_requests(sec_app):
    uid, victim = _victim(sec_app)
    assert victim.get("/api/auth/me", headers={"Origin": EVIL}).status_code == 200      # safe method
    anonymous = sec_app.test_client()                                                    # no cookie -> nothing to abuse
    assert anonymous.post("/chat", json={"message": "hi"}, headers={"Origin": EVIL}).status_code == 200
    assert anonymous.post("/api/auth/login", json={"email": "x@y.zz", "password": "nope-nope-1"},
                          headers={"Origin": EVIL}).status_code == 401


def test_with_wildcard_cors_only_same_origin_may_use_the_cookie(sec_app, monkeypatch):
    import config

    monkeypatch.setattr(config.Config, "CORS_ORIGINS", "*")
    uid, victim = _victim(sec_app)
    assert victim.post("/api/auth/logout", headers={"Origin": EVIL}).status_code == 403
    assert victim.patch("/api/account/profile", json={"name": "Same"}, headers={"Origin": "http://localhost"}).status_code == 200


# =================================================================
# sessions
# =================================================================

def test_login_revokes_the_session_the_browser_was_already_carrying(sec_app):
    uid = _make_user(sec_app, "shared-pc@example.com")
    other = _make_user(sec_app, "next-user@example.com")
    client = sec_app.test_client()
    old_token = _cookie_login(sec_app, client, uid)
    assert client.get("/api/auth/me").status_code == 200

    # someone else signs in on the same browser
    response = client.post("/api/auth/login", json={"email": "next-user@example.com", "password": PW},
                           headers={"Origin": FRONTEND})
    assert response.status_code == 200

    import config
    stale = sec_app.test_client()
    stale.set_cookie(config.Config.SESSION_COOKIE_NAME, old_token)
    assert stale.get("/api/auth/me").status_code == 401             # the previous user's session is gone
    assert client.get("/api/auth/me").get_json()["user"]["email"] == "next-user@example.com"


# =================================================================
# response headers
# =================================================================

def test_security_headers_and_no_store_on_private_endpoints(sec_app):
    uid, victim = _victim(sec_app)
    for path in ("/api/auth/me", "/api/conversations", "/api/account/profile"):
        response = victim.get(path)
        assert response.headers["Cache-Control"] == "no-store", path
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Referrer-Policy"] == "no-referrer"
        assert "default-src 'none'" in response.headers["Content-Security-Policy"]
        assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    anonymous = sec_app.test_client().post("/chat", json={"message": "hi"})
    assert anonymous.headers["Cache-Control"] == "no-store"


def test_hsts_is_sent_only_when_the_deployment_is_https_only(sec_app, monkeypatch):
    import config

    monkeypatch.setattr(config.Config, "SESSION_COOKIE_SECURE", True)
    assert "max-age=" in sec_app.test_client().get("/api/auth/me").headers["Strict-Transport-Security"]
    monkeypatch.setattr(config.Config, "SESSION_COOKIE_SECURE", False)
    assert "Strict-Transport-Security" not in sec_app.test_client().get("/api/auth/me").headers


def test_cors_is_limited_to_the_methods_and_headers_the_frontend_uses():
    import app as app_module

    kwargs = app_module._cors_kwargs
    assert set(kwargs["methods"]) == {"GET", "POST", "PATCH", "DELETE", "OPTIONS"}
    assert "PUT" not in kwargs["methods"]
    assert set(kwargs["allow_headers"]) == {"Content-Type", "Accept"}
    assert "Retry-After" in kwargs["expose_headers"]


# =================================================================
# authorization inventory + production configuration
# =================================================================

_PUBLIC = {
    ("/", "GET"), ("/health", "GET"), ("/policies", "GET"), ("/chat", "POST"),
    ("/api/policies", "GET"), ("/api/policies/<policy_id>", "GET"),
    ("/api/policies/sectors", "GET"), ("/api/policies/categories", "GET"),
    ("/api/policies/sub-categories", "GET"),
    ("/api/auth/register", "POST"), ("/api/auth/verify-email", "GET"),
    ("/api/auth/resend-verification", "POST"), ("/api/auth/login", "POST"),
    ("/api/auth/forgot-password", "POST"), ("/api/auth/reset-password", "POST"),
    ("/api/auth/logout", "POST"),   # public by design: with no session it just clears the cookie
    ("/static/<path:filename>", "GET"),
}


def test_every_non_public_route_in_the_real_app_requires_authentication(flask_app_module):
    """Route inventory: a NEW route must either be added to the explicit
    public allow-list above (a conscious decision) or reject anonymous
    callers - it cannot silently ship unauthenticated."""
    import re

    app = flask_app_module.app
    client = app.test_client()
    unprotected, checked = [], 0
    for rule in app.url_map.iter_rules():
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            if (rule.rule, method) in _PUBLIC:
                continue
            path = re.sub(r"<[^>]+>", "1", rule.rule)
            response = client.open(path, method=method, json={} if method in ("POST", "PATCH", "PUT") else None)
            checked += 1
            if response.status_code != 401:
                unprotected.append((method, rule.rule, response.status_code))
    assert checked >= 15
    assert unprotected == [], f"routes reachable without authentication: {unprotected}"


def test_production_config_refuses_unsafe_settings(monkeypatch):
    import config

    C = config.Config
    monkeypatch.setattr(C, "CORS_ORIGINS", [FRONTEND])
    monkeypatch.setattr(C, "SESSION_COOKIE_SECURE", True)
    monkeypatch.setattr(C, "DATABASE_URL", "postgresql+psycopg://u:p@h/db")
    monkeypatch.setattr(C, "RATE_LIMIT_ENABLED", True)
    assert C.production_problems() == []

    monkeypatch.setattr(C, "CORS_ORIGINS", "*")
    monkeypatch.setattr(C, "SESSION_COOKIE_SECURE", False)
    monkeypatch.setattr(C, "DATABASE_URL", None)
    monkeypatch.setattr(C, "RATE_LIMIT_ENABLED", False)
    problems = " | ".join(C.production_problems())
    for needle in ("CORS_ORIGINS", "SESSION_COOKIE_SECURE", "DATABASE_URL", "RATE_LIMIT_ENABLED"):
        assert needle in problems

    monkeypatch.setattr(C, "APP_ENV", "")
    C.enforce_production()                                           # development: never blocks startup
    monkeypatch.setattr(C, "APP_ENV", "production")
    with pytest.raises(RuntimeError) as err:
        C.enforce_production()
    assert "postgresql" not in str(err.value) and "password" not in str(err.value).lower()
