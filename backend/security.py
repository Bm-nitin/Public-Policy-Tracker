"""
Phase 3: password hashing.

Argon2id via argon2-cffi (a maintained, widely used implementation - the
`argon2.PasswordHasher` default profile uses the Argon2id variant with a
random per-hash salt generated automatically).

Deliberately isolated from auth_routes.py / models.py so:
  - password hashing logic has its own focused tests, independent of the
    HTTP layer or the database;
  - nothing in this file ever logs a password or a hash - callers must not
    add logging that includes either argument to these functions.
"""

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError, InvalidHash

_hasher = PasswordHasher()


def hash_password(plain_password):
    """Returns an Argon2id hash string (includes algorithm params + a
    fresh random salt - never call this twice expecting the same output
    for the same password, that's by design)."""
    return _hasher.hash(plain_password)


def verify_password(plain_password, password_hash):
    """Returns True/False. Never raises - any verification failure
    (wrong password, corrupted/foreign hash format) is treated as "does
    not match" rather than propagating an exception to callers."""
    try:
        return _hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False
