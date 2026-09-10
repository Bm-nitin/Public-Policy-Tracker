"""
Phase 3: registration input validation.

Pure functions only - no database access, no password hashing, no Flask
request object. This keeps validation rules independently testable and
keeps auth_routes.py thin.
"""

import re

MAX_NAME_LENGTH = 120
MAX_EMAIL_LENGTH = 255
MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

# Deliberately simple (not RFC 5322-complete) - "reasonably valid" per the
# Phase 3 brief, not a full email-address parser. Rejects the obvious
# malformed cases (missing @, missing domain, whitespace) without pulling
# in a new dependency for this phase.
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def normalize_email(email):
    """Trim + lowercase, for uniqueness checks and storage. This is a
    normalization step, not a content change - it does not alter the
    name field, and does not alter the local part's characters."""
    return email.strip().lower()


def validate_registration_payload(data):
    """Validates a registration request body.

    Returns (errors, cleaned):
      - errors: list[str], empty if valid
      - cleaned: dict with "name", "email", "password" ready for storage
        (name trimmed, email normalized, password left exactly as
        submitted since it's about to be hashed, not stored) - or None if
        errors is non-empty.
    """
    errors = []

    if not isinstance(data, dict):
        return ["Request body must be a JSON object with name, email, and password"], None

    name = data.get("name")
    email = data.get("email")
    password = data.get("password")

    if "name" not in data:
        errors.append("name is required")
    elif not isinstance(name, str):
        errors.append("name must be a string")
    elif not name.strip():
        errors.append("name must not be empty")
    elif len(name) > MAX_NAME_LENGTH:
        errors.append(f"name must be {MAX_NAME_LENGTH} characters or fewer")

    if "email" not in data:
        errors.append("email is required")
    elif not isinstance(email, str):
        errors.append("email must be a string")
    elif not email.strip():
        errors.append("email must not be empty")
    else:
        normalized_email = normalize_email(email)
        if len(normalized_email) > MAX_EMAIL_LENGTH:
            errors.append(f"email must be {MAX_EMAIL_LENGTH} characters or fewer")
        elif not _EMAIL_RE.match(normalized_email):
            errors.append("email format is invalid")

    if "password" not in data:
        errors.append("password is required")
    elif not isinstance(password, str):
        errors.append("password must be a string")
    elif not password:
        errors.append("password must not be empty")
    else:
        if len(password) < MIN_PASSWORD_LENGTH:
            errors.append(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
        if len(password) > MAX_PASSWORD_LENGTH:
            errors.append(f"password must be {MAX_PASSWORD_LENGTH} characters or fewer")

    if errors:
        return errors, None

    return [], {
        "name": name.strip(),
        "email": normalize_email(email),
        "password": password,
    }
