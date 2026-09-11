"""
Phase 5: session helper (get_current_user/login_required), logout, and
session-fixation tests.
"""


def _extract_token_from_email(mock_email_service):
    body = mock_email_service[-1]["body"]
    return body.split("token=")[1].split()[0].strip()


def _register_and_verify(registration_client, mock_email_service, payload):
    registration_client.post("/api/auth/register", json=payload)
    raw_token = _extract_token_from_email(mock_email_service)
    registration_client.get(f"/api/auth/verify-email?token={raw_token}")


def _login(registration_client, payload):
    return registration_client.post("/api/auth/login", json={
        "email": payload["email"],
        "password": payload["password"],
    })


# --- D: session identification ------------------------------------------------

def test_me_without_any_session_is_rejected(registration_client):
    response = registration_client.get("/api/auth/me")
    assert response.status_code == 401
    assert response.get_json() == {"error": "Authentication required"}


def test_me_after_login_identifies_the_correct_user(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _login(registration_client, valid_registration_payload)

    response = registration_client.get("/api/auth/me")
    assert response.status_code == 200
    assert response.get_json()["user"]["email"] == "ada@example.com"


def test_me_response_never_exposes_password_hash(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _login(registration_client, valid_registration_payload)

    response = registration_client.get("/api/auth/me")
    body_text = response.get_data(as_text=True)
    assert "password_hash" not in body_text


def test_me_with_a_forged_cookie_value_is_rejected(registration_client):
    """A cookie value that was never actually issued by the server (not
    derived from any real session) must not authenticate anything -
    identity is derived only from a database-validated token, never
    trusted from the client as-is."""
    registration_client.set_cookie("ppt_session", "attacker-forged-token-value")
    response = registration_client.get("/api/auth/me")
    assert response.status_code == 401


# --- E: logout -----------------------------------------------------------------

def test_logout_after_login_succeeds(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _login(registration_client, valid_registration_payload)

    response = registration_client.post("/api/auth/logout")
    assert response.status_code == 200
    assert response.get_json() == {"message": "Logged out"}


def test_session_is_invalid_after_logout(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _login(registration_client, valid_registration_payload)

    registration_client.post("/api/auth/logout")

    response = registration_client.get("/api/auth/me")
    assert response.status_code == 401


def test_repeated_logout_is_safe(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _login(registration_client, valid_registration_payload)

    first = registration_client.post("/api/auth/logout")
    second = registration_client.post("/api/auth/logout")

    assert first.status_code == 200
    assert second.status_code == 200


def test_logout_with_no_session_at_all_is_safe(registration_client):
    response = registration_client.post("/api/auth/logout")
    assert response.status_code == 200


def test_logout_clears_the_cookie(
    registration_client, mock_email_service, valid_registration_payload
):
    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _login(registration_client, valid_registration_payload)

    response = registration_client.post("/api/auth/logout")
    set_cookie = response.headers.get("Set-Cookie", "")
    assert "ppt_session=" in set_cookie
    # Werkzeug expires deleted cookies immediately (Expires in the past /
    # Max-Age=0) - either marker confirms this is a deletion, not a
    # fresh valid cookie.
    assert ("Max-Age=0" in set_cookie) or ("1970" in set_cookie)


# --- H: session fixation --------------------------------------------------------

def test_login_never_reuses_an_attacker_preset_cookie_value(
    registration_client, mock_email_service, valid_registration_payload
):
    """Simulates the classic fixation attempt: an attacker gets a victim's
    browser to carry a cookie value the attacker already knows BEFORE the
    victim logs in, hoping the app will just "authenticate" that same
    value. Since this app always mints a brand-new random token server-
    side on every successful login (never accepts or extends whatever
    cookie value was already present), the post-login cookie must never
    equal the attacker-supplied one."""
    attacker_chosen_value = "attacker-knows-this-exact-value-12345"
    registration_client.set_cookie("ppt_session", attacker_chosen_value)

    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    login_response = _login(registration_client, valid_registration_payload)

    set_cookie_header = login_response.headers.get("Set-Cookie", "")
    assert attacker_chosen_value not in set_cookie_header


def test_attacker_preset_cookie_does_not_grant_access_after_victim_logs_in(
    registration_client, mock_email_service, valid_registration_payload
):
    """Even if an attacker's own browser still holds their originally-
    chosen cookie value after the victim's real login happened elsewhere,
    that stale/forged value must never resolve to the victim's account -
    there's no server-side row for it, because login() never wrote one
    for it."""
    attacker_chosen_value = "attacker-knows-this-exact-value-12345"

    _register_and_verify(registration_client, mock_email_service, valid_registration_payload)
    _login(registration_client, valid_registration_payload)  # real login mints its own token

    # A separate client presenting only the attacker's guessed value,
    # never the real one the server actually issued.
    with registration_client.application.test_client() as attacker_client:
        attacker_client.set_cookie("ppt_session", attacker_chosen_value)
        response = attacker_client.get("/api/auth/me")
        assert response.status_code == 401
