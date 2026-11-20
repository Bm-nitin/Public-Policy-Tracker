"""Registration, email delivery, recovery, login, and cookie behavior."""

from dataclasses import replace
import re
from urllib.parse import urlencode, urlsplit

from playwright.sync_api import expect
import pytest

from e2e.workflows import (
    COOKIE_NAME, account, api_request, dialog_message, open_page, register, reset_request, sign_in,
)


def test_register_verify_and_login(page, stack):
    user = account(stack)
    assert "Registration successful" in register(page, stack, user)
    assert stack.sql("SELECT email_verified FROM users WHERE email=%s", (user.email,)) == [(False,)]
    url = stack.mail_link(user.email, "verify")
    parts = urlsplit(url)
    assert parts.path == "/verify-email.html" and not parts.query and bool(parts.fragment)
    open_page(page, stack, url.removeprefix(stack.frontend + "/"))
    expect(page).to_have_url(f"{stack.frontend}/verify-email.html")
    assert stack.sql("SELECT email_verified FROM users WHERE email=%s", (user.email,)) == [(False,)]

    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/api/auth/verify-email")) as submitted:
        page.get_by_role("button", name="Verify email").click()
    expect(page.get_by_role("status")).to_contain_text("Your email is verified")
    assert not urlsplit(submitted.value.url).query
    assert not submitted.value.header_value("referer")
    assert stack.sql("SELECT email_verified FROM users WHERE email=%s", (user.email,)) == [(True,)]
    assert stack.sql("SELECT used_at IS NOT NULL FROM email_verification_tokens") == [(True,)]
    page.get_by_role("link", name="Continue to sign in").click()
    expect(page.get_by_role("heading", name="Welcome back")).to_be_visible()
    sign_in(page, stack, user)


@pytest.mark.parametrize("field, feedback", [
    ("required", "All fields are required"),
    ("email", "Email format is invalid"),
    ("password", "at least 8 characters"),
])
def test_registration_validation_does_not_create_user(page, stack, field, feedback):
    user = account(stack)
    if field == "required":
        user = replace(user, name="")
    elif field == "email":
        user = replace(user, email="invalid-email")
    else:
        user = replace(user, password="short")
    assert feedback.lower() in register(page, stack, user).lower()
    assert stack.sql("SELECT count(*) FROM users") == [(0,)]
    assert not stack.mail


def test_duplicate_registration_preserves_existing_user(page, stack):
    user = account(stack, seeded=True)
    assert "already exists" in register(page, stack, user)
    assert stack.sql("SELECT count(*) FROM users") == [(1,)]
    assert not stack.mail


def test_expired_verification_resends_and_renews_in_same_tab(page, stack):
    user = account(stack)
    assert "Registration successful" in register(page, stack, user)
    expired = stack.mail_link(user.email, "verify")
    stack.sql("UPDATE email_verification_tokens SET created_at=now()-interval '2 days', expires_at=now()-interval '1 day'")
    open_page(page, stack, expired.removeprefix(stack.frontend + "/"))
    page.get_by_role("button", name="Verify email").click()
    expect(page.get_by_role("status")).to_contain_text("invalid, expired, or already used")
    email = page.get_by_role("textbox", name="Email")
    email.fill(user.email)
    email.press("Enter")
    expect(page.get_by_role("status")).to_contain_text("a verification link has been sent")
    renewed = stack.mail_link(user.email, "verify")
    assert renewed != expired
    page.goto(renewed)
    expect(page).to_have_url(f"{stack.frontend}/verify-email.html")
    page.get_by_role("button", name="Verify email").click()
    expect(page.get_by_role("status")).to_contain_text("Your email is verified")
    assert stack.sql("SELECT email_verified FROM users") == [(True,)]


def test_invalid_verification_link_offers_recovery(page, stack):
    open_page(page, stack, f"verify-email.html#token={stack.password()}")
    page.get_by_role("button", name="Verify email").click()
    expect(page.get_by_role("status")).to_contain_text("invalid, expired, or already used")
    expect(page.get_by_role("button", name="Send verification link")).to_be_visible()
    expect(page).to_have_url(f"{stack.frontend}/verify-email.html")


def test_wrong_password_and_unknown_email_have_same_feedback(page, stack):
    user = account(stack, seeded=True)
    unknown = account(stack)
    open_page(page, stack, "login.html")
    form = page.locator(".sign-in")
    password = stack.password()
    messages = []
    for address in (user.email, unknown.email):
        form.get_by_label("Email", exact=True).fill(address)
        form.get_by_label("Password", exact=True).fill(password)
        messages.append(dialog_message(page, form.get_by_role("button", name="Sign In").click))
    assert messages == ["Invalid email or password"] * 2
    assert stack.sql("SELECT count(*) FROM user_sessions") == [(0,)]


@pytest.mark.parametrize("target", ["dashboard.html", "profile.html"])
def test_protected_page_redirects_to_login(page, stack, target):
    open_page(page, stack, target)
    expect(page).to_have_url(f"{stack.frontend}/login.html?next={target}")
    expect(page.get_by_role("heading", name="Welcome back")).to_be_visible()


def test_logout_revokes_session_and_clears_secure_cookie(page, stack):
    user = account(stack, seeded=True)
    sign_in(page, stack, user, "dashboard.html")
    cookies = [cookie for cookie in page.context.cookies(stack.api) if cookie["name"] == COOKIE_NAME]
    assert len(cookies) == 1
    cookie = cookies[0]
    assert cookie["secure"] and cookie["httpOnly"]
    assert cookie["sameSite"] == "None" and cookie["path"] == "/"
    assert COOKIE_NAME not in page.evaluate("document.cookie")
    page.get_by_role("button", name="Logout").click()
    expect(page).to_have_url(f"{stack.frontend}/login.html")
    assert all(item["name"] != COOKIE_NAME for item in page.context.cookies(stack.api))
    assert stack.sql("SELECT revoked_at IS NOT NULL FROM user_sessions") == [(True,)]
    page.context.add_cookies([cookie])
    assert api_request(page, stack, "/api/auth/me")["status"] == 401


def test_reset_link_changes_password_and_revokes_existing_sessions(page, stack):
    user = account(stack, seeded=True)
    sign_in(page, stack, user)
    link = reset_request(page, stack, user)
    parts = urlsplit(link)
    assert parts.path == "/reset-password" and not parts.query and bool(parts.fragment)
    open_page(page, stack, link.removeprefix(stack.frontend + "/"))
    expect(page).to_have_url(f"{stack.frontend}/reset-password")
    password = stack.password()
    page.get_by_label("New password", exact=True).fill(password)
    page.get_by_label("Confirm new password", exact=True).fill(password)
    with page.expect_request(lambda r: r.method == "POST" and r.url.endswith("/api/auth/reset-password")) as sent:
        page.get_by_role("button", name="Reset Password").click()
    assert not urlsplit(sent.value.url).query
    assert not urlsplit(sent.value.header_value("referer") or "").query
    expect(page.get_by_role("status")).to_contain_text("Your password has been reset")
    expect(page).to_have_url(f"{stack.frontend}/login.html")
    assert stack.sql("SELECT revoked_at IS NOT NULL FROM user_sessions") == [(True,)]
    assert stack.sql("SELECT used_at IS NOT NULL FROM password_reset_tokens") == [(True,)]
    sign_in(page, stack, replace(user, password=password))


@pytest.mark.parametrize("problem", ["invalid", "expired"])
def test_reset_rejects_invalid_or_expired_links(page, stack, problem):
    from security import verify_password

    user = account(stack, seeded=True)
    link = reset_request(page, stack, user)
    if problem == "expired":
        stack.sql("UPDATE password_reset_tokens SET expires_at=now()-interval '1 day'")
    else:
        link = f"{stack.frontend}/reset-password#{urlencode({'token': stack.password()})}"
    open_page(page, stack, link.removeprefix(stack.frontend + "/"))
    password = stack.password()
    page.get_by_label("New password", exact=True).fill(password)
    page.get_by_label("Confirm new password", exact=True).fill(password)
    page.get_by_role("button", name="Reset Password").click()
    expect(page.get_by_role("status")).to_contain_text(re.compile("invalid or expired", re.I))
    [(stored,)] = stack.sql("SELECT password_hash FROM users WHERE email=%s", (user.email,))
    assert verify_password(user.password, stored)
