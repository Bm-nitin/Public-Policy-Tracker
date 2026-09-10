"""
Phase 3: tests for backend/validators.py - pure functions, no DB, no
password hashing. Covers required scenarios 9-15 from the Phase 3 brief
(missing/invalid/empty/short/oversized fields) at the unit level, plus a
sanity check that a fully valid payload passes.
"""

import pytest

from validators import (
    MAX_EMAIL_LENGTH,
    MAX_NAME_LENGTH,
    MAX_PASSWORD_LENGTH,
    MIN_PASSWORD_LENGTH,
    normalize_email,
    validate_registration_payload,
)


def _payload(**overrides):
    base = {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "password": "correct-horse-battery-staple",
    }
    base.update(overrides)
    return base


def test_valid_payload_has_no_errors():
    errors, cleaned = validate_registration_payload(_payload())
    assert errors == []
    assert cleaned == {
        "name": "Ada Lovelace",
        "email": "ada@example.com",
        "password": "correct-horse-battery-staple",
    }


def test_non_dict_body_is_rejected():
    errors, cleaned = validate_registration_payload(None)
    assert errors
    assert cleaned is None

    errors, cleaned = validate_registration_payload("not a dict")
    assert errors
    assert cleaned is None


# --- missing fields (scenarios 9, 10, 11) -----------------------------------

def test_missing_name_is_rejected():
    payload = _payload()
    del payload["name"]
    errors, cleaned = validate_registration_payload(payload)
    assert any("name" in e for e in errors)
    assert cleaned is None


def test_missing_email_is_rejected():
    payload = _payload()
    del payload["email"]
    errors, cleaned = validate_registration_payload(payload)
    assert any("email" in e for e in errors)
    assert cleaned is None


def test_missing_password_is_rejected():
    payload = _payload()
    del payload["password"]
    errors, cleaned = validate_registration_payload(payload)
    assert any("password" in e for e in errors)
    assert cleaned is None


# --- invalid / empty values (scenarios 12, 13) ------------------------------

@pytest.mark.parametrize("bad_email", [
    "not-an-email",
    "missing-domain@",
    "@missing-local.com",
    "no-at-sign.com",
    "spaces in@email.com",
])
def test_invalid_email_format_is_rejected(bad_email):
    errors, cleaned = validate_registration_payload(_payload(email=bad_email))
    assert any("email" in e for e in errors)
    assert cleaned is None


def test_empty_name_is_rejected():
    errors, cleaned = validate_registration_payload(_payload(name="   "))
    assert any("name" in e for e in errors)
    assert cleaned is None


def test_empty_email_is_rejected():
    errors, cleaned = validate_registration_payload(_payload(email=""))
    assert any("email" in e for e in errors)
    assert cleaned is None


def test_empty_password_is_rejected():
    errors, cleaned = validate_registration_payload(_payload(password=""))
    assert any("password" in e for e in errors)
    assert cleaned is None


def test_non_string_fields_are_rejected():
    errors, cleaned = validate_registration_payload(_payload(name=12345))
    assert any("name" in e for e in errors)
    assert cleaned is None

    errors, cleaned = validate_registration_payload(_payload(email=["a@b.com"]))
    assert any("email" in e for e in errors)
    assert cleaned is None

    errors, cleaned = validate_registration_payload(_payload(password=123456789))
    assert any("password" in e for e in errors)
    assert cleaned is None


# --- password strength (scenario 14) ----------------------------------------

def test_short_password_is_rejected():
    short_password = "a" * (MIN_PASSWORD_LENGTH - 1)
    errors, cleaned = validate_registration_payload(_payload(password=short_password))
    assert any("at least" in e for e in errors)
    assert cleaned is None


def test_password_at_minimum_length_is_accepted():
    exact_password = "a" * MIN_PASSWORD_LENGTH
    errors, cleaned = validate_registration_payload(_payload(password=exact_password))
    assert errors == []


# --- excessive length (scenario 15) -----------------------------------------

def test_excessively_long_name_is_rejected():
    long_name = "a" * (MAX_NAME_LENGTH + 1)
    errors, cleaned = validate_registration_payload(_payload(name=long_name))
    assert any("name" in e for e in errors)
    assert cleaned is None


def test_excessively_long_email_is_rejected():
    long_local_part = "a" * (MAX_EMAIL_LENGTH)
    long_email = f"{long_local_part}@example.com"
    errors, cleaned = validate_registration_payload(_payload(email=long_email))
    assert any("email" in e for e in errors)
    assert cleaned is None


def test_excessively_long_password_is_rejected():
    long_password = "a" * (MAX_PASSWORD_LENGTH + 1)
    errors, cleaned = validate_registration_payload(_payload(password=long_password))
    assert any("password" in e for e in errors)
    assert cleaned is None


# --- normalization -----------------------------------------------------------

def test_normalize_email_trims_and_lowercases():
    assert normalize_email("  Ada@Example.COM  ") == "ada@example.com"


def test_name_is_only_trimmed_not_otherwise_changed():
    errors, cleaned = validate_registration_payload(_payload(name="  Ada Lovelace  "))
    assert errors == []
    assert cleaned["name"] == "Ada Lovelace"
