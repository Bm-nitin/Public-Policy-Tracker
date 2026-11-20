"""Policy explorer, chat, conversation history, dashboard, and account."""

import re

from playwright.sync_api import expect
import pytest

from e2e.workflows import AI_REPLY, account, api_request, open_page, sign_in


@pytest.mark.public_trace
def test_search_filter_details_and_empty_results(page, stack):
    open_page(page, stack, "policies.html")
    expect(page.get_by_role("heading", name="Policies", exact=True)).to_be_visible()
    expect(page.get_by_text("Learning Access Grant", exact=True)).to_be_visible()
    expect(page.get_by_text("Solar Access Grant", exact=True)).to_be_visible()

    sector = page.get_by_role("combobox", name="Filter by sector")
    sector.select_option("energy")
    expect(page.get_by_text("Learning Access Grant", exact=True)).to_have_count(0)
    expect(page.get_by_text("Solar Access Grant", exact=True)).to_be_visible()
    details = page.get_by_role("button", name="View Details")
    details.click()
    dialog = page.get_by_role("dialog", name="Solar Access Grant")
    expect(dialog).to_be_visible()
    expect(dialog.get_by_text("Funds home solar equipment.")).to_be_visible()
    expect(dialog.get_by_role("button", name="Close details")).to_be_focused()
    page.keyboard.press("Escape")
    expect(dialog).to_be_hidden()
    expect(details).to_be_focused()

    search = page.get_by_role("searchbox", name="Search policies")
    search.fill("could not match any record")
    expect(page.get_by_text("No policies found.")).to_be_visible()
    page.get_by_role("button", name="Clear Filters").first.click()
    search.fill("learning")
    listings = page.locator("#policiesGrid")
    expect(listings.get_by_text("Learning Access Grant", exact=True)).to_be_visible()
    expect(listings.get_by_text("Solar Access Grant", exact=True)).to_have_count(0)


@pytest.mark.public_trace
@pytest.mark.parametrize("status, body", [(503, '{"error":"unavailable"}'), (200, '<not-json>')])
def test_policy_explorer_reports_fetch_errors_and_recovers(page, stack, status, body):
    def listing(route):
        route.fulfill(
            status=status, body=body, content_type="application/json",
            headers={"Access-Control-Allow-Origin": stack.frontend, "Access-Control-Allow-Credentials": "true"},
        )

    pattern = re.compile(re.escape(stack.api) + r"/api/policies\?")
    page.route(pattern, listing)
    open_page(page, stack, "policies.html")
    expect(page.get_by_text("Unable to load policies right now. Please try again.")).to_be_visible()
    page.unroute(pattern, listing)
    page.reload()
    expect(page.get_by_text("Learning Access Grant", exact=True)).to_be_visible()


def test_saved_policies_are_protected_and_visible_on_dashboard(page, stack):
    open_page(page, stack, "policies.html")
    assert api_request(page, stack, "/api/saved-policies", method="POST", body={"policy_id": 1})["status"] == 401
    user = account(stack, seeded=True)
    sign_in(page, stack, user, "dashboard.html")
    expect(page.get_by_text("You haven't saved any policies yet.")).to_be_visible()
    expect(page.get_by_text("No conversations yet.")).to_be_visible()
    expect(page.get_by_text("No recent activity.")).to_be_visible()

    saved = api_request(page, stack, "/api/saved-policies", method="POST", body={"policy_id": 2})
    assert saved["status"] == 201
    assert stack.sql("SELECT policy_id FROM saved_policies") == [(2,)]
    page.reload()
    expect(page.locator("#statSaved")).to_have_text("1")
    page.get_by_role("link", name="View details for Solar Access Grant").click()
    expect(page.get_by_role("dialog", name="Solar Access Grant")).to_be_visible()

    result = api_request(page, stack, "/api/saved-policies/2", method="DELETE")
    assert result["status"] == 200
    assert stack.sql("SELECT count(*) FROM saved_policies") == [(0,)]
    open_page(page, stack, "dashboard.html")
    expect(page.get_by_text("You haven't saved any policies yet.")).to_be_visible()


@pytest.mark.public_trace
def test_chat_ignores_empty_and_duplicate_submissions(page, stack):
    open_page(page, stack, "index.html")
    assert api_request(page, stack, "/api/conversations", method="POST", body={})["status"] == 401
    chat = page.get_by_role("log", name="Conversation with the policy assistant")
    field = page.get_by_role("textbox", name="Your question")
    send = page.get_by_role("button", name="Send message")
    url = f"{stack.api}/chat"
    paused = []

    def hold_chat(route):
        paused.append(route)

    page.route(url, hold_chat)
    field.fill("   ")
    send.click()
    expect(chat.locator(".message")).to_have_count(0)
    field.fill("What does the solar policy cover?")
    with page.expect_request(url):
        field.press("Enter")
    expect(chat.get_by_text("...")).to_be_visible()

    field.fill("This extra submission should wait")
    send.click()
    expect(chat.locator(".message.user")).to_have_count(1)
    assert len(paused) == 1
    paused[0].continue_()
    expect(chat.get_by_text(AI_REPLY)).to_be_visible()
    assert stack.sql("SELECT count(*) FROM messages") == [(0,)]


@pytest.mark.public_trace
@pytest.mark.parametrize("status, body, feedback", [
    (503, '{"error":"unavailable"}', "No response from server."),
    (200, '<not-json>', "Server error. Is backend running?"),
])
def test_chat_api_failure_is_readable_and_retry_works(page, stack, status, body, feedback):
    path = f"{stack.api}/chat"

    def fail_chat(route):
        route.fulfill(
            status=status, body=body, content_type="application/json",
            headers={"Access-Control-Allow-Origin": stack.frontend},
        )

    page.route(path, fail_chat)
    open_page(page, stack, "index.html")
    field = page.get_by_role("textbox", name="Your question")
    field.fill("Tell me about energy.")
    field.press("Enter")
    expect(page.get_by_role("log").get_by_text(feedback)).to_be_visible()
    expect(page.get_by_text("<not-json>", exact=True)).to_have_count(0)
    page.unroute(path, fail_chat)
    field.fill("Try again.")
    field.press("Enter")
    expect(page.get_by_role("log").get_by_text(AI_REPLY)).to_be_visible()


def test_conversation_creation_replay_switch_and_persistence(page, stack):
    user = account(stack, seeded=True)
    sign_in(page, stack, user, "dashboard.html")
    conversations = {}
    for title in ("First Town Hall", "Second Town Hall"):
        response = api_request(page, stack, "/api/conversations", method="POST", body={"title": title})
        assert response["status"] == 201
        conversation = response["data"]["data"]["id"]
        conversations[title] = conversation
        message = api_request(
            page, stack, f"/api/conversations/{conversation}/messages",
            method="POST", body={"role": "assistant", "content": f"Prior notes for {title}."},
        )
        assert message["status"] == 201

    page.reload()
    expect(page.locator("#statConversations")).to_have_text("2")
    page.get_by_role("link", name="Continue conversation: First Town Hall").click()
    history = page.get_by_role("log")
    expect(history.get_by_text("Prior notes for First Town Hall.")).to_be_visible()
    expect(history.get_by_text("Prior notes for Second Town Hall.")).to_have_count(0)
    question = "Continue the first discussion."
    page.get_by_role("textbox", name="Your question").fill(question)
    page.get_by_role("button", name="Send message").click()
    expect(history.get_by_text(AI_REPLY)).to_be_visible()

    assert stack.sql(
        "SELECT role, content FROM messages WHERE conversation_id=%s ORDER BY id",
        (conversations["First Town Hall"],),
    ) == [("assistant", "Prior notes for First Town Hall."), ("user", question), ("assistant", AI_REPLY)]

    page.get_by_role("navigation", name="Main", exact=True).get_by_role("link", name="Dashboard").click()
    page.get_by_role("link", name="Continue conversation: Second Town Hall").click()
    expect(history.get_by_text("Prior notes for Second Town Hall.")).to_be_visible()
    expect(history.get_by_text(question)).to_have_count(0)


def test_profile_validation_and_update_persist(page, stack):
    user = account(stack, seeded=True)
    sign_in(page, stack, user, "profile.html")
    expect(page.get_by_role("heading", name="Account", exact=True)).to_be_visible()
    name = page.get_by_role("textbox", name="Display name")
    expect(name).to_have_value(user.name)
    name.fill("  ")
    page.get_by_role("button", name="Save changes").click()
    expect(page.get_by_text("Enter a display name.")).to_be_visible()
    expect(name).to_have_attribute("aria-invalid", "true")
    updated = stack.keep_private("Updated Browser Account")
    name.fill(updated)
    page.get_by_role("button", name="Save changes").click()
    expect(page.get_by_text("Profile updated.")).to_be_visible()
    expect(page.locator("#header-account")).to_contain_text(updated)
    assert stack.sql("SELECT name FROM users WHERE email=%s", (user.email,)) == [(updated,)]
    page.reload()
    expect(name).to_have_value(updated)


def test_expired_session_requires_login_on_protected_pages(page, stack):
    user = account(stack, seeded=True)
    sign_in(page, stack, user, "dashboard.html")
    stack.sql("UPDATE user_sessions SET expires_at=now()-interval '1 hour'")
    assert api_request(page, stack, "/api/dashboard")["status"] == 401
    page.reload()
    expect(page).to_have_url(f"{stack.frontend}/login.html?next=dashboard.html")
    open_page(page, stack, "profile.html")
    expect(page).to_have_url(f"{stack.frontend}/login.html?next=profile.html")


def test_dashboard_service_error_is_generic_and_retry_recovers(page, stack):
    user = account(stack, seeded=True)
    sign_in(page, stack, user, "dashboard.html")
    url = f"{stack.api}/api/dashboard"

    def unavailable(route):
        route.fulfill(status=503, json={"error": "Traceback: synthetic internal detail"}, headers={
            "Access-Control-Allow-Origin": stack.frontend, "Access-Control-Allow-Credentials": "true",
        })

    page.route(url, unavailable)
    page.reload()
    expect(page.get_by_role("heading", name="Dashboard unavailable")).to_be_visible()
    expect(page.get_by_text("Traceback: synthetic internal detail")).to_have_count(0)
    page.unroute(url, unavailable)
    page.get_by_role("button", name="Try again").click()
    expect(page.get_by_role("heading", name="Recent saved policies")).to_be_visible()


def test_account_password_change_requires_the_current_password(page, stack):
    from security import verify_password

    user = account(stack, seeded=True)
    sign_in(page, stack, user, "profile.html")
    current = page.get_by_label("Current password", exact=True)
    current.fill(stack.password())
    changed = stack.password()
    page.get_by_label("New password", exact=True).fill(changed)
    page.get_by_label("Confirm new password", exact=True).fill(changed)
    update = page.get_by_role("button", name="Update password")
    update.click()
    expect(page.locator("#accCurrentPwError")).to_contain_text(re.compile("current password", re.I))
    [(stored,)] = stack.sql("SELECT password_hash FROM users WHERE email=%s", (user.email,))
    assert verify_password(user.password, stored)
    current.fill(user.password)
    update.click()
    expect(page.locator("#passwordStatus")).to_contain_text("Password updated.")
    [(stored,)] = stack.sql("SELECT password_hash FROM users WHERE email=%s", (user.email,))
    assert verify_password(changed, stored)
    assert api_request(page, stack, "/api/auth/me")["status"] == 200


def test_untrusted_origin_is_rejected_without_logging_out(page, stack):
    user = account(stack, seeded=True)
    sign_in(page, stack, user)
    open_page(page, stack, "")
    assert api_request(page, stack, "/api/auth/me")["status"] == 200
    page.goto(stack.other_origin)
    url = f"{stack.api}/api/auth/logout"
    with page.expect_response(url) as denial:
        page.evaluate(
            """url => fetch(url, {method: 'POST', mode: 'no-cors',
                                  credentials: 'include', body: 'origin check'})""",
            url,
        )
    response = denial.value
    assert response.status == 403
    assert response.request.header_value("origin") == stack.other_origin
    assert stack.sql("SELECT revoked_at IS NULL FROM user_sessions") == [(True,)]
    open_page(page, stack, "index.html")
    expect(page.get_by_role("button", name="Logout")).to_be_visible()
