"""
Phase 12 - persistent chat history tests.

Uses a fresh, isolated db_test_app (see conftest.py) per test with
auth_bp and conversations_bp registered on it - real session cookies are
created directly (via sessions.generate_session_token() + a UserSession
row, bypassing the full register/verify-email/login HTTP round trip,
which Phase 3/5 already test end to end on their own) so these tests can
focus on conversation/message behavior itself. No real Gemini API call
anywhere - /chat integration tests monkeypatch chatbot.get_response()
directly.
"""

import pytest


# =================================================================
# fixtures
# =================================================================

@pytest.fixture
def history_app(db_test_app, monkeypatch):
    import config
    from auth_routes import auth_bp
    from conversations_routes import conversations_bp

    monkeypatch.setattr(config.Config, "DATABASE_URL", "sqlite:///:memory:")
    db_test_app.register_blueprint(auth_bp)
    db_test_app.register_blueprint(conversations_bp)

    # Mount the REAL /chat view function (not a reimplementation) onto
    # this isolated app/database, so Phase 12's /chat integration tests
    # exercise the actual production route - including its
    # conversation_id/ownership/persistence logic - against a database
    # that also has the conversations these tests create, rather than
    # whatever database backend/app.py's separately-imported shared
    # `app` instance happens to be bound to (its ambient Config.DATABASE_URL,
    # not this fixture's isolated sqlite).
    import app as app_module
    db_test_app.add_url_rule("/chat", "chat", app_module.chat, methods=["POST"])

    return db_test_app


def _create_user(app, email):
    from database import db
    from models import User

    with app.app_context():
        user = User(name="Test User", email=email, password_hash="x", email_verified=True)
        db.session.add(user)
        db.session.commit()
        return user.id


def _login(app, client, user_id):
    """Creates a real, valid UserSession row and attaches its raw token
    as the session cookie on `client` - the exact same identity
    mechanism sessions.get_current_user() checks (see that module's
    docstring), just skipping the HTTP register/verify/login round trip
    Phase 3/4/5's own tests already cover."""
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
def two_users_and_clients(history_app):
    """Returns (client_a, user_a_id, client_b, user_b_id) - two
    independently authenticated clients, for cross-user ownership tests.
    Deliberately NOT using `with app.test_client() as client:` (which
    preserves the request context after each call, for inspecting
    flask.g/request afterward) - these tests only ever read
    response.get_json(), and interleaving requests from two such
    context-preserving clients against the same global context stack
    corrupts Werkzeug's context push/pop ordering ("Popped wrong request
    context"). A plain, non-context-manager test_client() has no such
    issue here."""
    client_a = history_app.test_client()
    client_b = history_app.test_client()
    user_a_id = _create_user(history_app, "alice@example.com")
    user_b_id = _create_user(history_app, "bob@example.com")
    _login(history_app, client_a, user_a_id)
    _login(history_app, client_b, user_b_id)
    return client_a, user_a_id, client_b, user_b_id


@pytest.fixture
def authed_client(history_app):
    client = history_app.test_client()
    user_id = _create_user(history_app, "solo@example.com")
    _login(history_app, client, user_id)
    return client, user_id


# =================================================================
# 1-2. create conversation - auth required
# =================================================================

def test_authenticated_user_can_create_conversation(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/conversations", json={"title": "My First Chat"})
    assert resp.status_code == 201
    body = resp.get_json()["data"]
    assert body["title"] == "My First Chat"
    assert "id" in body and "created_at" in body and "updated_at" in body


def test_unauthenticated_user_cannot_create_conversation(history_app):
    with history_app.test_client() as client:
        resp = client.post("/api/conversations", json={"title": "Nope"})
    assert resp.status_code == 401


def test_create_conversation_generates_title_from_first_message_when_omitted(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/conversations", json={"first_message": "What changed in agriculture policy?"})
    assert resp.status_code == 201
    assert resp.get_json()["data"]["title"] == "What changed in agriculture policy?"


def test_create_conversation_with_neither_title_nor_message_uses_default(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/conversations", json={})
    assert resp.status_code == 201
    assert resp.get_json()["data"]["title"] == "New Conversation"


def test_create_conversation_rejects_oversized_title(authed_client):
    from models import MAX_TITLE_LENGTH
    client, user_id = authed_client
    resp = client.post("/api/conversations", json={"title": "x" * (MAX_TITLE_LENGTH + 1)})
    assert resp.status_code == 400


def test_create_conversation_ignores_mass_assignment_of_user_id(authed_client, two_users_and_clients):
    """Even if a client sneaks a `user_id` into the body, ownership is
    always g.current_user.id, never anything from the request."""
    client, user_id = authed_client
    _, other_user_id, _, _ = two_users_and_clients
    resp = client.post("/api/conversations", json={"title": "Mine", "user_id": other_user_id})
    assert resp.status_code == 201
    conv_id = resp.get_json()["data"]["id"]

    # The conversation belongs to `user_id` (the authenticated caller),
    # not `other_user_id`, regardless of what was in the body.
    get_resp = client.get(f"/api/conversations/{conv_id}")
    assert get_resp.status_code == 200


# =================================================================
# 3-4. list conversations - own only
# =================================================================

def test_user_can_list_own_conversations(authed_client):
    client, user_id = authed_client
    client.post("/api/conversations", json={"title": "A"})
    client.post("/api/conversations", json={"title": "B"})

    resp = client.get("/api/conversations")
    assert resp.status_code == 200
    titles = {c["title"] for c in resp.get_json()["data"]}
    assert titles == {"A", "B"}


def test_user_cannot_see_another_users_conversations(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    client_a.post("/api/conversations", json={"title": "Alice Only"})

    resp = client_b.get("/api/conversations")
    assert resp.status_code == 200
    assert resp.get_json()["data"] == []


# =================================================================
# 5-6. retrieve single conversation - ownership enforced
# =================================================================

def test_user_can_retrieve_own_conversation(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    resp = client.get(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["title"] == "Mine"


def test_user_cannot_retrieve_another_users_conversation(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    conv_id = client_a.post("/api/conversations", json={"title": "Alice's"}).get_json()["data"]["id"]

    resp = client_b.get(f"/api/conversations/{conv_id}")
    assert resp.status_code == 404


def test_retrieving_nonexistent_conversation_returns_404_not_500(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/conversations/999999")
    assert resp.status_code == 404


def test_retrieving_conversation_never_distinguishes_missing_from_not_owned(two_users_and_clients):
    """Both cases must return the exact same status/body shape - see
    conversations_service.ConversationNotFoundError's docstring."""
    client_a, user_a, client_b, user_b = two_users_and_clients
    conv_id = client_a.post("/api/conversations", json={"title": "Alice's"}).get_json()["data"]["id"]

    not_owned = client_b.get(f"/api/conversations/{conv_id}")
    nonexistent = client_b.get("/api/conversations/999999")
    assert not_owned.status_code == nonexistent.status_code == 404
    assert not_owned.get_json() == nonexistent.get_json()


# =================================================================
# 7-8. retrieve messages - ownership enforced through conversation
# =================================================================

def test_user_can_retrieve_own_messages(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]
    client.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": "Hello"})

    resp = client.get(f"/api/conversations/{conv_id}/messages")
    assert resp.status_code == 200
    assert len(resp.get_json()["data"]) == 1
    assert resp.get_json()["data"][0]["content"] == "Hello"


def test_user_cannot_retrieve_another_users_messages(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    conv_id = client_a.post("/api/conversations", json={"title": "Alice's"}).get_json()["data"]["id"]
    client_a.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": "Secret"})

    resp = client_b.get(f"/api/conversations/{conv_id}/messages")
    assert resp.status_code == 404


# =================================================================
# 9-12. message validation
# =================================================================

def test_user_can_add_valid_messages(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    resp = client.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": "Hi"})
    assert resp.status_code == 201
    assert resp.get_json()["data"]["role"] == "user"
    assert resp.get_json()["data"]["content"] == "Hi"


def test_invalid_roles_are_rejected(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    for bad_role in ("system", "admin", "root", ""):
        resp = client.post(f"/api/conversations/{conv_id}/messages", json={"role": bad_role, "content": "x"})
        assert resp.status_code == 400, f"role={bad_role!r} should have been rejected"


def test_empty_content_is_rejected(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    resp = client.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": ""})
    assert resp.status_code == 400

    resp2 = client.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": "   "})
    assert resp2.status_code == 400


def test_oversized_content_is_rejected(authed_client):
    from models import MAX_MESSAGE_CONTENT_LENGTH
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    resp = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"role": "user", "content": "x" * (MAX_MESSAGE_CONTENT_LENGTH + 1)},
    )
    assert resp.status_code == 400


def test_non_string_content_is_rejected(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    resp = client.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": 12345})
    assert resp.status_code == 400


def test_adding_message_to_another_users_conversation_is_rejected(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    conv_id = client_a.post("/api/conversations", json={"title": "Alice's"}).get_json()["data"]["id"]

    resp = client_b.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": "Intruder"})
    assert resp.status_code == 404


# =================================================================
# 13-15. delete conversation
# =================================================================

def test_user_can_delete_own_conversation(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    resp = client.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 200

    get_resp = client.get(f"/api/conversations/{conv_id}")
    assert get_resp.status_code == 404


def test_user_cannot_delete_another_users_conversation(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    conv_id = client_a.post("/api/conversations", json={"title": "Alice's"}).get_json()["data"]["id"]

    resp = client_b.delete(f"/api/conversations/{conv_id}")
    assert resp.status_code == 404

    # Untouched - still retrievable by its real owner.
    assert client_a.get(f"/api/conversations/{conv_id}").status_code == 200


def test_deleting_conversation_removes_its_messages_no_orphans(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]
    client.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": "one"})
    client.post(f"/api/conversations/{conv_id}/messages", json={"role": "assistant", "content": "two"})

    client.delete(f"/api/conversations/{conv_id}")

    # Direct ORM access needs an application context of its own - the
    # Flask test client pushes/pops one per request, but that context is
    # already gone by the time control returns here (see
    # client.delete()'s completed response above); it is not something
    # this test can or should borrow from a prior request.
    from models import Message
    with client.application.app_context():
        remaining = Message.query.filter_by(conversation_id=conv_id).all()
    assert remaining == []


# =================================================================
# 16-17. pagination, deterministic ordering
# =================================================================

def test_conversation_listing_pagination_works(authed_client):
    client, user_id = authed_client
    for i in range(5):
        client.post("/api/conversations", json={"title": f"Conv {i}"})

    resp = client.get("/api/conversations?page=1&per_page=2")
    body = resp.get_json()
    assert len(body["data"]) == 2
    assert body["pagination"] == {"page": 1, "per_page": 2, "total": 5, "pages": 3}

    resp2 = client.get("/api/conversations?page=3&per_page=2")
    assert len(resp2.get_json()["data"]) == 1


def test_conversations_ordered_newest_updated_first(authed_client):
    client, user_id = authed_client
    first_id = client.post("/api/conversations", json={"title": "First"}).get_json()["data"]["id"]
    second_id = client.post("/api/conversations", json={"title": "Second"}).get_json()["data"]["id"]

    # Touch the first conversation again (adding a message bumps
    # updated_at) so it should now sort ahead of the second.
    client.post(f"/api/conversations/{first_id}/messages", json={"role": "user", "content": "bump"})

    resp = client.get("/api/conversations")
    ids_in_order = [c["id"] for c in resp.get_json()["data"]]
    assert ids_in_order[0] == first_id


def test_add_message_bumps_conversation_updated_at(history_app):
    """Service-level, no HTTP: directly proves add_message() advances
    the persisted (not just in-memory) updated_at - the exact guarantee
    that was silently broken before (see conversations_service.py's
    add_message() docstring for the root cause and fix). Compares two
    real, independently-computed timestamps rather than sleeping and
    hoping enough time passed."""
    import conversations_service as cs
    from database import db
    from models import Conversation, User

    with history_app.app_context():
        user = User(name="T", email="bump@example.com", password_hash="x", email_verified=True)
        db.session.add(user)
        db.session.commit()

        conversation = cs.create_conversation(user.id, title="Test")
        original_updated_at = conversation.updated_at

        cs.add_message(user.id, conversation.id, "user", "hello")

        # Re-fetch from the database (not the same in-memory object) -
        # this is what actually proves persistence, not just an
        # in-memory attribute mutation.
        reloaded = Conversation.query.filter_by(id=conversation.id).first()
        assert reloaded.updated_at > original_updated_at


def test_conversation_listing_uses_id_desc_tiebreak_for_equal_updated_at(history_app):
    """Isolates the tiebreak rule itself from any timing dependency: two
    conversations are given the EXACT SAME updated_at directly (not
    "close enough" via real elapsed time), so the only thing that can
    determine their relative order is the id DESC tiebreak - proving
    that rule works correctly on its own, independent of whether two
    real-world updates ever happen to land on identical timestamps in
    practice."""
    import conversations_service as cs
    from database import db
    from models import Conversation, User

    with history_app.app_context():
        user = User(name="T", email="tiebreak@example.com", password_hash="x", email_verified=True)
        db.session.add(user)
        db.session.commit()

        conv_low_id = cs.create_conversation(user.id, title="Lower id")
        conv_high_id = cs.create_conversation(user.id, title="Higher id")
        assert conv_low_id.id < conv_high_id.id

        same_timestamp = conv_high_id.updated_at
        Conversation.query.filter_by(id=conv_low_id.id).update({"updated_at": same_timestamp})
        db.session.commit()

        # Confirm the precondition: both rows now have the identical
        # updated_at value (not merely "close").
        reloaded_low = Conversation.query.filter_by(id=conv_low_id.id).first()
        reloaded_high = Conversation.query.filter_by(id=conv_high_id.id).first()
        assert reloaded_low.updated_at == reloaded_high.updated_at

        result = cs.list_conversations(user.id)
        ids_in_order = [c["id"] for c in result["items"]]
        assert ids_in_order == [conv_high_id.id, conv_low_id.id]


def test_message_listing_pagination_and_ordering(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]
    for i in range(5):
        client.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": f"msg {i}"})

    resp = client.get(f"/api/conversations/{conv_id}/messages?page=1&per_page=2")
    body = resp.get_json()
    assert [m["content"] for m in body["data"]] == ["msg 0", "msg 1"]
    assert body["pagination"]["total"] == 5

    resp2 = client.get(f"/api/conversations/{conv_id}/messages?page=2&per_page=2")
    assert [m["content"] for m in resp2.get_json()["data"]] == ["msg 2", "msg 3"]


def test_invalid_pagination_params_are_rejected(authed_client):
    client, user_id = authed_client
    assert client.get("/api/conversations?page=0").status_code == 400
    assert client.get("/api/conversations?page=-1").status_code == 400
    assert client.get("/api/conversations?per_page=0").status_code == 400
    assert client.get("/api/conversations?per_page=abc").status_code == 400


def test_per_page_above_maximum_is_rejected(authed_client):
    from conversations_service import MAX_CONVERSATIONS_PER_PAGE
    client, user_id = authed_client
    resp = client.get(f"/api/conversations?per_page={MAX_CONVERSATIONS_PER_PAGE + 1}")
    assert resp.status_code == 400


# =================================================================
# 18-19. metadata validation
# =================================================================

def test_valid_metadata_is_stored_and_returned(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    metadata = {"retrieval_mode": "hybrid", "policy_ids": [12, 45, 78]}
    resp = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"role": "assistant", "content": "Here's what I found.", "metadata": metadata},
    )
    assert resp.status_code == 201
    assert resp.get_json()["data"]["metadata"] == metadata


def test_malformed_metadata_is_rejected(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    # Not a dict/object.
    resp = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"role": "assistant", "content": "x", "metadata": "just a string"},
    )
    assert resp.status_code == 400

    resp2 = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"role": "assistant", "content": "x", "metadata": [1, 2, 3]},
    )
    assert resp2.status_code == 400


def test_oversized_metadata_is_rejected(authed_client):
    from conversations_service import MAX_METADATA_SERIALIZED_LENGTH
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    huge_metadata = {"policy_ids": list(range(MAX_METADATA_SERIALIZED_LENGTH))}
    resp = client.post(
        f"/api/conversations/{conv_id}/messages",
        json={"role": "assistant", "content": "x", "metadata": huge_metadata},
    )
    assert resp.status_code == 400


def test_missing_request_body_is_rejected_not_500(authed_client):
    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    resp = client.post(f"/api/conversations/{conv_id}/messages", data="not json", content_type="text/plain")
    assert resp.status_code == 400


# =================================================================
# 20. database errors do not leak internals
# =================================================================

def test_conversation_not_found_error_never_leaks_internal_details(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/conversations/999999")
    body = resp.get_json()
    assert set(body.keys()) == {"error"}
    assert "sql" not in body["error"].lower()
    assert "traceback" not in body["error"].lower()


def test_invalid_conversation_id_format_is_rejected_cleanly(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/conversations/not-a-number")
    assert resp.status_code == 400
    assert set(resp.get_json().keys()) == {"error"}


# =================================================================
# 21-24. /chat integration
# =================================================================

def test_existing_chat_behavior_unchanged_without_conversation_id(chatbot_module, monkeypatch):
    """No `conversation_id` in the body - the entire pre-Phase-12
    contract - must be byte-for-byte unchanged: stateless, no auth
    required, no persistence attempted."""
    import app as app_module
    client = app_module.app.test_client()

    resp = client.post("/chat", json={"message": "ISRO Formation Policy 1969"})
    assert resp.status_code == 200
    assert "ISRO Formation Policy, 1969" in resp.get_json()["reply"]


def test_chat_with_conversation_id_requires_authentication():
    import app as app_module
    client = app_module.app.test_client()

    resp = client.post("/chat", json={"message": "hello", "conversation_id": 1})
    assert resp.status_code == 401


def test_chat_with_conversation_id_requires_ownership(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    conv_id = client_a.post("/api/conversations", json={"title": "Alice's"}).get_json()["data"]["id"]

    resp = client_b.post("/chat", json={"message": "hello", "conversation_id": conv_id})
    assert resp.status_code == 404


def test_authenticated_chat_persists_user_message(two_users_and_clients, monkeypatch):
    client_a, user_a, client_b, user_b = two_users_and_clients
    conv_id = client_a.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    import app as app_module
    monkeypatch.setattr(app_module, "get_response", lambda text: "A reply.")

    resp = client_a.post("/chat", json={"message": "What changed in agriculture?", "conversation_id": conv_id})
    assert resp.status_code == 200

    messages = client_a.get(f"/api/conversations/{conv_id}/messages").get_json()["data"]
    user_messages = [m for m in messages if m["role"] == "user"]
    assert any(m["content"] == "What changed in agriculture?" for m in user_messages)


def test_successful_assistant_response_is_persisted(two_users_and_clients, monkeypatch):
    client_a, user_a, client_b, user_b = two_users_and_clients
    conv_id = client_a.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    import app as app_module
    monkeypatch.setattr(app_module, "get_response", lambda text: "The grounded reply.")

    client_a.post("/chat", json={"message": "hello", "conversation_id": conv_id})

    messages = client_a.get(f"/api/conversations/{conv_id}/messages").get_json()["data"]
    assistant_messages = [m for m in messages if m["role"] == "assistant"]
    assert any(m["content"] == "The grounded reply." for m in assistant_messages)


def test_failed_gemini_response_does_not_create_fake_assistant_content(two_users_and_clients, monkeypatch):
    """get_response() never raises (every internal failure already
    degrades to a safe fallback string - see chatbot.get_grounded_response()'s
    docstring), so whatever it returns - including its own honest
    fallback text - is exactly what gets persisted; this test confirms
    persistence never invents or substitutes different content."""
    client_a, user_a, client_b, user_b = two_users_and_clients
    conv_id = client_a.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    import app as app_module
    monkeypatch.setattr(
        app_module, "get_response",
        lambda text: "The available policy dataset does not contain enough relevant information to answer this question.",
    )

    client_a.post("/chat", json={"message": "nonsense query", "conversation_id": conv_id})

    messages = client_a.get(f"/api/conversations/{conv_id}/messages").get_json()["data"]
    assistant_messages = [m for m in messages if m["role"] == "assistant"]
    assert len(assistant_messages) == 1
    assert "does not contain enough relevant information" in assistant_messages[0]["content"]


def test_chat_persistence_failure_does_not_break_the_reply(two_users_and_clients, monkeypatch):
    """A DB error while persisting must never prevent the actual chat
    reply from being returned to the user."""
    client_a, user_a, client_b, user_b = two_users_and_clients
    conv_id = client_a.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]

    import app as app_module
    monkeypatch.setattr(app_module, "get_response", lambda text: "A perfectly good reply.")

    def _boom(*a, **k):
        raise RuntimeError("simulated database failure")
    import conversations_service
    monkeypatch.setattr(conversations_service, "add_message", _boom)

    resp = client_a.post("/chat", json={"message": "hello", "conversation_id": conv_id})
    assert resp.status_code == 200
    assert resp.get_json()["reply"] == "A perfectly good reply."


# =================================================================
# 25. policy JSON remains untouched
# =================================================================

def test_policy_json_files_are_never_modified_by_chat_history(authed_client):
    import hashlib
    import os

    data_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    before = {}
    for fname in os.listdir(data_dir):
        if fname.endswith(".json"):
            with open(os.path.join(data_dir, fname), "rb") as f:
                before[fname] = hashlib.sha256(f.read()).hexdigest()

    client, user_id = authed_client
    conv_id = client.post("/api/conversations", json={"title": "Mine"}).get_json()["data"]["id"]
    client.post(f"/api/conversations/{conv_id}/messages", json={"role": "user", "content": "hello"})
    client.delete(f"/api/conversations/{conv_id}")

    after = {}
    for fname in os.listdir(data_dir):
        if fname.endswith(".json"):
            with open(os.path.join(data_dir, fname), "rb") as f:
                after[fname] = hashlib.sha256(f.read()).hexdigest()

    assert before == after


# =================================================================
# title generation (unit-level, no HTTP)
# =================================================================

def test_generate_title_from_message_truncates_long_messages():
    from conversations_service import generate_title_from_message
    from models import MAX_TITLE_LENGTH

    long_message = "word " * 100
    title = generate_title_from_message(long_message)
    assert len(title) <= MAX_TITLE_LENGTH
    assert title.endswith("...")


def test_generate_title_from_message_handles_empty_input():
    from conversations_service import generate_title_from_message, DEFAULT_TITLE
    assert generate_title_from_message("") == DEFAULT_TITLE
    assert generate_title_from_message("   ") == DEFAULT_TITLE
    assert generate_title_from_message(None) == DEFAULT_TITLE
