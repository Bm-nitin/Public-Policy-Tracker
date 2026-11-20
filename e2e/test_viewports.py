"""Viewport checks and smoke tests shared by Chromium, Firefox, and WebKit."""

from playwright.sync_api import expect
import pytest

from e2e.workflows import (
    AI_REPLY, account, assert_no_horizontal_overflow, assert_within_viewport,
    open_navigation, open_page, sign_in,
)


@pytest.mark.cross_browser
@pytest.mark.public_trace
def test_landing_navigation_and_login_form(page, stack, browser, browser_name):
    print(f"{browser_name} {browser.version}")
    open_page(page, stack, "index.html")
    expect(page.get_by_role("textbox", name="Your question")).to_be_visible()
    open_navigation(page).get_by_role("link", name="Policies").click()
    expect(page.get_by_role("heading", name="Policies", exact=True)).to_be_visible()
    page.get_by_role("link", name="Sign In").click()
    expect(page.get_by_role("heading", name="Welcome back")).to_be_visible()
    expect(page.locator(".sign-in").get_by_label("Email", exact=True)).to_be_visible()


@pytest.mark.cross_browser
def test_login_and_dashboard_smoke(page, stack):
    user = account(stack, seeded=True)
    sign_in(page, stack, user, "dashboard.html")
    expect(page.get_by_role("heading", name=user.name)).to_be_visible()
    expect(page.get_by_role("heading", name="Recent saved policies")).to_be_visible()
    expect(page.get_by_text("No conversations yet.")).to_be_visible()


@pytest.mark.parametrize("width,height", [(1366, 768), (768, 1024), (390, 844)])
def test_main_workflows_at_representative_viewports(page, stack, width, height):
    page.set_viewport_size({"width": width, "height": height})
    open_page(page, stack, "index.html")
    menu = page.get_by_role("button", name="Navigation menu")
    nav = open_navigation(page)
    if menu.is_visible():
        page.keyboard.press("Escape")
        expect(menu).to_be_focused()
        expect(menu).to_have_attribute("aria-expanded", "false")
        nav = open_navigation(page)
    nav.get_by_role("link", name="Policies").click()

    policy_card = page.locator(".policy-card").filter(has_text="Learning Access Grant")
    details = policy_card.get_by_role("button", name="View Details")
    assert_within_viewport(details)
    assert_no_horizontal_overflow(page)
    details.click()
    dialog = page.get_by_role("dialog", name="Learning Access Grant")
    assert_within_viewport(dialog)
    expect(dialog.get_by_role("button", name="Close details")).to_be_focused()
    page.keyboard.press("Escape")
    expect(details).to_be_focused()

    open_page(page, stack, "login.html")
    sign_in_panel = page.locator(".sign-in")
    assert_within_viewport(sign_in_panel.get_by_label("Email", exact=True))
    assert_within_viewport(sign_in_panel.get_by_role("button", name="Sign In"))
    assert_no_horizontal_overflow(page)

    user = account(stack, seeded=True)
    sign_in(page, stack, user, "dashboard.html")
    assert_within_viewport(page.get_by_role("heading", name="Recent saved policies"))
    assert_no_horizontal_overflow(page)

    open_page(page, stack, "index.html")
    field = page.get_by_role("textbox", name="Your question")
    submit = page.get_by_role("button", name="Send message")
    assert_within_viewport(field)
    assert_within_viewport(submit)
    field_bounds, submit_bounds = field.bounding_box(), submit.bounding_box()
    assert field_bounds is not None and submit_bounds is not None
    assert field_bounds["x"] + field_bounds["width"] <= submit_bounds["x"] + 1
    assert field.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 14
    field.fill("Show me a policy.")
    submit.click()
    expect(page.get_by_role("log").get_by_text(AI_REPLY)).to_be_visible()
    assert_no_horizontal_overflow(page)
