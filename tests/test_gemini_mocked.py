"""
Area 6: Gemini integration (backend/chatbot.py: call_generative_ai)

The real Gemini API is NEVER called in this file. `mock_gemini_client`
(see conftest.py) replaces `chatbot.client` with a fake object whose
`.models.generate_content(...)` either returns a canned response or raises
a given exception - no network access required, fully deterministic.
"""


def test_gemini_successful_response_is_cleaned_and_returned(chatbot_module, mock_gemini_client):
    mock_gemini_client(reply_text="Here is a policy answer.:contentReference[oaicite:0]{index=0}")
    result = chatbot_module.call_generative_ai("What is the impact of policy X?")
    assert result == "Here is a policy answer."


def test_gemini_empty_text_response_returns_fallback_message(chatbot_module, mock_gemini_client):
    mock_gemini_client(reply_text="")
    result = chatbot_module.call_generative_ai("some question")
    assert result == (
        "I could not generate a response. "
        "Please ask a public-policy-related question."
    )


def test_gemini_none_text_response_returns_fallback_message(chatbot_module, mock_gemini_client):
    mock_gemini_client(reply_text=None)
    result = chatbot_module.call_generative_ai("some question")
    assert result == (
        "I could not generate a response. "
        "Please ask a public-policy-related question."
    )


def test_gemini_raises_exception_returns_unavailable_message(chatbot_module, mock_gemini_client):
    mock_gemini_client(raise_exc=RuntimeError("simulated network failure"))
    result = chatbot_module.call_generative_ai("some question")
    assert result == (
        "AI assistance is currently unavailable. "
        "Please try a policy-related question from the available sectors."
    )


def test_gemini_missing_api_key_returns_unavailable_message_without_calling_client(
    chatbot_module, monkeypatch
):
    monkeypatch.setattr(chatbot_module, "API_KEY", None)
    monkeypatch.setattr(chatbot_module, "client", None)
    result = chatbot_module.call_generative_ai("some question")
    assert result == (
        "AI assistance is currently unavailable. "
        "Please try a policy-related question from the available sectors."
    )


def test_clean_ai_response_strips_content_reference_artifacts(chatbot_module):
    raw = "This is the answer.:contentReference[oaicite:0]{index=0}\n\n\nMore text here."
    assert chatbot_module.clean_ai_response(raw) == (
        "This is the answer.\n\nMore text here."
    )


def test_clean_ai_response_empty_input_returns_empty_string(chatbot_module):
    assert chatbot_module.clean_ai_response("") == ""
    assert chatbot_module.clean_ai_response(None) == ""
