"""
Phase 4: email service.

Deliberately minimal - a single send_email() function using Python's
stdlib smtplib, no third-party email SDK/framework (per the Phase 4 brief:
"do not add an unnecessarily complicated email framework"). SMTP works
with essentially any transactional email provider (SES, SendGrid, Mailgun,
Postmark, a real mailbox, etc. all speak SMTP), so this stays provider-
agnostic without depending on a specific commercial API.

auth_routes.py never touches smtplib directly - it only calls
send_verification_email() / send_password_reset_email() (the latter added
in Phase 6, reusing send_email()/EmailSendError rather than a second
email implementation), keeping SMTP details out of the route layer.
"""

import smtplib
from email.message import EmailMessage

from config import Config
from verification_tokens import TOKEN_EXPIRY_HOURS

APP_NAME = "Public Policy Tracker"


class EmailSendError(Exception):
    """Raised when a verification email could not be sent. Never carries
    SMTP credentials or a raw traceback in its message - callers must
    treat this as an opaque "sending failed" signal."""
    pass


def send_email(to_email, subject, body_text):
    """Sends a plain-text email via SMTP. Raises EmailSendError (never a
    raw smtplib/socket exception) on any failure, so callers can't
    accidentally leak connection details in a response."""
    if not Config.MAIL_HOST:
        raise EmailSendError("Mail is not configured (MAIL_HOST is unset).")

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = Config.MAIL_FROM or Config.MAIL_USERNAME or "no-reply@example.com"
    message["To"] = to_email
    message.set_content(body_text)

    try:
        with smtplib.SMTP(Config.MAIL_HOST, Config.MAIL_PORT, timeout=10) as server:
            if Config.MAIL_USE_TLS:
                server.starttls()
            if Config.MAIL_USERNAME and Config.MAIL_PASSWORD:
                server.login(Config.MAIL_USERNAME, Config.MAIL_PASSWORD)
            server.send_message(message)
    except Exception as exc:
        # Deliberately do not include str(exc) verbatim if it might echo
        # back credentials (some SMTP auth failures include the attempted
        # username in their error text) - keep it generic.
        raise EmailSendError("Failed to send email via the configured mail server.") from exc


def build_verification_email(user_name, verification_url):
    subject = f"Verify your {APP_NAME} account"
    body = (
        f"Hi {user_name},\n\n"
        f"Thanks for registering with {APP_NAME}. Please verify your "
        f"email address by visiting the link below:\n\n"
        f"{verification_url}\n\n"
        f"This link expires in {TOKEN_EXPIRY_HOURS} hours and can only be "
        f"used once.\n\n"
        f"If you didn't create this account, you can safely ignore this "
        f"email - no further action is needed.\n\n"
        f"- {APP_NAME}"
    )
    return subject, body


def build_verification_url(raw_token):
    if not Config.FRONTEND_URL:
        raise EmailSendError("Cannot build a verification link: FRONTEND_URL is not configured.")
    base = Config.FRONTEND_URL.rstrip("/")
    return f"{base}/verify-email?token={raw_token}"


def send_verification_email(user, raw_token):
    """Builds and sends the verification email for a freshly generated
    token. Returns the verification URL (useful for logging/tests -
    never the raw token by itself, and this URL is not written to any
    log by this function)."""
    verification_url = build_verification_url(raw_token)
    subject, body = build_verification_email(user.name, verification_url)
    send_email(user.email, subject, body)
    return verification_url


def build_password_reset_email(user_name, reset_url):
    subject = f"Reset your {APP_NAME} password"
    body = (
        f"Hi {user_name},\n\n"
        f"We received a request to reset your {APP_NAME} password. "
        f"Click the link below to choose a new one:\n\n"
        f"{reset_url}\n\n"
        f"This link expires in {Config.PASSWORD_RESET_TOKEN_EXPIRY_MINUTES} "
        f"minutes and can only be used once.\n\n"
        f"If you didn't request this, you can safely ignore this email - "
        f"your password will not be changed.\n\n"
        f"- {APP_NAME}"
    )
    return subject, body


def build_password_reset_url(raw_token):
    if not Config.FRONTEND_URL:
        raise EmailSendError("Cannot build a password reset link: FRONTEND_URL is not configured.")
    base = Config.FRONTEND_URL.rstrip("/")
    return f"{base}/reset-password?token={raw_token}"


def send_password_reset_email(user, raw_token):
    """Same shape as send_verification_email() - see that function. The
    reset URL is returned for tests/callers, never logged by this
    function itself (see the Phase 6 brief's "do not log reset URLs")."""
    reset_url = build_password_reset_url(raw_token)
    subject, body = build_password_reset_email(user.name, reset_url)
    send_email(user.email, subject, body)
    return reset_url
