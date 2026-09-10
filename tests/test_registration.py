"""
Phase 3: POST /api/auth/register end-to-end tests.

Uses registration_client (see conftest.py), a Flask test client wired to
an isolated in-memory database, fresh for every test - no test depends on
another test's data or on ordering.

SANDBOX NOTE: see conftest.py's flask_sqlalchemy and argon2 shim
docstrings for exactly what is and is not validated when this file runs
in this offline sandbox vs. a real environment with flask-sqlalchemy,
argon2-cffi, and PostgreSQL installed. The ORM operations here (INSERT,
UNIQUE constraint enforcement, SELECT) execute as real SQL against a real
(if in-memory, non-Postgres) database - they are not simulated in Python.
"""

import models


# --- 1-2: successful registration, user stored in database -----------------

def test_successful_registration_returns_201(registration_client, valid_registration_payload):
    response = registration_client.post("/api/auth/register", json=valid_registration_payload)
    assert response.status_code == 201


def test_successful_registration_stores_user_in_database(
    registration_client, valid_registration_payload, db_test_app
):
    registration_client.post("/api/auth/register", json=valid_registration_payload)

    with db_test_app.app_context():
        stored = models.User.query.filter_by(email="ada@example.com").first()

    assert stored is not None
    assert stored.name == "Ada Lovelace"
    assert stored.email == "ada@example.com"


# --- 3-4: password is hashed, plaintext not stored --------------------------

def test_stored_password_is_hashed_not_plaintext(
    registration_client, valid_registration_payload, db_test_app
):
    registration_client.post("/api/auth/register", json=valid_registration_payload)

    with db_test_app.app_context():
        stored = models.User.query.filter_by(email="ada@example.com").first()

    assert stored.password_hash != valid_registration_payload["password"]
    assert valid_registration_payload["password"] not in stored.password_hash


# --- 5-6: hash verification (correct / incorrect) ---------------------------

def test_stored_hash_verifies_against_the_original_password(
    registration_client, valid_registration_payload, db_test_app
):
    from security import verify_password

    registration_client.post("/api/auth/register", json=valid_registration_payload)

    with db_test_app.app_context():
        stored = models.User.query.filter_by(email="ada@example.com").first()

    assert verify_password(valid_registration_payload["password"], stored.password_hash) is True


def test_stored_hash_rejects_an_incorrect_password(
    registration_client, valid_registration_payload, db_test_app
):
    from security import verify_password

    registration_client.post("/api/auth/register", json=valid_registration_payload)

    with db_test_app.app_context():
        stored = models.User.query.filter_by(email="ada@example.com").first()

    assert verify_password("definitely-the-wrong-password", stored.password_hash) is False


# --- 7-8, 19: email uniqueness, duplicate rejection, IntegrityError --------

def test_duplicate_email_is_rejected_with_409(registration_client, valid_registration_payload):
    first = registration_client.post("/api/auth/register", json=valid_registration_payload)
    assert first.status_code == 201

    second = registration_client.post("/api/auth/register", json=valid_registration_payload)
    assert second.status_code == 409
    assert "error" in second.get_json()


def test_duplicate_email_does_not_create_a_second_row(
    registration_client, valid_registration_payload, db_test_app
):
    registration_client.post("/api/auth/register", json=valid_registration_payload)
    registration_client.post("/api/auth/register", json=valid_registration_payload)

    with db_test_app.app_context():
        all_matches = models.User.query.filter_by(email="ada@example.com").all()

    assert len(all_matches) == 1


def test_duplicate_email_case_insensitive_is_also_rejected(
    registration_client, valid_registration_payload
):
    """Email normalization (lowercasing) means Ada@Example.com and
    ada@example.com must collide, not create two accounts."""
    registration_client.post("/api/auth/register", json=valid_registration_payload)

    shouting_email_payload = dict(valid_registration_payload)
    shouting_email_payload["email"] = "ADA@EXAMPLE.COM"

    response = registration_client.post("/api/auth/register", json=shouting_email_payload)
    assert response.status_code == 409


def test_duplicate_email_does_not_return_a_500_or_crash(
    registration_client, valid_registration_payload
):
    """This is the IntegrityError-handling check (scenario 19): the
    UNIQUE constraint violation on the second insert must be caught and
    turned into a clean 409, never an unhandled exception / 500."""
    registration_client.post("/api/auth/register", json=valid_registration_payload)
    response = registration_client.post("/api/auth/register", json=valid_registration_payload)
    assert response.status_code != 500
    assert response.status_code == 409


# --- 9-15: validation is enforced at the HTTP layer too ---------------------

def test_missing_name_returns_400(registration_client, valid_registration_payload):
    payload = dict(valid_registration_payload)
    del payload["name"]
    response = registration_client.post("/api/auth/register", json=payload)
    assert response.status_code == 400


def test_missing_email_returns_400(registration_client, valid_registration_payload):
    payload = dict(valid_registration_payload)
    del payload["email"]
    response = registration_client.post("/api/auth/register", json=payload)
    assert response.status_code == 400


def test_missing_password_returns_400(registration_client, valid_registration_payload):
    payload = dict(valid_registration_payload)
    del payload["password"]
    response = registration_client.post("/api/auth/register", json=payload)
    assert response.status_code == 400


def test_invalid_email_format_returns_400(registration_client, valid_registration_payload):
    payload = dict(valid_registration_payload)
    payload["email"] = "not-an-email"
    response = registration_client.post("/api/auth/register", json=payload)
    assert response.status_code == 400


def test_empty_values_return_400(registration_client):
    response = registration_client.post(
        "/api/auth/register",
        json={"name": "", "email": "", "password": ""},
    )
    assert response.status_code == 400


def test_short_password_returns_400(registration_client, valid_registration_payload):
    payload = dict(valid_registration_payload)
    payload["password"] = "short"
    response = registration_client.post("/api/auth/register", json=payload)
    assert response.status_code == 400


def test_excessive_input_length_returns_400(registration_client, valid_registration_payload):
    payload = dict(valid_registration_payload)
    payload["name"] = "a" * 5000
    response = registration_client.post("/api/auth/register", json=payload)
    assert response.status_code == 400


def test_no_json_body_returns_400_not_500(registration_client):
    """Deliberately different from the pre-existing /chat bug documented
    in test_api.py - this new endpoint uses get_json(silent=True), so a
    missing/malformed body is a clean validation error, not a 500."""
    response = registration_client.post("/api/auth/register")
    assert response.status_code == 400


# --- 16: email_verified starts false -----------------------------------------

def test_new_user_has_email_verified_false(
    registration_client, valid_registration_payload, db_test_app
):
    registration_client.post("/api/auth/register", json=valid_registration_payload)

    with db_test_app.app_context():
        stored = models.User.query.filter_by(email="ada@example.com").first()

    assert stored.email_verified is False


def test_registration_response_reports_email_verified_false(
    registration_client, valid_registration_payload
):
    response = registration_client.post("/api/auth/register", json=valid_registration_payload)
    body = response.get_json()
    assert body["user"]["email_verified"] is False


# --- 17-18: response never exposes password / password_hash ----------------

def test_response_does_not_include_password(registration_client, valid_registration_payload):
    response = registration_client.post("/api/auth/register", json=valid_registration_payload)
    body = response.get_json()
    assert "password" not in body
    assert "password" not in body["user"]


def test_response_does_not_include_password_hash(registration_client, valid_registration_payload):
    response = registration_client.post("/api/auth/register", json=valid_registration_payload)
    body = response.get_json()
    assert "password_hash" not in body
    assert "password_hash" not in body["user"]


def test_response_user_object_only_has_safe_fields(registration_client, valid_registration_payload):
    response = registration_client.post("/api/auth/register", json=valid_registration_payload)
    body = response.get_json()
    assert set(body["user"].keys()) == {"id", "name", "email", "email_verified"}


# --- registration does not auto-authenticate --------------------------------

def test_registration_response_contains_no_session_or_token_fields(
    registration_client, valid_registration_payload
):
    """Phase 3 explicitly does not implement login/sessions/JWT/cookies -
    confirms the response carries no such fields and the client receives
    no Set-Cookie header."""
    response = registration_client.post("/api/auth/register", json=valid_registration_payload)
    body = response.get_json()
    for forbidden_key in ("token", "access_token", "session", "jwt"):
        assert forbidden_key not in body
        assert forbidden_key not in body["user"]
    assert "Set-Cookie" not in response.headers


# --- no database configured -------------------------------------------------

def test_registration_without_database_configured_returns_503(app_client):
    """app_client (see conftest.py) uses the real app.py with this repo's
    actual .env, which does not set DATABASE_URL - so this exercises the
    real "no database configured" guard in auth_routes.py, not a mock."""
    response = app_client.post(
        "/api/auth/register",
        json={"name": "A", "email": "a@example.com", "password": "password123"},
    )
    assert response.status_code == 503
