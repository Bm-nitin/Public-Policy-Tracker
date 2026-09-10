"""
Phase 4: tests for backend/email_service.py.

build_verification_email() / build_verification_url() are pure functions
and tested directly with real code. send_email() itself (the part that
would open a real SMTP connection) is exercised only through the
mock_email_service fixture (see conftest.py) in test_email_verification.py
- this file never opens a socket.
"""

import pytest

from email_service import (
    EmailSendError,
    build_verification_email,
    build_verification_url,
    send_email,
)


def test_send_email_without_mail_host_configured_raises_cleanly(monkeypatch):
    import config
    monkeypatch.setattr(config.Config, "MAIL_HOST", None)
    with pytest.raises(EmailSendError):
        send_email("someone@example.com", "subject", "body")


def test_build_verification_url_uses_frontend_url_from_config(monkeypatch):
    import config
    monkeypatch.setattr(config.Config, "FRONTEND_URL", "https://app.example.com")
    url = build_verification_url("some-raw-token-value")
    assert url == "https://app.example.com/verify-email?token=some-raw-token-value"


def test_build_verification_url_strips_trailing_slash(monkeypatch):
    import config
    monkeypatch.setattr(config.Config, "FRONTEND_URL", "https://app.example.com/")
    url = build_verification_url("tok")
    assert url == "https://app.example.com/verify-email?token=tok"


def test_build_verification_url_without_frontend_url_raises_cleanly(monkeypatch):
    import config
    monkeypatch.setattr(config.Config, "FRONTEND_URL", None)
    with pytest.raises(EmailSendError):
        build_verification_url("some-token")


def test_build_verification_email_contains_the_link():
    subject, body = build_verification_email("Ada", "https://app.example.com/verify-email?token=abc123")
    assert "https://app.example.com/verify-email?token=abc123" in body


def test_build_verification_email_contains_expiration_info():
    subject, body = build_verification_email("Ada", "https://example.com/verify?token=x")
    assert "24 hours" in body


def test_build_verification_email_contains_app_name_and_greeting():
    subject, body = build_verification_email("Ada", "https://example.com/verify?token=x")
    assert "Ada" in body
    assert "Public Policy Tracker" in subject


def test_build_verification_email_never_contains_password_related_words():
    """Sanity check: nothing about a password should ever end up in a
    verification email - this function has no way to receive one, but
    this test guards against that ever accidentally changing."""
    subject, body = build_verification_email("Ada", "https://example.com/verify?token=x")
    combined = (subject + body).lower()
    assert "password" not in combined
