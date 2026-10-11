"""
Phase 17 - profile & account management backend tests.

Same db_test_app + real-session-cookie authentication pattern as
tests/test_dashboard.py / tests/test_saved_policies.py. Passwords are real
Argon2id hashes (security.hash_password) wherever a test exercises the
password flow; no network, no Gemini.
"""

import json

import pytest

OLD_PW = "OldPassw0rd!"
NEW_PW = "BrandNewPassw0rd!"


# =================================================================
# fixtures / helpers
# =================================================================

@pytest.fixture
def account_app(db_test_app, monkeypatch):
    import config
    from account_routes import account_bp
    from auth_routes import auth_bp

    monkeypatch.setattr(config.Config, "DATABASE_URL", "sqlite:///:memory:")
    db_test_app.register_blueprint(auth_bp)
    db_test_app.register_blueprint(account_bp)
    return db_test_app


def _create_user(app, email, name="Test User", password=OLD_PW):
    from database import db
    from models import User
    from security import hash_password

    with app.app_context():
        user = User(name=name, email=email, password_hash=hash_password(password), email_verified=True)
        db.session.add(user)
        db.session.commit()
        return user.id


def _login(app, client, user_id):
    """Adds a fresh UserSession row and puts its raw token in the client's cookie jar."""
    import config
    from database import db
    from models import UserSession
    from sessions import generate_session_token

    raw_token, token_hash, expires_at = generate_session_token()
    with app.app_context():
        db.session.add(UserSession(user_id=user_id, session_token_hash=token_hash, expires_at=expires_at))
        db.session.commit()
    client.set_cookie(config.Config.SESSION_COOKIE_NAME, raw_token)
    return raw_token


def _user_row(app, user_id):
    from models import User

    with app.app_context():
        u = User.query.filter_by(id=user_id).first()
        return {"name": u.name, "email": u.email, "password_hash": u.password_hash, "email_verified": u.email_verified}


def _active_sessions(app, user_id):
    from models import UserSession

    with app.app_context():
        return len(UserSession.query.filter_by(user_id=user_id, revoked_at=None).all())


def _hash_scheme(password_hash):
    """'argon2id' for '$argon2id$v=19$...'; 'sandboxpbkdf2' for the offline stand-in's hashes."""
    return [part for part in password_hash.split("$") if part][0]


def _patch(client, body):
    return client.patch("/api/account/profile", json=body)


def _change(client, body):
    return client.post("/api/account/change-password", json=body)


@pytest.fixture
def authed(account_app):
    client = account_app.test_client()
    uid = _create_user(account_app, "solo@example.com", name="Solo User")
    _login(account_app, client, uid)
    return account_app, client, uid


@pytest.fixture
def two_users(account_app):
    a, b = account_app.test_client(), account_app.test_client()
    uid_a = _create_user(account_app, "alice@example.com", name="Alice")
    uid_b = _create_user(account_app, "bob@example.com", name="Bob")
    _login(account_app, a, uid_a)
    _login(account_app, b, uid_b)
    return account_app, a, uid_a, b, uid_b


# =================================================================
# GET /api/account/profile
# =================================================================

def test_get_profile_returns_only_the_users_own_public_fields(authed):
    app, client, uid = authed
    response = client.get("/api/account/profile")
    assert response.status_code == 200
    profile = response.get_json()["profile"]
    assert set(profile) == {"name", "email", "email_verified", "created_at"}
    assert profile["name"] == "Solo User"
    assert profile["email"] == "solo@example.com"
    assert profile["email_verified"] is True
    # aware-UTC ISO string: a browser must not be able to misread it as local time
    assert profile["created_at"].endswith("+00:00")


def test_get_profile_never_leaks_id_or_password_material(authed):
    app, client, uid = authed
    raw = client.get("/api/account/profile").get_data(as_text=True)
    assert "password" not in raw.lower()
    assert "$argon2" not in raw
    assert '"id"' not in raw and "user_id" not in raw


def test_get_profile_is_not_cacheable(authed):
    app, client, uid = authed
    assert client.get("/api/account/profile").headers["Cache-Control"] == "no-store"
    assert _patch(client, {"name": "X"}).headers["Cache-Control"] == "no-store"


def test_get_profile_requires_authentication(account_app):
    response = account_app.test_client().get("/api/account/profile")
    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication required"}


def test_get_profile_rejects_garbage_and_revoked_sessions(authed):
    import config
    from database import db
    from models import UserSession

    app, client, uid = authed
    bad = app.test_client()
    bad.set_cookie(config.Config.SESSION_COOKIE_NAME, "not-a-real-token")
    assert bad.get("/api/account/profile").status_code == 401

    with app.app_context():
        for row in UserSession.query.filter_by(user_id=uid).all():
            from datetime import datetime, timezone
            row.revoked_at = datetime.now(timezone.utc)
            db.session.add(row)
        db.session.commit()
    assert client.get("/api/account/profile").status_code == 401


def test_get_profile_ignores_user_id_query_parameter(two_users):
    app, a, uid_a, b, uid_b = two_users
    profile = a.get(f"/api/account/profile?user_id={uid_b}&email=bob@example.com").get_json()["profile"]
    assert profile["email"] == "alice@example.com"


def test_get_profile_without_database_configured_is_a_normal_401(authed, monkeypatch):
    import config

    app, client, uid = authed
    monkeypatch.setattr(config.Config, "DATABASE_URL", None)
    assert client.get("/api/account/profile").status_code == 401


# =================================================================
# PATCH /api/account/profile
# =================================================================

def test_update_name_persists_and_returns_updated_profile(authed):
    app, client, uid = authed
    response = _patch(client, {"name": "  New Display Name  "})
    assert response.status_code == 200
    body = response.get_json()
    assert body["message"] == "Profile updated"
    assert body["profile"]["name"] == "New Display Name"          # trimmed
    assert _user_row(app, uid)["name"] == "New Display Name"
    assert client.get("/api/account/profile").get_json()["profile"]["name"] == "New Display Name"


def test_update_name_accepts_unicode_and_exact_max_length(authed):
    app, client, uid = authed
    assert _patch(client, {"name": "Zoë 李雷 🙂"}).status_code == 200
    assert _user_row(app, uid)["name"] == "Zoë 李雷 🙂"
    assert _patch(client, {"name": "a" * 120}).status_code == 200


def test_update_name_leaves_every_other_column_alone(authed):
    app, client, uid = authed
    before = _user_row(app, uid)
    _patch(client, {"name": "Changed"})
    after = _user_row(app, uid)
    assert after["email"] == before["email"]
    assert after["password_hash"] == before["password_hash"]
    assert after["email_verified"] == before["email_verified"]


def test_update_profile_requires_authentication(account_app):
    uid = _create_user(account_app, "x@example.com", name="Untouched")
    response = _patch(account_app.test_client(), {"name": "Hacked"})
    assert response.status_code == 401
    assert _user_row(account_app, uid)["name"] == "Untouched"


def test_update_profile_rejects_invalid_names_and_changes_nothing(authed):
    app, client, uid = authed
    bad_bodies = [
        {}, {"name": ""}, {"name": "   "}, {"name": None}, {"name": 123},
        {"name": ["x"]}, {"name": {"a": 1}}, {"name": True}, {"name": "a" * 121},
    ]
    for body in bad_bodies:
        response = _patch(client, body)
        assert response.status_code == 400, body
        payload = response.get_json()
        assert payload["error"] == "Invalid profile data", body
        assert payload["details"] and all(isinstance(d, str) for d in payload["details"]), body
        assert _user_row(app, uid)["name"] == "Solo User", body


def test_update_profile_rejects_non_object_and_non_json_bodies(authed):
    app, client, uid = authed
    assert client.patch("/api/account/profile", json=["name"]).status_code == 400
    assert client.patch("/api/account/profile", json="Solo").status_code == 400
    assert client.patch("/api/account/profile", data="{}", content_type="text/plain").status_code == 400
    # a text/plain body must NOT be parsed even if it is valid JSON (cross-site "simple request")
    assert client.patch("/api/account/profile", data=json.dumps({"name": "Smuggled"}),
                        content_type="text/plain").status_code == 400
    assert client.patch("/api/account/profile", data="not json", content_type="application/json").status_code == 400
    assert _user_row(app, uid)["name"] == "Solo User"


def test_update_profile_rejects_fields_that_are_not_editable(authed):
    app, client, uid = authed
    before = _user_row(app, uid)
    for extra in ({"email": "evil@example.com"}, {"id": 999}, {"user_id": 999}, {"email_verified": False},
                  {"password_hash": "x"}, {"created_at": "2000-01-01"}, {"is_admin": True}):
        response = _patch(client, {"name": "Renamed", **extra})
        assert response.status_code == 400, extra
        assert response.get_json()["details"] == ["Only name can be changed here"]
        # client-supplied key names are never echoed back as JSON keys / values
        assert set(response.get_json()) == {"error", "details"}
    assert _user_row(app, uid) == before


def test_update_profile_cannot_touch_another_users_account(two_users):
    app, a, uid_a, b, uid_b = two_users
    bob_before = _user_row(app, uid_b)
    for body in ({"name": "Pwned", "id": uid_b}, {"name": "Pwned", "user_id": uid_b},
                 {"name": "Pwned", "email": "bob@example.com"}):
        assert _patch(a, body).status_code == 400
    assert _user_row(app, uid_b) == bob_before
    assert _user_row(app, uid_a)["name"] == "Alice"
    # and a valid update by Alice only ever changes Alice
    assert _patch(a, {"name": "Alice Renamed"}).status_code == 200
    assert _user_row(app, uid_b) == bob_before
    assert b.get("/api/account/profile").get_json()["profile"]["name"] == "Bob"


def test_update_profile_database_error_is_generic_500_and_rolls_back(authed, monkeypatch):
    from database import db
    from sqlalchemy.exc import SQLAlchemyError

    app, client, uid = authed

    def boom():
        raise SQLAlchemyError("secret-sql INSERT ... parameters: ('Leaky Name',) postgresql://u:p@host/db")

    real_commit = db.session.commit
    monkeypatch.setattr(db.session, "commit", boom)
    response = _patch(client, {"name": "Leaky Name"})
    assert response.status_code == 500
    raw = response.get_data(as_text=True)
    assert response.get_json() == {"error": "Something went wrong. Please try again."}
    assert "secret-sql" not in raw and "postgresql" not in raw and "Leaky" not in raw
    # restore ONLY commit - monkeypatch.undo() would also revert the fixture's DATABASE_URL patch
    monkeypatch.setattr(db.session, "commit", real_commit)
    assert _user_row(app, uid)["name"] == "Solo User"
    assert client.get("/api/account/profile").status_code == 200   # session still fine afterwards


def test_validate_name_matches_the_rules_registration_applies():
    from validators import validate_name, validate_registration_payload

    samples = ["Ok", "  padded  ", "", "   ", None, 5, [], "a" * 120, "a" * 121]
    for sample in samples:
        reg_errors, _ = validate_registration_payload(
            {"name": sample, "email": "a@b.co", "password": "longenough1"})
        reg_name_errors = [e for e in reg_errors if e.startswith("name ")]
        assert validate_name(sample) == reg_name_errors, sample


# =================================================================
# POST /api/account/change-password
# =================================================================

def test_change_password_success_updates_hash_and_old_password_stops_working(authed):
    from security import verify_password

    app, client, uid = authed
    old_hash = _user_row(app, uid)["password_hash"]
    response = _change(client, {"current_password": OLD_PW, "new_password": NEW_PW})
    assert response.status_code == 200
    assert "Password updated" in response.get_json()["message"]

    new_hash = _user_row(app, uid)["password_hash"]
    assert new_hash != old_hash
    # same scheme the project's hasher (security.hash_password) produced for the old
    # password - Argon2id in production, the offline PBKDF2 stand-in in the sandbox
    assert _hash_scheme(new_hash) == _hash_scheme(old_hash)
    assert NEW_PW not in new_hash and OLD_PW not in new_hash
    assert verify_password(NEW_PW, new_hash) is True
    assert verify_password(OLD_PW, new_hash) is False


def test_change_password_response_never_echoes_passwords(authed):
    app, client, uid = authed
    raw = _change(client, {"current_password": OLD_PW, "new_password": NEW_PW}).get_data(as_text=True)
    assert OLD_PW not in raw and NEW_PW not in raw and "argon2" not in raw
    raw = _change(client, {"current_password": "wrong-password-1", "new_password": NEW_PW}).get_data(as_text=True)
    assert "wrong-password-1" not in raw and NEW_PW not in raw


def test_change_password_signs_out_other_devices_but_keeps_this_one(account_app):
    uid = _create_user(account_app, "multi@example.com", name="Multi")
    here, phone, laptop = account_app.test_client(), account_app.test_client(), account_app.test_client()
    for c in (here, phone, laptop):
        _login(account_app, c, uid)
    other_uid = _create_user(account_app, "other@example.com", name="Other")
    other_client = account_app.test_client()
    _login(account_app, other_client, other_uid)
    assert _active_sessions(account_app, uid) == 3

    assert _change(here, {"current_password": OLD_PW, "new_password": NEW_PW}).status_code == 200

    assert _active_sessions(account_app, uid) == 1
    assert here.get("/api/account/profile").status_code == 200       # the page you're on stays signed in
    assert phone.get("/api/account/profile").status_code == 401
    assert laptop.get("/api/account/profile").status_code == 401
    # another user's sessions are never touched
    assert _active_sessions(account_app, other_uid) == 1
    assert other_client.get("/api/account/profile").status_code == 200


def test_change_password_new_password_works_through_the_existing_login_endpoint(account_app):
    uid = _create_user(account_app, "flow@example.com", name="Flow")
    client = account_app.test_client()
    _login(account_app, client, uid)
    assert _change(client, {"current_password": OLD_PW, "new_password": NEW_PW}).status_code == 200

    fresh = account_app.test_client()
    assert fresh.post("/api/auth/login", json={"email": "flow@example.com", "password": OLD_PW}).status_code == 401
    assert fresh.post("/api/auth/login", json={"email": "flow@example.com", "password": NEW_PW}).status_code == 200


def test_change_password_wrong_current_password_is_400_not_401(authed):
    app, client, uid = authed
    before = _user_row(app, uid)["password_hash"]
    response = _change(client, {"current_password": "definitely-wrong", "new_password": NEW_PW})
    # 400, not 401: the frontend treats 401 as "session lost -> go to login"
    assert response.status_code == 400
    assert response.get_json() == {"error": "Current password is incorrect"}
    assert _user_row(app, uid)["password_hash"] == before
    assert _active_sessions(app, uid) == 1
    assert client.get("/api/account/profile").status_code == 200


def test_change_password_rejects_invalid_submissions_and_changes_nothing(authed):
    app, client, uid = authed
    before = _user_row(app, uid)["password_hash"]
    bad_bodies = [
        {},
        {"current_password": OLD_PW},                                  # new missing
        {"new_password": NEW_PW},                                      # current missing
        {"current_password": "", "new_password": NEW_PW},
        {"current_password": None, "new_password": NEW_PW},
        {"current_password": 12345678, "new_password": NEW_PW},
        {"current_password": OLD_PW, "new_password": ""},
        {"current_password": OLD_PW, "new_password": "short"},        # < 8
        {"current_password": OLD_PW, "new_password": "x" * 129},      # > 128
        {"current_password": OLD_PW, "new_password": None},
        {"current_password": OLD_PW, "new_password": ["a"] * 9},
        {"current_password": OLD_PW, "new_password": OLD_PW},         # unchanged
    ]
    for body in bad_bodies:
        response = _change(client, body)
        assert response.status_code == 400, body
        assert response.get_json()["error"] == "Invalid password data", body
        assert all(isinstance(d, str) for d in response.get_json()["details"]), body
        assert _user_row(app, uid)["password_hash"] == before, body
    assert _active_sessions(app, uid) == 1


def test_change_password_uses_the_shared_password_rules(authed):
    from validators import MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

    app, client, uid = authed
    assert _change(client, {"current_password": OLD_PW, "new_password": "p" * (MIN_PASSWORD_LENGTH - 1)}).status_code == 400
    assert _change(client, {"current_password": OLD_PW, "new_password": "q" * MAX_PASSWORD_LENGTH}).status_code == 200


def test_change_password_rejects_non_object_non_json_and_extra_fields(authed):
    app, client, uid = authed
    before = _user_row(app, uid)
    assert client.post("/api/account/change-password", json=["x"]).status_code == 400
    assert client.post("/api/account/change-password", data="nope", content_type="application/json").status_code == 400
    assert client.post("/api/account/change-password",
                       data=json.dumps({"current_password": OLD_PW, "new_password": NEW_PW}),
                       content_type="text/plain").status_code == 400
    for extra in ({"user_id": 2}, {"email": "x@y.zz"}, {"password_hash": "z"}, {"confirm_password": NEW_PW}):
        assert _change(client, {"current_password": OLD_PW, "new_password": NEW_PW, **extra}).status_code == 400
    assert _user_row(app, uid) == before


def test_change_password_requires_authentication(account_app):
    uid = _create_user(account_app, "anon@example.com", name="Anon")
    before = _user_row(account_app, uid)["password_hash"]
    response = _change(account_app.test_client(), {"current_password": OLD_PW, "new_password": NEW_PW})
    assert response.status_code == 401
    assert _user_row(account_app, uid)["password_hash"] == before


def test_change_password_cannot_change_another_users_password(two_users):
    app, a, uid_a, b, uid_b = two_users
    bob_before = _user_row(app, uid_b)["password_hash"]
    # Alice knows Bob's id and even his password; the id is never read, so only Alice's password can change
    response = _change(a, {"current_password": OLD_PW, "new_password": NEW_PW, "user_id": uid_b})
    assert response.status_code == 400
    assert _user_row(app, uid_b)["password_hash"] == bob_before
    assert _change(a, {"current_password": OLD_PW, "new_password": NEW_PW}).status_code == 200
    assert _user_row(app, uid_b)["password_hash"] == bob_before
    assert _active_sessions(app, uid_b) == 1
    assert b.get("/api/account/profile").status_code == 200


def test_change_password_database_error_is_generic_500_and_changes_nothing(account_app, monkeypatch):
    from database import db
    from sqlalchemy.exc import SQLAlchemyError

    uid = _create_user(account_app, "dberr@example.com", name="DbErr")
    here, other = account_app.test_client(), account_app.test_client()
    _login(account_app, here, uid)
    _login(account_app, other, uid)
    before = _user_row(account_app, uid)["password_hash"]

    def boom():
        raise SQLAlchemyError("UPDATE users SET password_hash='$argon2id$LEAK' parameters postgresql://u:p@host/db")

    real_commit = db.session.commit
    monkeypatch.setattr(db.session, "commit", boom)
    response = _change(here, {"current_password": OLD_PW, "new_password": NEW_PW})
    assert response.status_code == 500
    raw = response.get_data(as_text=True)
    assert response.get_json() == {"error": "Something went wrong. Please try again."}
    assert "LEAK" not in raw and "postgresql" not in raw and "argon2" not in raw
    monkeypatch.setattr(db.session, "commit", real_commit)

    assert _user_row(account_app, uid)["password_hash"] == before      # unchanged
    assert _active_sessions(account_app, uid) == 2                      # nothing was revoked
    assert other.get("/api/account/profile").status_code == 200


def test_database_failure_logs_only_the_exception_class_never_sql_or_secrets(authed, monkeypatch):
    import logging

    from database import db
    from sqlalchemy.exc import SQLAlchemyError

    app, client, uid = authed
    records = []

    class _Collect(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Collect()
    log = logging.getLogger("account_routes")
    log.addHandler(handler)
    try:
        def boom():
            raise SQLAlchemyError("parameters: ('" + NEW_PW + "', '$argon2id$HASHLEAK')")

        monkeypatch.setattr(db.session, "commit", boom)
        assert _change(client, {"current_password": OLD_PW, "new_password": NEW_PW}).status_code == 500
    finally:
        log.removeHandler(handler)
    joined = " ".join(records)
    assert "SQLAlchemyError" in joined or "Error" in joined
    assert NEW_PW not in joined and OLD_PW not in joined and "HASHLEAK" not in joined


# =================================================================
# wiring / regression
# =================================================================

def test_account_routes_are_registered_in_the_real_app(flask_app_module):
    rules = {(r.rule, m) for r in flask_app_module.app.url_map.iter_rules() for m in r.methods}
    assert ("/api/account/profile", "GET") in rules
    assert ("/api/account/profile", "PATCH") in rules
    assert ("/api/account/change-password", "POST") in rules
    # existing routes are still there
    assert ("/api/dashboard/", "GET") in rules or ("/api/dashboard", "GET") in rules
    assert ("/api/auth/me", "GET") in rules


def test_auth_me_is_unchanged_and_still_reports_the_same_user_after_a_rename(authed):
    app, client, uid = authed
    assert _patch(client, {"name": "Renamed"}).status_code == 200
    user = client.get("/api/auth/me").get_json()["user"]
    assert user["name"] == "Renamed" and user["email"] == "solo@example.com"
