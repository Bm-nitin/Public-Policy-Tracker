"""
Area 5: Flask API (backend/app.py)

Uses Flask's built-in test client with no network access.
"""

import pytest


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


@pytest.mark.parametrize("body, content_type", [
    ("", None),
    ('{"message": "hello"}', "text/plain"),
    ("not-json{{{", "application/json"),
    ('["message"]', "application/json"),
    ('"message"', "application/json"),
    ("null", "application/json"),
])
def test_chat_invalid_json_request_returns_400(app_client, body, content_type):
    response = app_client.post("/chat", data=body, content_type=content_type)
    assert response.status_code == 400
    assert response.get_json() == {"reply": "Invalid request"}


def test_chat_preserves_body_reader_size_error(app_client, monkeypatch):
    monkeypatch.setitem(app_client.application.config, "MAX_CONTENT_LENGTH", 32)
    reply = app_client.post("/chat", json={"message": "hello" * 20})
    assert reply.status_code == 413
    assert reply.get_json() == {"error": "Request too large"}


def test_chat_response_structure_on_success(app_client):
    response = app_client.post("/chat", json={"message": "healthcare policy"})
    data = response.get_json()
    assert set(data.keys()) == {"reply"}
