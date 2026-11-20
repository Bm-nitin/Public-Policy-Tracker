"""Browser actions and synthetic accounts shared by E2E cases."""

from dataclasses import dataclass
from uuid import uuid4

from playwright.sync_api import expect


AI_REPLY = "The browser test assistant found a relevant policy."
COOKIE_NAME = "ppt_e2e"


@dataclass(repr=False)
class TestAccount:
    name: str
    email: str
    password: str


def account(stack, *, seeded=False):
    user = TestAccount(
        stack.keep_private("Browser Test Account"),
        stack.keep_private(f"{uuid4().hex}@users.example.test"),
        stack.password(),
    )
    if seeded:
        from security import hash_password

        stack.sql(
            "INSERT INTO users (name, email, password_hash, email_verified) VALUES (%s, %s, %s, true)",
            (user.name, user.email, hash_password(user.password)),
        )
    return user


def open_page(page, stack, name):
    response = page.goto(f"{stack.frontend}/{name}")
    assert response is not None and response.ok


def dialog_message(page, action):
    with page.expect_event("dialog") as dialog:
        page.once("dialog", lambda prompt: prompt.accept())
        action()
    return dialog.value.message


def open_registration(page, stack):
    open_page(page, stack, "login.html")
    page.get_by_role("button", name="Sign Up").click()
    expect(page.get_by_role("heading", name="Create your account")).to_be_visible()
    return page.locator(".sign-up")


def register(page, stack, user):
    form = open_registration(page, stack)
    form.get_by_label("Name", exact=True).fill(user.name)
    form.get_by_label("Email", exact=True).fill(user.email)
    form.get_by_label("Password", exact=True).fill(user.password)
    return dialog_message(page, form.get_by_role("button", name="Sign Up").click)


def sign_in(page, stack, user, destination="index.html"):
    url = "login.html" if destination == "index.html" else f"login.html?next={destination}"
    open_page(page, stack, url)
    form = page.locator(".sign-in")
    form.get_by_label("Email", exact=True).fill(user.email)
    form.get_by_label("Password", exact=True).fill(user.password)
    form.get_by_role("button", name="Sign In").click()
    expect(page).to_have_url(f"{stack.frontend}/{destination}")
    expect(page.get_by_role("button", name="Logout")).to_be_visible()


def api_request(page, stack, path, *, method="GET", body=None):
    return page.evaluate(
        """async ({url, method, body}) => {
            const options = {method, credentials: 'include'};
            if (body !== null) {
                options.headers = {'Content-Type': 'application/json'};
                options.body = JSON.stringify(body);
            }
            const response = await fetch(url, options);
            return {status: response.status, data: await response.json()};
        }""",
        {"url": stack.api + path, "method": method, "body": body},
    )


def reset_request(page, stack, user):
    open_page(page, stack, "login.html")
    page.get_by_role("link", name="Forgot password?").click()
    page.get_by_label("Email", exact=True).fill(user.email)
    page.get_by_role("button", name="Send Reset Link").click()
    expect(page.get_by_role("status")).to_contain_text("If an account with that email exists")
    return stack.mail_link(user.email, "reset")


def assert_within_viewport(locator):
    expect(locator).to_be_visible()
    assert locator.evaluate("""node => {
        const rect = node.getBoundingClientRect();
        return rect.width > 0 && rect.height > 0 && rect.left >= -1 &&
               rect.right <= document.documentElement.clientWidth + 1;
    }""")


def assert_no_horizontal_overflow(page):
    assert page.evaluate(
        "document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
    )


def open_navigation(page):
    menu = page.get_by_role("button", name="Navigation menu")
    if menu.is_visible():
        menu.click()
        expect(menu).to_have_attribute("aria-expanded", "true")
    nav = page.get_by_role("navigation", name="Main", exact=True)
    expect(nav).to_be_visible()
    return nav
