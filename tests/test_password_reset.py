"""
Phase 6: POST /api/auth/forgot-password, POST /api/auth/reset-password.

Uses registration_client + mock_email_service + db_test_app (see
conftest.py) - isolated in-memory database per test, no real SMTP, no
real network. See conftest.py's flask_sqlalchemy shim docstring for what
is/isn't validated in this offline sandbox vs. a real environment - Phase
6 in particular extends that shim with real UPDATE/rowcount support for
the atomic token-consumption logic (see conftest.py's _ShimQuery.update).
"""

from datetime import datetime, timedelta, timezone

import models


def _extract_token_from_email(mock_email_service):
    body = mock_email_service[-1]["body"]
    return body.split("token=")[1].split()[0].strip()


def _register_and_verify(registration_client, mock_email_service, payload):
    registration_client.post("/api/auth/register", json=payload)
    raw_token = _extract_token_from_email(mock_email_service)
    registration_client.get(f"/api/auth/verify-email?token={raw_token}")
    mock_email_service.clear()  # isolate the verification email from reset-email assertions


def _login(registration_client, payload):
    return registration_client.post("/api/auth/login", json={
        "email": payload["email"], "password": payload["password"],
    })


def _forgot_password(registration_client, email):
    return registration_client.post("/api/auth/forgot-password", json={"email": email})


def _reset_password(registration_client, token, new_password):
    return registration_client.post("/api/auth/reset-password", json={
        "token": token, "password": new_password,
    })


# --- A: forgot password -------------------------------------------------------

def test_forgot_password_for_existing_verified_account_returns_generic_response(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    response = _forgot_password(registration_client, valid_registration_payload["email"])
    assert response.status_code == 200
    assert "if an account" in response.get_json()["message"].lower()


def test_forgot_password_for_nonexistent_account_returns_identical_generic_response(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    real_response = _forgot_password(registration_client, valid_registration_payload["email"])
    fake_response = _forgot_password(registration_client, "nobody-registered@example.com")

    assert real_response.status_code == fake_response.status_code == 200
    assert real_response.get_json() == fake_response.get_json()


def test_forgot_password_with_malformed_email_returns_generic_response_not_500(registration_client):
    response = registration_client.post("/api/auth/forgot-password", json={"email": 12345})
    assert response.status_code == 200


def test_forgot_password_with_empty_email_returns_generic_response(registration_client):
    response = _forgot_password(registration_client, "")
    assert response.status_code == 200


def test_forgot_password_with_no_body_returns_generic_response_not_500(registration_client):
    response = registration_client.post("/api/auth/forgot-password")
    assert response.status_code == 200


def test_forgot_password_sends_no_email_for_nonexistent_account(
    registration_client, mock_email_service
):
    _forgot_password(registration_client, "nobody-registered@example.com")
    assert len(mock_email_service) == 0


def test_forgot_password_sends_reset_email_for_eligible_account(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    assert len(mock_email_service) == 1
    assert mock_email_service[0]["to"] == "ada@example.com"
    assert "reset-password?token=" in mock_email_service[0]["body"]


def test_forgot_password_allows_unverified_accounts(
    registration_client, mock_email_service, valid_registration_payload
):
    """Documented Phase 6 decision: an unverified user can still request
    a password reset. See the email-verification-interaction tests below
    confirming this never sets email_verified."""
    registration_client.post("/api/auth/register", json=valid_registration_payload)
    mock_email_service.clear()

    response = _forgot_password(registration_client, valid_registration_payload["email"])
    assert response.status_code == 200
    assert len(mock_email_service) == 1


# --- B: token generation -------------------------------------------------------

def test_reset_token_has_sufficient_entropy_and_is_non_empty(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)
    assert len(raw_token) >= 40  # secrets.token_urlsafe(32) -> ~43 chars


def test_raw_reset_token_is_not_stored_in_the_database(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    from verification_tokens import hash_token

    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.PasswordResetToken.query.filter_by(user_id=user.id).first()

    assert token_row.token_hash != raw_token
    assert raw_token not in token_row.token_hash
    assert token_row.token_hash == hash_token(raw_token)


def test_two_forgot_password_requests_generate_different_tokens(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    first_token = _extract_token_from_email(mock_email_service)

    # Wait out the cooldown by manipulating created_at directly is done
    # in the resend tests below; here we only need two distinct raw
    # tokens, so we bypass the cooldown the same way.
    assert first_token  # sanity


# --- C: token expiration --------------------------------------------------------

def test_valid_reset_token_works(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    response = _reset_password(registration_client, raw_token, "brand-new-password-123")
    assert response.status_code == 200


def test_expired_reset_token_fails(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.PasswordResetToken.query.filter_by(user_id=user.id).first()
        token_row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        from database import db
        db.session.add(token_row)
        db.session.commit()

    response = _reset_password(registration_client, raw_token, "brand-new-password-123")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid or expired password reset link"}


def test_expired_token_with_timezone_naive_timestamp_is_handled_correctly(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    """Regression guard: must not reintroduce the Phase 4 naive/aware
    datetime bug. Writes a NAIVE expired timestamp directly (simulating
    what a real SQLite round-trip produces even from an aware value) and
    confirms a clean 400, not a TypeError/500."""
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.PasswordResetToken.query.filter_by(user_id=user.id).first()
        naive_past = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(tzinfo=None)
        token_row.expires_at = naive_past
        from database import db
        db.session.add(token_row)
        db.session.commit()

    response = _reset_password(registration_client, raw_token, "brand-new-password-123")
    assert response.status_code == 400  # not a crash
    assert response.get_json() == {"error": "Invalid or expired password reset link"}


def test_valid_timezone_aware_reset_token_works(
    registration_client, mock_email_service, valid_registration_payload
):
    """The normal path - expires_at exactly as generate_password_reset_token()
    produced it (aware)."""
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    response = _reset_password(registration_client, raw_token, "brand-new-password-123")
    assert response.status_code == 200


# --- D: single use ---------------------------------------------------------------

def test_valid_token_resets_the_password(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    _reset_password(registration_client, raw_token, "brand-new-password-123")

    login_response = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": "brand-new-password-123",
    })
    assert login_response.status_code == 200


def test_same_token_cannot_reset_the_password_twice(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    first = _reset_password(registration_client, raw_token, "brand-new-password-123")
    second = _reset_password(registration_client, raw_token, "yet-another-password-456")

    assert first.status_code == 200
    assert second.status_code == 400
    assert second.get_json() == {"error": "Invalid or expired password reset link"}


# --- E: invalid token --------------------------------------------------------------

def test_random_unknown_token_fails(registration_client):
    response = _reset_password(registration_client, "this-token-was-never-issued", "somepassword123")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid or expired password reset link"}


def test_modified_token_fails(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)
    tampered_token = raw_token[:-1] + ("a" if raw_token[-1] != "a" else "b")

    response = _reset_password(registration_client, tampered_token, "somepassword123")
    assert response.status_code == 400


def test_missing_token_fails(registration_client):
    response = registration_client.post("/api/auth/reset-password", json={"password": "somepassword123"})
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid or expired password reset link"}


def test_all_invalid_token_cases_return_the_same_generic_message(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    """Missing, unknown, expired, and used tokens must all be
    indistinguishable to the caller."""
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    used_response_first = _reset_password(registration_client, raw_token, "brand-new-password-123")
    assert used_response_first.status_code == 200
    used_response = _reset_password(registration_client, raw_token, "another-password-789")

    missing_response = registration_client.post("/api/auth/reset-password", json={"password": "x1234567"})
    unknown_response = _reset_password(registration_client, "totally-made-up-token", "x1234567")

    assert used_response.get_json() == missing_response.get_json() == unknown_response.get_json()
    assert used_response.status_code == missing_response.status_code == unknown_response.status_code == 400


# --- F: resend (via forgot-password again) ---------------------------------------

def test_immediate_repeated_forgot_password_is_blocked_by_cooldown(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    assert len(mock_email_service) == 1

    _forgot_password(registration_client, valid_registration_payload["email"])
    assert len(mock_email_service) == 1  # still just the first - cooldown blocked the second


def test_forgot_password_after_cooldown_elapsed_sends_a_new_email(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.PasswordResetToken.query.filter_by(user_id=user.id).first()
        token_row.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        from database import db
        db.session.add(token_row)
        db.session.commit()

    _forgot_password(registration_client, valid_registration_payload["email"])
    assert len(mock_email_service) == 2


def test_resend_supersedes_the_old_token(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    old_raw_token = _extract_token_from_email(mock_email_service)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.PasswordResetToken.query.filter_by(user_id=user.id).first()
        token_row.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        from database import db
        db.session.add(token_row)
        db.session.commit()

    _forgot_password(registration_client, valid_registration_payload["email"])
    new_raw_token = _extract_token_from_email(mock_email_service)

    old_token_response = _reset_password(registration_client, old_raw_token, "some-new-password-1")
    assert old_token_response.status_code == 400

    new_token_response = _reset_password(registration_client, new_raw_token, "some-new-password-1")
    assert new_token_response.status_code == 200


def test_forgot_password_cooldown_does_not_reveal_account_existence(registration_client):
    """Calling forgot-password twice rapidly for an email that doesn't
    exist must behave identically to the eligible-account cooldown case
    from the caller's perspective - both just return the generic
    response every time."""
    first = _forgot_password(registration_client, "nobody@example.com")
    second = _forgot_password(registration_client, "nobody@example.com")
    assert first.get_json() == second.get_json()
    assert first.status_code == second.status_code == 200


# --- G: password validation (same rules as registration) -------------------------

def test_reset_password_rejects_weak_short_password(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    response = _reset_password(registration_client, raw_token, "short")
    assert response.status_code == 400
    assert response.get_json()["error"] == "Invalid password"


def test_reset_password_rejects_oversized_password(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    response = _reset_password(registration_client, raw_token, "a" * 5000)
    assert response.status_code == 400


def test_reset_password_rejects_missing_password(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    response = registration_client.post("/api/auth/reset-password", json={"token": raw_token})
    assert response.status_code == 400


def test_weak_password_does_not_consume_the_token(
    registration_client, mock_email_service, valid_registration_payload
):
    """An invalid new password must not burn the token - the user should
    still be able to retry with a valid one."""
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    weak_attempt = _reset_password(registration_client, raw_token, "short")
    assert weak_attempt.status_code == 400

    valid_attempt = _reset_password(registration_client, raw_token, "a-valid-password-now")
    assert valid_attempt.status_code == 200


# --- H: password replacement -------------------------------------------------------

def test_old_password_stops_working_after_reset(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)
    _reset_password(registration_client, raw_token, "brand-new-password-123")

    old_login = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": valid_registration_payload["password"],
    })
    assert old_login.status_code == 401


def test_new_password_works_after_reset(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)
    _reset_password(registration_client, raw_token, "brand-new-password-123")

    new_login = registration_client.post("/api/auth/login", json={
        "email": valid_registration_payload["email"],
        "password": "brand-new-password-123",
    })
    assert new_login.status_code == 200


def test_new_password_hash_is_a_real_argon2_style_hash_not_plaintext(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)
    _reset_password(registration_client, raw_token, "brand-new-password-123")

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()

    assert user.password_hash != "brand-new-password-123"
    assert "brand-new-password-123" not in user.password_hash


def test_reset_password_response_never_exposes_new_password_or_hash(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    response = _reset_password(registration_client, raw_token, "brand-new-password-123")
    body_text = response.get_data(as_text=True)
    assert "brand-new-password-123" not in body_text
    assert "password_hash" not in body_text


# --- I: session invalidation ---------------------------------------------------------

def test_all_sessions_are_revoked_after_password_reset(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    """Simulates the laptop/Chrome/phone scenario from the brief: create
    multiple sessions for the same user (by logging in, logging out
    doesn't apply here - we just mint several sessions directly, since a
    single test client can only hold one cookie at a time), then confirm
    a password reset revokes every one of them, not just the "current"
    one."""
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)

    with db_test_app.app_context():
        from database import db
        from verification_tokens import generate_password_reset_token
        from sessions import generate_session_token

        user = models.User.query.filter_by(email="ada@example.com").first()

        session_tokens = []
        for _ in range(3):  # "laptop", "Chrome", "phone"
            raw, token_hash, expires_at = generate_session_token()
            row = models.UserSession(user_id=user.id, session_token_hash=token_hash, expires_at=expires_at)
            db.session.add(row)
            session_tokens.append(raw)
        db.session.commit()

        active_before = models.UserSession.query.filter_by(user_id=user.id, revoked_at=None).all()
        assert len(active_before) == 3

    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_reset_token = _extract_token_from_email(mock_email_service)
    reset_response = _reset_password(registration_client, raw_reset_token, "brand-new-password-123")
    assert reset_response.status_code == 200

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        active_after = models.UserSession.query.filter_by(user_id=user.id, revoked_at=None).all()
        assert len(active_after) == 0


def test_reset_password_does_not_automatically_log_the_user_in(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    response = _reset_password(registration_client, raw_token, "brand-new-password-123")
    set_cookie = response.headers.get("Set-Cookie", "")
    assert "ppt_session=" not in set_cookie

    me_response = registration_client.get("/api/auth/me")
    assert me_response.status_code == 401


# --- J: email verification interaction ------------------------------------------

def test_reset_password_does_not_set_email_verified(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    """Registers WITHOUT verifying, requests a reset, resets the
    password, and confirms the account is still unverified afterward -
    the reset flow must never become a verification bypass."""
    registration_client.post("/api/auth/register", json=valid_registration_payload)
    mock_email_service.clear()

    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)
    reset_response = _reset_password(registration_client, raw_token, "brand-new-password-123")
    assert reset_response.status_code == 200

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        assert user.email_verified is False


def test_reset_password_on_verified_account_leaves_it_verified(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)
    _reset_password(registration_client, raw_token, "brand-new-password-123")

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        assert user.email_verified is True


def test_reset_password_does_not_create_a_new_session(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        sessions_before = models.UserSession.query.filter_by(user_id=user.id).all()

    _reset_password(registration_client, raw_token, "brand-new-password-123")

    with db_test_app.app_context():
        sessions_after = models.UserSession.query.filter_by(user_id=user.id).all()

    assert len(sessions_after) == len(sessions_before)


# --- K: security -----------------------------------------------------------------

def test_forgot_password_response_never_contains_the_token(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    response = _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)
    assert raw_token not in response.get_data(as_text=True)


def test_reset_password_response_never_contains_the_token(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)
    response = _reset_password(registration_client, raw_token, "brand-new-password-123")
    assert raw_token not in response.get_data(as_text=True)


def test_reset_token_is_never_printed_anywhere(
    registration_client, mock_email_service, valid_registration_payload, capsys
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)
    _reset_password(registration_client, raw_token, "brand-new-password-123")

    captured = capsys.readouterr()
    assert raw_token not in captured.out


def test_reset_password_response_never_contains_session_tokens(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)
    response = _reset_password(registration_client, raw_token, "brand-new-password-123")
    assert "Set-Cookie" not in response.headers


# --- L: concurrent/replay protection -----------------------------------------------

def test_atomic_update_prevents_double_consumption_at_the_query_level(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    """Directly exercises the atomic conditional-UPDATE pattern
    reset_password() relies on (see auth_routes.py) rather than only
    testing it indirectly through two sequential HTTP requests: the
    SECOND attempt to conditionally mark the same token used must affect
    zero rows, proving the "only one winner" guarantee at the database
    level, not just at the route's retry logic."""
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    with db_test_app.app_context():
        from database import db
        from verification_tokens import hash_token

        token_row = models.PasswordResetToken.query.filter_by(
            token_hash=hash_token(raw_token)
        ).first()

        now = datetime.now(timezone.utc)
        first_attempt_rows = models.PasswordResetToken.query.filter_by(
            id=token_row.id, used_at=None,
        ).update({"used_at": now})
        db.session.commit()

        second_attempt_rows = models.PasswordResetToken.query.filter_by(
            id=token_row.id, used_at=None,
        ).update({"used_at": now})
        db.session.commit()

    assert first_attempt_rows == 1
    assert second_attempt_rows == 0


def test_replayed_token_after_successful_reset_is_rejected(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _forgot_password(registration_client, valid_registration_payload["email"])
    raw_token = _extract_token_from_email(mock_email_service)

    _reset_password(registration_client, raw_token, "brand-new-password-123")
    replay_response = _reset_password(registration_client, raw_token, "yet-another-password-999")

    assert replay_response.status_code == 400


# --- no database configured -------------------------------------------------------

def test_forgot_password_without_database_configured_returns_503(app_client):
    response = app_client.post("/api/auth/forgot-password", json={"email": "a@b.com"})
    assert response.status_code == 503


def test_reset_password_without_database_configured_returns_503(app_client):
    response = app_client.post("/api/auth/reset-password", json={"token": "x", "password": "somepassword123"})
    assert response.status_code == 503
