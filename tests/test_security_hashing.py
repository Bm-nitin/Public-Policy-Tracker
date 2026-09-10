"""
Phase 3: tests for backend/security.py password hashing.

Covers required scenarios 3-6 from the Phase 3 brief: password is hashed,
plaintext is never stored, correct password verifies, incorrect password
fails verification.

SANDBOX NOTE: in this offline sandbox (no network access to install
argon2-cffi), these tests run against the pbkdf2-based substitute defined
in conftest.py's argon2 shim, NOT real Argon2id - see that shim's
docstring for why, and for confirmation that it's a real (if different)
cryptographic hash, not a fake. The hashing *contract* asserted below
(unique salt, non-reversible, correct/incorrect verification) is
algorithm-agnostic and holds for both the shim and the real argon2-cffi
package - running this file in an environment with the real dependency
installed exercises the same assertions against real Argon2id.
"""

from security import hash_password, verify_password


def test_hash_password_does_not_return_the_plaintext():
    password = "correct-horse-battery-staple"
    hashed = hash_password(password)
    assert hashed != password
    assert password not in hashed


def test_hash_password_produces_a_unique_salt_each_call():
    password = "correct-horse-battery-staple"
    hash_one = hash_password(password)
    hash_two = hash_password(password)
    # Same input password, but each hash call must use a fresh random
    # salt, so the two output hashes must differ even though they'd both
    # verify the same password.
    assert hash_one != hash_two


def test_verify_password_succeeds_for_the_correct_password():
    password = "correct-horse-battery-staple"
    hashed = hash_password(password)
    assert verify_password(password, hashed) is True


def test_verify_password_fails_for_an_incorrect_password():
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("wrong-password", hashed) is False


def test_verify_password_fails_for_empty_string_against_real_hash():
    hashed = hash_password("correct-horse-battery-staple")
    assert verify_password("", hashed) is False


def test_verify_password_never_raises_on_garbage_hash_input():
    # A corrupted/foreign hash string must be treated as "does not
    # match", not propagate an exception up to the caller.
    assert verify_password("anything", "not-a-real-hash-at-all") is False
