"""
Phase 5: POST /api/auth/login tests.

Uses registration_client + mock_email_service + db_test_app (see
conftest.py) - isolated in-memory database per test, no real SMTP, no
real network. See conftest.py's flask_sqlalchemy shim docstring for what
is/isn't validated in this offline sandbox vs. a real environment.
"""

from datetime import datetime, timedelta, timezone

import models


def _extract_token_from_email(mock_email_service):
    body = mock_email_service[-1]["body"]
    return body.split("token=")[1].split()[0].strip()


def _register_and_verify(registration_client, mock_email_service, payload):
    """Registers a user and verifies their email, leaving them ready to
    log in (but not yet logged in)."""
    registration_client.post("/api/auth/register", json=payload)
    raw_token = _extract_token_from_email(mock_email_service)
    registration_client.get(f"/api/auth/verify-email?token={raw_token}")


# --- A: successful login ----------------------------------------------------

def test_login_with_correct_credentials_and_verified_email_succeeds(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)

    response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": valid_registration_payload["password"],
    })

    assert response.status_code == 200
    body = response.get_json()
    assert body["user"]["email"] == "ada@example.com"
    assert body["user"]["email_verified"] is True


def test_successful_login_sets_a_session_cookie(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": valid_registration_payload["password"],
    })
    set_cookie_header = response.headers.get("Set-Cookie", "")
    assert "ppt_session=" in set_cookie_header


def test_login_response_never_includes_password_or_hash(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": valid_registration_payload["password"],
    })
    body_text = response.get_data(as_text=True)
    assert "password_hash" not in body_text
    assert valid_registration_payload["password"] not in body_text
    assert set(response.get_json()["user"].keys()) == {"id", "name", "email", "email_verified"}


# --- B: failed login ---------------------------------------------------------

def test_login_with_wrong_password_returns_generic_401(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": "definitely-the-wrong-password",
    })
    assert response.status_code == 401
    assert response.get_json() == {"error": "Invalid email or password"}


def test_login_with_nonexistent_email_returns_identical_generic_401(registration_client):
    response = registration_client.post("/api/auth/login", json={
        "email": "nobody-registered@example.com",
        "password": "whatever-password-123",
    })
    assert response.status_code == 401
    # Byte-identical to the wrong-password case above - no enumeration.
    assert response.get_json() == {"error": "Invalid email or password"}


def test_login_with_malformed_input_returns_generic_401(registration_client):
    response = registration_client.post("/api/auth/login")  # no body at all
    assert response.status_code == 401
    assert response.get_json() == {"error": "Invalid email or password"}


def test_login_with_missing_password_field_returns_generic_401(registration_client):
    response = registration_client.post("/api/auth/login", json={"email": "a@b.com"})
    assert response.status_code == 401


def test_login_with_missing_email_field_returns_generic_401(registration_client):
    response = registration_client.post("/api/auth/login", json={"password": "somepassword"})
    assert response.status_code == 401


def test_login_with_non_string_fields_returns_generic_401(registration_client):
    response = registration_client.post("/api/auth/login", json={"email": 12345, "password": ["x"]})
    assert response.status_code == 401


def test_login_with_oversized_password_returns_generic_401(registration_client):
    response = registration_client.post("/api/auth/login", json={
        "email": "a@b.com",
        "password": "a" * 5000,
    })
    assert response.status_code == 401


def test_login_does_not_reveal_existence_via_response_shape(
    registration_client, mock_email_service, valid_registration_payload
):
    """The wrong-password (real account) and nonexistent-email cases must
    be indistinguishable in status code AND body - checked together
    explicitly, not just individually above."""
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)

    wrong_password_response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": "wrong-password-entirely",
    })
    unknown_email_response = registration_client.post("/api/auth/login", json={
        "email": "totally-unregistered@example.com",
        "password": "wrong-password-entirely",
    })

    assert wrong_password_response.status_code == unknown_email_response.status_code
    assert wrong_password_response.get_json() == unknown_email_response.get_json()


# --- C: unverified account ----------------------------------------------------

def test_login_with_unverified_account_and_correct_password_is_rejected(
    registration_client, mock_email_service, valid_registration_payload
):
    # Register but deliberately do NOT verify.
    registration_client.post("/api/auth/register", json=valid_registration_payload)

    response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": valid_registration_payload["password"],
    })

    assert response.status_code == 403
    assert "verify" in response.get_json()["error"].lower()


def test_login_with_unverified_account_creates_no_session(
    registration_client, mock_email_service, valid_registration_payload
):
    registration_client.post("/api/auth/register", json=valid_registration_payload)
    response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": valid_registration_payload["password"],
    })
    assert "Set-Cookie" not in response.headers or "ppt_session=" not in response.headers.get("Set-Cookie", "")

    # Confirm no authenticated session exists at all afterwards.
    me_response = registration_client.get("/api/auth/me")
    assert me_response.status_code == 401


def test_login_with_unverified_account_and_wrong_password_still_gets_generic_error(
    registration_client, mock_email_service, valid_registration_payload
):
    """Password correctness is checked BEFORE the verified-status check -
    a wrong-password guess against an unverified account must get the
    same generic 401 as any other wrong-password guess, not the 403
    (which would otherwise leak "this email is unverified but exists")."""
    registration_client.post("/api/auth/register", json=valid_registration_payload)

    response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": "wrong-password-entirely",
    })

    assert response.status_code == 401
    assert response.get_json() == {"error": "Invalid email or password"}


# --- F: last_login ------------------------------------------------------------

def test_successful_login_updates_last_login(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    with db_test_app.app_context():
        pass  # ensure app context module is imported before assertion below

    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)

    with db_test_app.app_context():
        user_before = models.User.query.filter_by(email="ada@example.com").first()
        assert user_before.last_login is None

    registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": valid_registration_payload["password"],
    })

    with db_test_app.app_context():
        user_after = models.User.query.filter_by(email="ada@example.com").first()
        assert user_after.last_login is not None


def test_failed_login_does_not_update_last_login(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)

    registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": "wrong-password-entirely",
    })

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        assert user.last_login is None


# --- G: cookie/security properties -------------------------------------------

def test_session_cookie_is_httponly(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": valid_registration_payload["password"],
    })
    set_cookie = response.headers.get("Set-Cookie", "")
    assert "HttpOnly" in set_cookie


def test_session_cookie_has_samesite_and_secure_configured(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": valid_registration_payload["password"],
    })
    set_cookie = response.headers.get("Set-Cookie", "")
    # Default config (see backend/config.py): SameSite=None + Secure,
    # the only valid pairing for this app's real cross-origin deployment.
    assert "SameSite=None" in set_cookie
    assert "Secure" in set_cookie


def test_session_cookie_has_an_expiration(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": valid_registration_payload["password"],
    })
    set_cookie = response.headers.get("Set-Cookie", "")
    assert "Expires=" in set_cookie


def test_session_cookie_value_is_opaque_not_user_data(
    registration_client, mock_email_service, valid_registration_payload
):
    """The cookie value itself must not contain the email, name, or any
    recognizable user data - just an opaque random token."""
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": valid_registration_payload["password"],
    })
    set_cookie = response.headers.get("Set-Cookie", "")
    assert "ada" not in set_cookie.lower()
    assert "lovelace" not in set_cookie.lower()


# --- database-not-configured -------------------------------------------------

def test_login_without_database_configured_returns_503(no_db_client):
    response = no_db_client.post("/api/auth/login", json={"email": "a@b.com", "password": "x"})
    assert response.status_code == 503


def test_me_without_database_configured_returns_401(no_db_client):
    """Deterministic now that no_db_client genuinely forces
    DATABASE_URL unset (previously this only worked by coincidence
    against whatever the real .env happened to contain): get_current_user()
    checks Config.DATABASE_URL first, before ever looking at a cookie, so
    login_required's 401 fires reliably here - no more lenient
    "401 or 503" needed."""
    response = no_db_client.get("/api/auth/me")
    assert response.status_code == 401
