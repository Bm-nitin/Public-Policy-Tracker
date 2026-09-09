"""
Area 5: Flask API (backend/app.py)

Uses Flask's built-in test client - no real server, no network access.
Expected values (including the 500-on-malformed-JSON behavior below) were
captured directly from the running app, not assumed.
"""


def test_home_route_returns_200_and_text(app_client):
    response = app_client.get("/")
    assert response.status_code == 200
    assert response.data == b"Track Public Policy Chatbot is running"


def test_health_route_returns_ok_status(app_client):
    response = app_client.get("/health")
    assert response.status_code == 200
    assert response.get_json() == {"status": "ok"}


def test_policies_route_returns_full_dataset(app_client):
    response = app_client.get("/policies")
    assert response.status_code == 200
    data = response.get_json()
    assert isinstance(data, list)
    assert len(data) == 151  # 15 sector files, current dataset size

    sample = data[0]
    for field in ("name", "category", "sub_category", "change", "impact", "sector"):
        assert field in sample


def test_chat_valid_request_returns_reply_key(app_client):
    response = app_client.post("/chat", json={"message": "Tell me about education policy"})
    assert response.status_code == 200
    data = response.get_json()
    assert "reply" in data
    assert isinstance(data["reply"], str)
    assert data["reply"].startswith("Showing Education policies:")


def test_chat_missing_message_key_returns_400(app_client):
    response = app_client.post("/chat", json={"foo": "bar"})
    assert response.status_code == 400
    assert response.get_json() == {"reply": "Invalid request"}


def test_chat_empty_message_returns_400(app_client):
    response = app_client.post("/chat", json={"message": "   "})
    assert response.status_code == 400
    assert response.get_json() == {"reply": "Empty message"}


def test_chat_no_json_body_at_all_returns_500(app_client):
    """Characterizes a real bug: request.get_json() raises a werkzeug
    UnsupportedMediaType/BadRequest HTTPException when no JSON body/content
    type is sent. Because that exception is an Exception subclass, app.py's
    broad `except Exception` swallows it and returns the generic 500
    handler instead of a clean 400. This test locks in the CURRENT (500)
    behavior; it is a known issue flagged in CURRENT_ARCHITECTURE.md, not
    something Phase 0.5 is fixing."""
    response = app_client.post("/chat")
    assert response.status_code == 500
    assert response.get_json() == {"reply": "Something went wrong"}


def test_chat_malformed_json_body_returns_500(app_client):
    """Same root cause as the test above: invalid JSON in the body raises
    a BadRequest inside the try block, which is caught by the generic
    except and turned into a 500, not a 400."""
    response = app_client.post(
        "/chat", data="not-json{{{", content_type="application/json"
    )
    assert response.status_code == 500
    assert response.get_json() == {"reply": "Something went wrong"}


def test_chat_response_structure_on_success(app_client):
    response = app_client.post("/chat", json={"message": "healthcare policy"})
    data = response.get_json()
    assert set(data.keys()) == {"reply"}
