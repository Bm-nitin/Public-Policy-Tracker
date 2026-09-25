"""
Phase 14 - dashboard backend tests.

Reuses the exact db_test_app + real-session-cookie authentication
pattern tests/test_chat_history.py (Phase 12) and
tests/test_saved_policies.py (Phase 13) already established. No real
Gemini API call anywhere in this file - the dashboard never touches
Gemini at all.
"""

import pytest


# =================================================================
# fixtures
# =================================================================

@pytest.fixture
def dashboard_app(db_test_app, monkeypatch):
    import config
    from auth_routes import auth_bp
    from conversations_routes import conversations_bp
    from dashboard_routes import dashboard_bp
    from saved_policies_routes import saved_policies_bp

    monkeypatch.setattr(config.Config, "DATABASE_URL", "sqlite:///:memory:")
    db_test_app.register_blueprint(auth_bp)
    db_test_app.register_blueprint(conversations_bp)
    db_test_app.register_blueprint(saved_policies_bp)
    db_test_app.register_blueprint(dashboard_bp)
    return db_test_app


def _create_user(app, email, name="Test User"):
    from database import db
    from models import User

    with app.app_context():
        user = User(name=name, email=email, password_hash="SHOULD_NEVER_LEAK", email_verified=True)
        db.session.add(user)
        db.session.commit()
        return user.id


def _login(app, client, user_id):
    import config
    from database import db
    from models import UserSession
    from sessions import generate_session_token

    raw_token, token_hash, expires_at = generate_session_token()
    with app.app_context():
        db.session.add(UserSession(user_id=user_id, session_token_hash=token_hash, expires_at=expires_at))
        db.session.commit()
    client.set_cookie(config.Config.SESSION_COOKIE_NAME, raw_token)


@pytest.fixture
def authed_client(dashboard_app):
    client = dashboard_app.test_client()
    user_id = _create_user(dashboard_app, "solo@example.com", name="Solo User")
    _login(dashboard_app, client, user_id)
    return client, user_id


@pytest.fixture
def two_users_and_clients(dashboard_app):
    client_a = dashboard_app.test_client()
    client_b = dashboard_app.test_client()
    user_a_id = _create_user(dashboard_app, "alice@example.com", name="Alice")
    user_b_id = _create_user(dashboard_app, "bob@example.com", name="Bob")
    _login(dashboard_app, client_a, user_a_id)
    _login(dashboard_app, client_b, user_b_id)
    return client_a, user_a_id, client_b, user_b_id


# =================================================================
# AUTH
# =================================================================

def test_unauthenticated_dashboard_returns_401(dashboard_app):
    client = dashboard_app.test_client()
    resp = client.get("/api/dashboard")
    assert resp.status_code == 401


def test_authenticated_user_receives_dashboard(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body.keys()) == {"user", "stats", "recent_saved_policies", "recent_conversations", "recent_activity"}


# =================================================================
# USER DATA
# =================================================================

def test_correct_current_user_returned(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/dashboard")
    user = resp.get_json()["user"]
    assert user["id"] == user_id
    assert user["name"] == "Solo User"
    assert user["email"] == "solo@example.com"
    assert user["email_verified"] is True
    assert "created_at" in user


def test_password_hash_never_returned(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/dashboard")
    body_text = str(resp.get_json())
    assert "SHOULD_NEVER_LEAK" not in body_text
    assert "password_hash" not in resp.get_json()["user"]


def test_security_tokens_never_returned(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/dashboard")
    body_text = str(resp.get_json()).lower()
    for forbidden in ("session_token", "token_hash", "verification_token", "reset_token", "password"):
        assert forbidden not in body_text


# =================================================================
# STATS
# =================================================================

def test_saved_policy_count_correct(authed_client):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": 1})
    client.post("/api/saved-policies", json={"policy_id": 2})
    client.post("/api/saved-policies", json={"policy_id": 3})

    resp = client.get("/api/dashboard")
    assert resp.get_json()["stats"]["saved_policies"] == 3


def test_conversation_count_correct(authed_client):
    client, user_id = authed_client
    client.post("/api/conversations", json={"title": "A"})
    client.post("/api/conversations", json={"title": "B"})

    resp = client.get("/api/dashboard")
    assert resp.get_json()["stats"]["conversations"] == 2


def test_message_count_correct(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "A"}).get_json()["data"]["id"]
    client.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": "hi"})
    client.post(f"/api/conversations/{conv_id}/messages", json={"role": "assistant", "content": "hello"})

    resp = client.get("/api/dashboard")
    assert resp.get_json()["stats"]["messages"] == 2


def test_counts_are_user_scoped(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    client_a.post("/api/saved-policies", json={"policy_id": 1})
    client_a.post("/api/conversations", json={"title": "A"})

    resp_b = client_b.get("/api/dashboard")
    stats_b = resp_b.get_json()["stats"]
    assert stats_b == {"saved_policies": 0, "conversations": 0, "messages": 0}


# =================================================================
# SAVED POLICIES
# =================================================================

def test_recent_saved_policies_returned(authed_client):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": 1})

    resp = client.get("/api/dashboard")
    recent = resp.get_json()["recent_saved_policies"]
    assert len(recent) == 1
    assert recent[0]["policy_id"] == 1
    assert "policy" in recent[0]


def test_saved_policies_ordering_newest_first(authed_client):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": 1})
    client.post("/api/saved-policies", json={"policy_id": 2})
    client.post("/api/saved-policies", json={"policy_id": 3})

    resp = client.get("/api/dashboard")
    ids_in_order = [item["policy_id"] for item in resp.get_json()["recent_saved_policies"]]
    assert ids_in_order == [3, 2, 1]


def test_saved_policy_data_comes_from_json(authed_client):
    from policy_loader import get_policy_by_id as loader_get

    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": 1})

    resp = client.get("/api/dashboard")
    returned = resp.get_json()["recent_saved_policies"][0]["policy"]
    real = loader_get(1)
    assert returned["name"] == real["name"]
    assert returned["change"] == real["change"]
    assert returned["impact"] == real["impact"]


def test_current_json_changes_are_reflected(authed_client, monkeypatch):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": 1})

    import dashboard_service

    def _patched(policy_id):
        return {
            "id": policy_id, "name": "CHANGED NAME", "sector": "test",
            "category": "test", "sub_category": "test", "change": "test", "impact": "test",
        }
    monkeypatch.setattr(dashboard_service, "get_policy_by_id", _patched)

    resp = client.get("/api/dashboard")
    assert resp.get_json()["recent_saved_policies"][0]["policy"]["name"] == "CHANGED NAME"


def test_missing_json_policy_does_not_crash_dashboard(authed_client, monkeypatch):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": 1})

    import dashboard_service
    monkeypatch.setattr(dashboard_service, "get_policy_by_id", lambda policy_id: None)

    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    assert resp.get_json()["recent_saved_policies"] == []


def test_another_users_saved_policies_are_excluded(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    client_a.post("/api/saved-policies", json={"policy_id": 1})

    resp_b = client_b.get("/api/dashboard")
    assert resp_b.get_json()["recent_saved_policies"] == []


# =================================================================
# CONVERSATIONS
# =================================================================

def test_recent_conversations_returned(authed_client):
    client, user_id = authed_client
    client.post("/api/conversations", json={"title": "My Chat"})

    resp = client.get("/api/dashboard")
    recent = resp.get_json()["recent_conversations"]
    assert len(recent) == 1
    assert recent[0]["title"] == "My Chat"
    assert set(recent[0].keys()) >= {"id", "title", "created_at", "updated_at"}


def test_conversations_ordering_newest_updated_first(authed_client):
    client, user_id = authed_client
    first_id = client.post("/api/conversations", json={"title": "First"}).get_json()["data"]["id"]
    client.post("/api/conversations", json={"title": "Second"})

    # Touch "First" again so it should sort ahead.
    client.post(f"/api/conversations/{first_id}/messages", json={"role": "user", "content": "bump"})

    resp = client.get("/api/dashboard")
    ids_in_order = [c["id"] for c in resp.get_json()["recent_conversations"]]
    assert ids_in_order[0] == first_id


def test_another_users_conversations_are_excluded(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    client_a.post("/api/conversations", json={"title": "Alice's"})

    resp_b = client_b.get("/api/dashboard")
    assert resp_b.get_json()["recent_conversations"] == []


def test_conversation_message_count_included(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "A"}).get_json()["data"]["id"]
    client.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": "one"})
    client.post(f"/api/conversations/{conv_id}/messages", json={"role": "assistant", "content": "two"})

    resp = client.get("/api/dashboard")
    assert resp.get_json()["recent_conversations"][0]["message_count"] == 2


# =================================================================
# ACTIVITY
# =================================================================

def test_expected_activity_types_returned(authed_client):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": 1})
    conv_id = client.post("/api/conversations", json={"title": "A"}).get_json()["data"]["id"]
    client.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": "hi"})

    resp = client.get("/api/dashboard")
    types_seen = {item["type"] for item in resp.get_json()["recent_activity"]}
    assert types_seen == {"policy_saved", "conversation_created", "message_sent"}


def test_activity_newest_first(authed_client):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": 1})
    client.post("/api/conversations", json={"title": "A"})
    client.post("/api/saved-policies", json={"policy_id": 2})

    resp = client.get("/api/dashboard")
    timestamps = [item["created_at"] for item in resp.get_json()["recent_activity"]]
    assert timestamps == sorted(timestamps, reverse=True)


def test_activity_result_is_bounded(authed_client):
    client, user_id = authed_client
    for policy_id in range(1, 16):
        client.post("/api/saved-policies", json={"policy_id": policy_id})

    resp = client.get("/api/dashboard?activity_limit=5")
    assert len(resp.get_json()["recent_activity"]) == 5


def test_activity_never_leaks_message_content(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "A"}).get_json()["data"]["id"]
    client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"role": "user", "content": "SECRET_MESSAGE_CONTENT_TOKEN"},
    )

    resp = client.get("/api/dashboard")
    body_text = str(resp.get_json())
    assert "SECRET_MESSAGE_CONTENT_TOKEN" not in body_text

    message_events = [i for i in resp.get_json()["recent_activity"] if i["type"] == "message_sent"]
    assert message_events
    assert set(message_events[0].keys()) == {"type", "created_at", "reference_id"}


def test_another_users_activity_is_excluded(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    client_a.post("/api/saved-policies", json={"policy_id": 1})
    client_a.post("/api/conversations", json={"title": "Alice's"})

    resp_b = client_b.get("/api/dashboard")
    assert resp_b.get_json()["recent_activity"] == []


# =================================================================
# LIMITS
# =================================================================

def test_default_limits_applied(authed_client):
    client, user_id = authed_client
    for policy_id in range(1, 11):
        client.post("/api/saved-policies", json={"policy_id": policy_id})

    resp = client.get("/api/dashboard")
    assert len(resp.get_json()["recent_saved_policies"]) == 5  # default


def test_custom_valid_limit_respected(authed_client):
    client, user_id = authed_client
    for policy_id in range(1, 11):
        client.post("/api/saved-policies", json={"policy_id": policy_id})

    resp = client.get("/api/dashboard?saved_limit=3")
    assert len(resp.get_json()["recent_saved_policies"]) == 3


def test_limit_of_20_is_accepted(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/dashboard?saved_limit=20&conversation_limit=20&activity_limit=20")
    assert resp.status_code == 200


def test_limit_above_20_is_rejected(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/dashboard?saved_limit=21")
    assert resp.status_code == 400


def test_zero_limit_is_rejected(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/dashboard?conversation_limit=0")
    assert resp.status_code == 400


def test_negative_limit_is_rejected(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/dashboard?activity_limit=-1")
    assert resp.status_code == 400


def test_malformed_limit_is_rejected(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/dashboard?saved_limit=abc")
    assert resp.status_code == 400


def test_boolean_limit_is_rejected_at_service_layer(dashboard_app):
    """Query strings can never actually carry a Python bool (Flask's
    request.args values are always strings), so the boolean-rejection
    rule is exercised directly against dashboard_service, which is
    exactly where that rule lives and could be reused by any future
    non-HTTP caller."""
    import dashboard_service as ds

    with dashboard_app.app_context():
        with pytest.raises(ds.ValidationError):
            ds._validate_limit(True, ds.DEFAULT_RECENT_LIMIT, ds.MAX_RECENT_LIMIT)
        with pytest.raises(ds.ValidationError):
            ds._validate_limit(False, ds.DEFAULT_RECENT_LIMIT, ds.MAX_RECENT_LIMIT)


# =================================================================
# SECURITY
# =================================================================

def test_user_id_query_param_cannot_change_identity(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    client_a.post("/api/saved-policies", json={"policy_id": 1})

    resp = client_a.get(f"/api/dashboard?user_id={user_b}")
    assert resp.get_json()["user"]["id"] == user_a
    assert len(resp.get_json()["recent_saved_policies"]) == 1


def test_idor_saved_policies_conversations_messages_and_counts(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients

    client_a.post("/api/saved-policies", json={"policy_id": 1})
    conv_id = client_a.post("/api/conversations", json={"title": "Alice's"}).get_json()["data"]["id"]
    client_a.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": "hi"})

    resp_b = client_b.get("/api/dashboard").get_json()
    assert resp_b["stats"] == {"saved_policies": 0, "conversations": 0, "messages": 0}
    assert resp_b["recent_saved_policies"] == []
    assert resp_b["recent_conversations"] == []
    assert resp_b["recent_activity"] == []


def test_no_sensitive_fields_anywhere_in_response(authed_client):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": 1})
    client.post("/api/conversations", json={"title": "A"})

    resp = client.get("/api/dashboard")
    body_text = str(resp.get_json()).lower()
    for forbidden in ("password", "secret", "api_key", "database_url", "token"):
        assert forbidden not in body_text


def test_database_error_does_not_leak_internals(authed_client, monkeypatch):
    client, user_id = authed_client

    import dashboard_routes

    def _boom(*a, **k):
        raise RuntimeError("connection to postgresql://user:pass@host/db failed")
    # Patched on dashboard_routes (not dashboard_service) - dashboard_routes.py
    # does `from dashboard_service import get_dashboard_summary` (a name
    # import), so it has its own separately-bound reference; patching
    # dashboard_service's copy would leave that reference untouched.
    monkeypatch.setattr(dashboard_routes, "get_dashboard_summary", _boom)

    resp = client.get("/api/dashboard")
    assert resp.status_code == 500
    body_text = str(resp.get_json())
    assert "postgresql://user:pass" not in body_text
    assert "RuntimeError" not in body_text
    assert "Traceback" not in body_text
    assert set(resp.get_json().keys()) == {"error"}
