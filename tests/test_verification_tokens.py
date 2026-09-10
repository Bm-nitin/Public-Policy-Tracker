"""
Phase 4: tests for backend/verification_tokens.py - pure functions, no
DB, no email. Real code, real hashlib/secrets (stdlib, always available
regardless of sandbox constraints elsewhere in this suite).
"""

from datetime import datetime, timedelta, timezone

from verification_tokens import (
    TOKEN_EXPIRY_HOURS,
    ensure_aware_utc,
    generate_verification_token,
    hash_token,
)


def test_generate_verification_token_returns_three_values():
    raw_token, token_hash, expires_at = generate_verification_token()
    assert isinstance(raw_token, str)
    assert isinstance(token_hash, str)
    assert isinstance(expires_at, datetime)


def test_raw_token_has_real_entropy_and_is_not_predictable():
    raw_token, _, _ = generate_verification_token()
    # secrets.token_urlsafe(32) -> ~43 base64url characters for 256 bits.
    assert len(raw_token) >= 40


def test_two_generated_tokens_are_never_equal():
    raw_one, _, _ = generate_verification_token()
    raw_two, _, _ = generate_verification_token()
    assert raw_one != raw_two


def test_token_hash_does_not_equal_the_raw_token():
    raw_token, token_hash, _ = generate_verification_token()
    assert token_hash != raw_token
    assert raw_token not in token_hash


def test_hash_token_is_deterministic_for_the_same_input():
    raw_token, token_hash, _ = generate_verification_token()
    assert hash_token(raw_token) == token_hash


def test_hash_token_is_a_64_char_hex_sha256_digest():
    raw_token, token_hash, _ = generate_verification_token()
    assert len(token_hash) == 64
    int(token_hash, 16)  # raises ValueError if not valid hex


def test_expires_at_is_in_the_future_by_the_documented_window():
    _, _, expires_at = generate_verification_token()
    now = datetime.now(timezone.utc)
    expected = now + timedelta(hours=TOKEN_EXPIRY_HOURS)
    # Allow a small tolerance for test execution time.
    assert abs((expires_at - expected).total_seconds()) < 5


# --- timezone-consistency regression tests (bug: TypeError comparing ------
# --- offset-naive and offset-aware datetimes, fixed via ensure_aware_utc) -

def test_generated_expires_at_is_timezone_aware():
    """The root-cause bug involved a naive datetime reaching a comparison
    against an aware one. generate_verification_token()'s output must
    always be aware to begin with."""
    _, _, expires_at = generate_verification_token()
    assert expires_at.tzinfo is not None


def test_ensure_aware_utc_attaches_utc_to_a_naive_datetime():
    naive = datetime(2026, 1, 1, 12, 0, 0)
    assert naive.tzinfo is None  # sanity check on the test input itself

    result = ensure_aware_utc(naive)
    assert result.tzinfo is not None
    assert result.utcoffset() == timedelta(0)
    # The wall-clock value itself must not change - only tzinfo is added,
    # never a value shift (this is the "don't strip tzinfo from `now`"
    # requirement's mirror image: we don't silently reinterpret the
    # naive value as a different moment in time either).
    assert result.replace(tzinfo=None) == naive


def test_ensure_aware_utc_converts_a_non_utc_aware_datetime_to_utc():
    eastern = timezone(timedelta(hours=-5))
    aware_non_utc = datetime(2026, 1, 1, 12, 0, 0, tzinfo=eastern)

    result = ensure_aware_utc(aware_non_utc)
    assert result.tzinfo is not None
    assert result == aware_non_utc  # same instant
    assert result.utcoffset() == timedelta(0)  # now expressed in UTC


def test_ensure_aware_utc_is_a_noop_for_already_utc_aware_datetime():
    already_utc = datetime.now(timezone.utc)
    assert ensure_aware_utc(already_utc) == already_utc


def test_ensure_aware_utc_passes_none_through_unchanged():
    assert ensure_aware_utc(None) is None


def test_naive_and_aware_expires_at_compare_equal_after_normalization():
    """Directly reproduces the original bug's shape (a naive datetime
    read back from a database that doesn't preserve tzinfo, like SQLite -
    see verification_tokens.ensure_aware_utc's docstring) and confirms
    the fix: comparing against an aware `now` no longer raises
    TypeError, and produces the correct result rather than a weakened
    one."""
    aware_moment = datetime.now(timezone.utc)
    naive_equivalent = aware_moment.replace(tzinfo=None)

    normalized = ensure_aware_utc(naive_equivalent)
    # No TypeError raised by this comparison - that's the actual fix.
    assert normalized > (aware_moment - timedelta(seconds=1))
    assert normalized < (aware_moment + timedelta(seconds=1))
