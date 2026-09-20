"""
Phase 11 - Gemini API interaction ONLY.

Deliberately narrow: this module knows how to send a system instruction
plus content to Gemini and get text back (or raise GeminiServiceError).
It does not know what a "policy" is, does not build context, does not
decide what to do on failure - those are backend/grounding.py's and
backend/chatbot.py's jobs respectively (see backend/chatbot.py's
get_grounded_response() for the orchestration that ties all three
together). This mirrors the existing separation backend/embeddings.py
already established for the embedding side of the Gemini API in
Phase 10 - this module is that same pattern for grounded chat
generation.

Reuses the existing google-genai dependency and Config.GEMINI_API_KEY /
Config.GEMINI_MODEL - no second provider, no second API key. This is
intentionally a SEPARATE client/function from backend/chatbot.py's
existing (Phase 0.5) call_generative_ai(), which remains untouched and
still does its own thing (an ungrounded, general-knowledge policy-topic
answer, with no retrieved context and no system_instruction) - see
get_grounded_response()'s docstring for exactly where each is used.
"""

from config import Config


class GeminiServiceError(Exception):
    """Raised for any Gemini API failure (missing API key, network
    error, empty response, SDK error, etc.) - backend/chatbot.py's
    get_grounded_response() catches this specifically and falls back to
    a safe, deterministic response built from the retrieved policy
    records, never letting a Gemini-layer problem crash or hang the
    chatbot. Never carries the raw underlying exception's message
    forward to a caller that might surface it to an end user - see
    generate_grounded_response()'s docstring."""


def is_available():
    """True if Gemini is configured at all (an API key is present) -
    does not confirm the key is valid or the network is reachable, only
    that it's worth attempting. Callers should still handle
    GeminiServiceError from generate_grounded_response() regardless of
    this check (a configured key can still fail at request time) - this
    is only useful for fast-pathing the fully-offline case (see
    backend/chatbot.py's get_grounded_response())."""
    return bool(Config.GEMINI_API_KEY)


def generate_grounded_response(user_query, context, system_instruction):
    """Sends `system_instruction` via the Gemini API's dedicated
    system_instruction parameter (see backend/grounding.py's
    GROUNDING_SYSTEM_INSTRUCTION docstring for why this - not string
    concatenation - is what makes it a real privilege boundary) and
    `context` + `user_query` together as plain content (both are
    DATA to the model: retrieved policy text and the end user's own
    question, on equal footing - neither is treated as more trusted than
    the other, and neither can rewrite `system_instruction`).

    Returns the response text (str). Raises GeminiServiceError - never
    lets google-genai's own exception types, a missing API key, or a
    malformed/empty response propagate as something a caller has to know
    google-genai's internals to handle, and never includes the raw
    underlying exception's text in the message it raises (that could
    contain request/response internals) - callers that log this
    exception get a clean, fixed message, not provider internals; the
    real underlying error is only ever printed server-side (see
    backend/chatbot.py's existing convention for this, e.g.
    call_generative_ai()'s own `print("[AI ERROR]", ...)`).
    """
    if not Config.GEMINI_API_KEY:
        raise GeminiServiceError("Gemini is not configured.")
    if not user_query or not user_query.strip():
        raise GeminiServiceError("Cannot generate a grounded response for an empty query.")

    contents = f"POLICY CONTEXT:\n{context}\n\nUSER QUESTION:\n{user_query}"

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=Config.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=Config.GEMINI_MODEL,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
            ),
        )
    except GeminiServiceError:
        raise
    except Exception as e:  # noqa: BLE001 - any provider/SDK/network failure degrades the same way
        # Server-side only - never forwarded to the caller (see docstring).
        print("[GEMINI SERVICE ERROR]", str(e))
        raise GeminiServiceError("Gemini request failed.") from e

    text = getattr(response, "text", None)
    if not text or not text.strip():
        raise GeminiServiceError("Gemini returned an empty response.")

    return text
