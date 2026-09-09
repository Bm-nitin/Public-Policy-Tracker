"""
Area 4: End-to-end chatbot behavior (backend/chatbot.py: get_response)

get_response() runs a 5-step waterfall:
  1. category match
  2. direct policy-name similarity match (>= 0.65)
  3. keyword match (>= 0.30)
  4. off-topic guard (is_policy_related)
  5. Gemini fallback

Each test below targets one step. Expected strings were captured directly
from the running application (not guessed) so these are true
characterization tests - see the Phase 0.5 report for how each fixture
query was chosen.
"""


def test_category_query_returns_up_to_three_policies_from_that_sector(chatbot_module):
    result = chatbot_module.get_response("Tell me about education policy")
    assert result.startswith("Showing Education policies:")
    assert "National Education Policy, 2020" in result
    # format_multiple caps at 3 policies
    assert result.count("•") <= 3


def test_direct_policy_name_query_returns_formatted_single_policy(chatbot_module):
    # "Ayushman Bharat - Pradhan Mantri Jan Arogya Yojana, 2018" contains no
    # CATEGORY_KEYWORDS word, so this genuinely exercises step 2 (name
    # similarity), not step 1 (category match).
    query = "Ayushman Bharat Pradhan Mantri Jan Arogya Yojana 2018"
    assert chatbot_module.detect_category(query) is None  # sanity check on step 1

    result = chatbot_module.get_response(query)
    assert result.startswith("Policy: Ayushman Bharat - Pradhan Mantri Jan Arogya Yojana, 2018")
    assert "Change:" in result
    assert "Impact:" in result


def test_keyword_match_query_returns_formatted_single_policy(chatbot_module):
    # "what's the weather today" has no category keyword and no close name
    # match, but "weather" overlaps with the PMGSY policy's change text
    # ("all-weather road connectivity"), clearing the 0.30 keyword
    # threshold. This is real, if surprising, current behavior.
    query = "what's the weather today"
    assert chatbot_module.detect_category(query) is None

    result = chatbot_module.get_response(query)
    assert result.startswith("Policy: Pradhan Mantri Gram Sadak Yojana (PMGSY), 2000")


def test_non_policy_query_returns_canned_offtopic_message(chatbot_module):
    result = chatbot_module.get_response("what is your favorite movie")
    assert result == (
        "I am a Public Policy Assistant. "
        "Please ask about government policies, schemes, "
        "laws, regulations, or their impacts."
    )


def test_empty_query_returns_prompt_to_enter_a_question(chatbot_module):
    assert chatbot_module.get_response("") == "Please enter a question."
    assert chatbot_module.get_response("   ") == "Please enter a question."


def test_unmatched_but_policy_related_query_falls_back_to_gemini(chatbot_module, monkeypatch):
    """Isolates step 5 by forcing steps 1-3 to fail, rather than hunting for
    a fragile 'organically unmatched' query - the matching functions
    already have dedicated unit tests above, so this test's job is only to
    confirm get_response() reaches call_generative_ai() when they do."""
    monkeypatch.setattr(chatbot_module, "detect_category", lambda text: None)
    monkeypatch.setattr(chatbot_module, "match_by_keywords", lambda *a, **k: (None, 0))
    monkeypatch.setattr(chatbot_module, "call_generative_ai", lambda text: "MOCKED_GEMINI_REPLY")

    # Contains "insurance", a word that only appears in is_policy_related's
    # policy_keywords set (not in any CATEGORY_KEYWORDS list), keeping the
    # off-topic guard from firing while steps 1-3 are stubbed out above.
    result = chatbot_module.get_response("insurance zzz qqq clarification xyz needed")
    assert result == "MOCKED_GEMINI_REPLY"


def test_format_response_structure(chatbot_module):
    policy = {
        "name": "Test Policy Name",
        "change": "Test change description.",
        "impact": "Test impact description.",
    }
    result = chatbot_module.format_response(policy)
    assert result == (
        "Policy: Test Policy Name\n\n"
        "Change: Test change description.\n\n"
        "Impact: Test impact description."
    )


def test_format_multiple_structure(chatbot_module):
    policies = [
        {"name": "Policy One", "impact": "Impact one."},
        {"name": "Policy Two", "impact": "Impact two."},
    ]
    result = chatbot_module.format_multiple(policies, "social_welfare")
    assert result.startswith("Showing Social Welfare policies:")
    assert "• Policy One" in result
    assert "  → Impact one." in result
    assert "• Policy Two" in result
