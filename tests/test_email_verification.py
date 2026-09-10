"""
Phase 4: POST /api/auth/register (extended), GET /api/auth/verify-email,
POST /api/auth/resend-verification.

Uses registration_client + mock_email_service (see conftest.py) - no real
SMTP connection, no real database, no network access anywhere in this
file. See conftest.py's flask_sqlalchemy shim docstring for what is and
is not validated when this runs in the offline sandbox vs. a real
environment with flask-sqlalchemy + PostgreSQL installed.
"""

from datetime import datetime, timedelta, timezone

import models
from verification_tokens import hash_token


def _register(registration_client, mock_email_service, payload):
    return registration_client.post("/api/auth/register", json=payload)


# --- 1-3: token created, stored hashed, raw token never stored -------------

def test_registration_creates_a_verification_token(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register(registration_client, mock_email_service, valid_registration_payload)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        tokens = models.EmailVerificationToken.query.filter_by(user_id=user.id).all()

    assert len(tokens) == 1


def test_verification_token_is_stored_hashed(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register(registration_client, mock_email_service, valid_registration_payload)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.EmailVerificationToken.query.filter_by(user_id=user.id).first()

    # 64 hex chars = a SHA-256 digest, not a raw secrets.token_urlsafe(32)
    # value (which is base64url and a different length/alphabet).
    assert len(token_row.token_hash) == 64
    int(token_row.token_hash, 16)  # valid hex


def test_raw_token_is_not_stored_anywhere_in_the_database(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register(registration_client, mock_email_service, valid_registration_payload)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.EmailVerificationToken.query.filter_by(user_id=user.id).first()

    # The raw token was emailed (captured by mock_email_service) - confirm
    # it's genuinely different from what's stored, i.e. what's stored is
    # a hash, not the token itself.
    sent_body = mock_email_service[0]["body"]
    raw_token_in_email = sent_body.split("token=")[1].split()[0].strip()
    assert raw_token_in_email != token_row.token_hash
    assert hash_token(raw_token_in_email) == token_row.token_hash


# --- 4-5: email sent via mocked service, contains the verification link ----

def test_verification_email_is_sent_through_mocked_service(
    registration_client, mock_email_service, valid_registration_payload
):
    _register(registration_client, mock_email_service, valid_registration_payload)
    assert len(mock_email_service) == 1
    assert mock_email_service[0]["to"] == "ada@example.com"


def test_verification_email_contains_a_verification_link(
    registration_client, mock_email_service, valid_registration_payload
):
    _register(registration_client, mock_email_service, valid_registration_payload)
    body = mock_email_service[0]["body"]
    assert "https://test.example/verify-email?token=" in body


# --- 6-7: valid token verifies user, then becomes unusable -----------------

def _extract_token_from_email(mock_email_service):
    body = mock_email_service[-1]["body"]
    return body.split("token=")[1].split()[0].strip()


def test_valid_token_verifies_the_user(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register(registration_client, mock_email_service, valid_registration_payload)
    raw_token = _extract_token_from_email(mock_email_service)

    response = registration_client.get(f"/api/auth/verify-email?token={raw_token}")
    assert response.status_code == 200
    assert response.get_json()["user"]["email_verified"] is True

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
    assert user.email_verified is True


def test_valid_token_becomes_unusable_after_verification(
    registration_client, mock_email_service, valid_registration_payload
):
    _register(registration_client, mock_email_service, valid_registration_payload)
    raw_token = _extract_token_from_email(mock_email_service)

    first = registration_client.get(f"/api/auth/verify-email?token={raw_token}")
    assert first.status_code == 200

    second = registration_client.get(f"/api/auth/verify-email?token={raw_token}")
    assert second.status_code == 400


# --- 8-11: expired / invalid / missing / already-used tokens rejected ------

def test_expired_token_is_rejected(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register(registration_client, mock_email_service, valid_registration_payload)
    raw_token = _extract_token_from_email(mock_email_service)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.EmailVerificationToken.query.filter_by(user_id=user.id).first()
        token_row.expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
        from database import db
        db.session.add(token_row)
        db.session.commit()

    response = registration_client.get(f"/api/auth/verify-email?token={raw_token}")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid or expired verification link"}


def test_invalid_unknown_token_is_rejected(registration_client):
    response = registration_client.get("/api/auth/verify-email?token=this-token-does-not-exist-at-all")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid or expired verification link"}


def test_missing_token_is_rejected(registration_client):
    response = registration_client.get("/api/auth/verify-email")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid or expired verification link"}


def test_already_used_token_is_rejected_with_same_generic_message(
    registration_client, mock_email_service, valid_registration_payload
):
    _register(registration_client, mock_email_service, valid_registration_payload)
    raw_token = _extract_token_from_email(mock_email_service)

    registration_client.get(f"/api/auth/verify-email?token={raw_token}")
    response = registration_client.get(f"/api/auth/verify-email?token={raw_token}")

    assert response.status_code == 400
    # Same message as expired/invalid/missing - no oracle for token state.
    assert response.get_json() == {"error": "Invalid or expired verification link"}


# --- 12: already-verified user handled safely -------------------------------

def test_already_verified_user_clicking_link_again_does_not_error(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register(registration_client, mock_email_service, valid_registration_payload)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.EmailVerificationToken.query.filter_by(user_id=user.id).first()
        raw_for_second_click = _extract_token_from_email(mock_email_service)

    # Verify once normally.
    registration_client.get(f"/api/auth/verify-email?token={raw_for_second_click}")

    # Manually mint a second still-valid token pointing at the now-verified
    # user (simulating "clicked an old email a second time before it
    # expired") and confirm it's handled as a safe no-op, not an error.
    from verification_tokens import generate_verification_token
    from database import db as real_db

    with db_test_app.app_context():
        raw2, hash2, expires2 = generate_verification_token()
        second_token = models.EmailVerificationToken(
            user_id=user.id, token_hash=hash2, expires_at=expires2,
        )
        real_db.session.add(second_token)
        real_db.session.commit()

    response = registration_client.get(f"/api/auth/verify-email?token={raw2}")
    assert response.status_code == 200
    assert response.get_json()["message"] == "Email already verified"
    assert response.get_json()["user"]["email_verified"] is True


# --- 13-15: no sensitive data in emails / responses -------------------------

def test_password_never_appears_in_sent_email(
    registration_client, mock_email_service, valid_registration_payload
):
    _register(registration_client, mock_email_service, valid_registration_payload)
    body = mock_email_service[0]["body"].lower()
    subject = mock_email_service[0]["subject"].lower()
    assert valid_registration_payload["password"].lower() not in body
    assert "password" not in body
    assert "password" not in subject


def test_password_hash_never_appears_in_sent_email(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register(registration_client, mock_email_service, valid_registration_payload)
    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
    body = mock_email_service[0]["body"]
    assert user.password_hash not in body


def test_mail_credentials_never_exposed_in_any_response(
    registration_client, mock_email_service, valid_registration_payload, monkeypatch
):
    import config
    monkeypatch.setattr(config.Config, "MAIL_PASSWORD", "super-secret-smtp-password")
    response = _register(registration_client, mock_email_service, valid_registration_payload)
    assert "super-secret-smtp-password" not in response.get_data(as_text=True)


# --- 16: database relationship (user_id FK) works ---------------------------

def test_token_user_id_correctly_references_the_registered_user(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register(registration_client, mock_email_service, valid_registration_payload)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.EmailVerificationToken.query.filter_by(user_id=user.id).first()

    assert token_row.user_id == user.id
    assert isinstance(user.id, int) and user.id > 0


# --- 19: email failure is handled per the documented policy ----------------

def test_registration_succeeds_even_if_email_sending_fails(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    mock_email_service.should_fail = True
    response = _register(registration_client, mock_email_service, valid_registration_payload)

    assert response.status_code == 201
    body = response.get_json()
    assert "could not be sent" in body["message"]
    assert "resend-verification" in body["message"]

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        tokens = models.EmailVerificationToken.query.filter_by(user_id=user.id).all()
    # Documented policy: account AND token both remain, ready for a
    # resend - nothing was rolled back because of the email failure.
    assert user is not None
    assert len(tokens) == 1


def test_registration_response_never_exposes_smtp_error_details(
    registration_client, mock_email_service, valid_registration_payload
):
    mock_email_service.should_fail = True
    response = _register(registration_client, mock_email_service, valid_registration_payload)
    body_text = response.get_data(as_text=True)
    assert "smtplib" not in body_text
    assert "Traceback" not in body_text


# --- resend-verification --------------------------------------------------

def test_resend_verification_for_unknown_email_returns_generic_response(registration_client):
    response = registration_client.post(
        "/api/auth/resend-verification", json={"email": "nobody@example.com"}
    )
    assert response.status_code == 200
    assert "if an account" in response.get_json()["message"].lower()


def test_resend_verification_sends_a_new_token_and_email(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register(registration_client, mock_email_service, valid_registration_payload)
    assert len(mock_email_service) == 1

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.EmailVerificationToken.query.filter_by(user_id=user.id).first()
        # Force the cooldown to have already elapsed for this test.
        token_row.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        from database import db
        db.session.add(token_row)
        db.session.commit()

    response = registration_client.post(
        "/api/auth/resend-verification", json={"email": "ada@example.com"}
    )
    assert response.status_code == 200
    assert len(mock_email_service) == 2  # original + resend


def test_resend_verification_supersedes_the_previous_token(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    _register(registration_client, mock_email_service, valid_registration_payload)
    old_raw_token = _extract_token_from_email(mock_email_service)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.EmailVerificationToken.query.filter_by(user_id=user.id).first()
        token_row.created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        from database import db
        db.session.add(token_row)
        db.session.commit()

    registration_client.post("/api/auth/resend-verification", json={"email": "ada@example.com"})

    # The old token must no longer work.
    response = registration_client.get(f"/api/auth/verify-email?token={old_raw_token}")
    assert response.status_code == 400


def test_resend_verification_for_already_verified_user_returns_generic_response(
    registration_client, mock_email_service, valid_registration_payload
):
    _register(registration_client, mock_email_service, valid_registration_payload)
    raw_token = _extract_token_from_email(mock_email_service)
    registration_client.get(f"/api/auth/verify-email?token={raw_token}")

    response = registration_client.post(
        "/api/auth/resend-verification", json={"email": "ada@example.com"}
    )
    assert response.status_code == 200
    # Same generic message as the "unknown email" case - no oracle.
    assert "if an account" in response.get_json()["message"].lower()


# --- verify-email without database configured -------------------------------

def test_verify_email_without_database_configured_returns_503(app_client):
    response = app_client.get("/api/auth/verify-email?token=anything")
    assert response.status_code == 503


def test_resend_verification_without_database_configured_returns_503(app_client):
    response = app_client.post("/api/auth/resend-verification", json={"email": "a@b.com"})
    assert response.status_code == 503


# --- timezone regression tests -----------------------------------------------
# Bug: comparing a timezone-naive expires_at/created_at (as returned by
# some real database backends, notably SQLite, even for columns declared
# DateTime(timezone=True) - see verification_tokens.ensure_aware_utc's
# docstring) against an aware `now` raised
# "TypeError: can't compare offset-naive and offset-aware datetimes".
# Fixed via ensure_aware_utc() in models.is_valid() and
# auth_routes.resend_verification()'s cooldown check. These tests
# reproduce the naive-datetime shape directly against the real route code
# (not just the isolated helper - see test_verification_tokens.py for
# that) to confirm the fix holds end-to-end.

def test_valid_timezone_aware_token_verifies_successfully(
    registration_client, mock_email_service, valid_registration_payload
):
    """A token whose expires_at is exactly as generate_verification_token()
    produced it (aware, real entropy, real SHA-256 hash) must verify
    without any TypeError - the normal, non-degraded path."""
    _register(registration_client, mock_email_service, valid_registration_payload)
    raw_token = _extract_token_from_email(mock_email_service)

    response = registration_client.get(f"/api/auth/verify-email?token={raw_token}")
    assert response.status_code == 200
    assert response.get_json()["user"]["email_verified"] is True


def test_expired_token_with_naive_datetime_written_directly_is_rejected_not_crashed(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    """Directly reproduces the bug's exact shape: write a
    TIMEZONE-NAIVE expires_at (simulating what a real SQLite round-trip
    produces even from an aware value) and confirm the route returns a
    clean 400 instead of raising TypeError."""
    _register(registration_client, mock_email_service, valid_registration_payload)
    raw_token = _extract_token_from_email(mock_email_service)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.EmailVerificationToken.query.filter_by(user_id=user.id).first()
        naive_past = (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None)
        token_row.expires_at = naive_past
        from database import db
        db.session.add(token_row)
        db.session.commit()

    # Must not raise - this is the actual regression check.
    response = registration_client.get(f"/api/auth/verify-email?token={raw_token}")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid or expired verification link"}


def test_used_token_with_naive_used_at_is_rejected_not_crashed(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    """used_at is only ever None-checked, not compared, but confirm the
    combination of a naive expires_at (still valid) plus a set used_at
    still resolves to "invalid", not a crash."""
    _register(registration_client, mock_email_service, valid_registration_payload)
    raw_token = _extract_token_from_email(mock_email_service)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.EmailVerificationToken.query.filter_by(user_id=user.id).first()
        token_row.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1)
        token_row.used_at = datetime.now(timezone.utc).replace(tzinfo=None)
        from database import db
        db.session.add(token_row)
        db.session.commit()

    response = registration_client.get(f"/api/auth/verify-email?token={raw_token}")
    assert response.status_code == 400
    assert response.get_json() == {"error": "Invalid or expired verification link"}


def test_resend_cooldown_comparison_does_not_crash_with_naive_created_at(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    """Reproduces the same bug shape in resend_verification()'s cooldown
    check: a naive created_at compared against aware `now` must not
    raise, and must correctly block the resend (cooldown still active)."""
    _register(registration_client, mock_email_service, valid_registration_payload)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.EmailVerificationToken.query.filter_by(user_id=user.id).first()
        # Naive, and recent - cooldown should still be in effect.
        token_row.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
        from database import db
        db.session.add(token_row)
        db.session.commit()

    response = registration_client.post(
        "/api/auth/resend-verification", json={"email": "ada@example.com"}
    )
    assert response.status_code == 200  # must not crash
    # Cooldown active -> no second email sent (still just the original).
    assert len(mock_email_service) == 1


def test_resend_cooldown_allows_resend_once_elapsed_with_naive_created_at(
    registration_client, mock_email_service, valid_registration_payload, db_test_app
):
    """Same naive-datetime shape, but old enough that the cooldown has
    elapsed - confirms the comparison is not just "doesn't crash" but
    actually correct in both directions."""
    _register(registration_client, mock_email_service, valid_registration_payload)

    with db_test_app.app_context():
        user = models.User.query.filter_by(email="ada@example.com").first()
        token_row = models.EmailVerificationToken.query.filter_by(user_id=user.id).first()
        naive_old = (datetime.now(timezone.utc) - timedelta(minutes=5)).replace(tzinfo=None)
        token_row.created_at = naive_old
        from database import db
        db.session.add(token_row)
        db.session.commit()

    response = registration_client.post(
        "/api/auth/resend-verification", json={"email": "ada@example.com"}
    )
    assert response.status_code == 200
    assert len(mock_email_service) == 2  # original + resend went through
