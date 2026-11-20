"""
Phase 4: tests for backend/email_service.py.

The email builders are tested directly. SMTP tests use a mocked server.
"""

import ssl
from unittest.mock import MagicMock

import pytest

import email_service
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


@pytest.fixture
def smtp_server(monkeypatch):
    from config import Config

    monkeypatch.setattr(Config, "MAIL_HOST", "smtp.example.com")
    monkeypatch.setattr(Config, "MAIL_USE_TLS", True)
    monkeypatch.setattr(Config, "MAIL_USERNAME", "test-sender")
    monkeypatch.setattr(Config, "MAIL_PASSWORD", "test-password")
    smtp = MagicMock()
    monkeypatch.setattr(email_service.smtplib, "SMTP", smtp)
    return smtp.return_value.__enter__.return_value


def test_send_email_verifies_tls_before_smtp_login(smtp_server):
    send_email("someone@example.com", "subject", "body")

    context = smtp_server.starttls.call_args.kwargs["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert [call[0] for call in smtp_server.method_calls] == ["starttls", "login", "send_message"]


def test_certificate_error_aborts_before_smtp_login(smtp_server):
    smtp_server.starttls.side_effect = ssl.SSLCertVerificationError("certificate rejected")

    with pytest.raises(EmailSendError, match="Failed to send email"):
        send_email("someone@example.com", "subject", "body")

    smtp_server.login.assert_not_called()
    smtp_server.send_message.assert_not_called()


def test_build_verification_url_uses_frontend_url_from_config(monkeypatch):
    import config
    monkeypatch.setattr(config.Config, "FRONTEND_URL", "https://app.example.com")
    url = build_verification_url("some-raw-token-value")
    assert url == "https://app.example.com/verify-email.html#token=some-raw-token-value"


def test_build_verification_url_strips_trailing_slash(monkeypatch):
    import config
    monkeypatch.setattr(config.Config, "FRONTEND_URL", "https://app.example.com/")
    url = build_verification_url("tok")
    assert url == "https://app.example.com/verify-email.html#token=tok"


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
