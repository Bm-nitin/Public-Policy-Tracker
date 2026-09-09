"""
Area 2: Category detection (backend/chatbot.py: detect_category,
get_category_policies)

Covers all 15 currently supported categories. Inputs below were verified
against the running code, not guessed, because several CATEGORY_KEYWORDS
lists overlap (e.g. "banking" and "economy" both list "bank"/"banking"/
"financial"/"finance") - detect_category() picks the first category (by
dict insertion order) with the highest keyword-overlap score, so a naive
one-word-per-category test can silently pick the wrong word. Where a
category has no word that uniquely identifies it, that is itself
documented below as a characterization finding rather than papered over.
"""

import pytest

CATEGORY_TRIGGER_WORDS = {
    "education": "education",
    "healthcare": "healthcare",
    "agriculture": "agriculture",
    "technology": "digital",  # "technology" alone ties with science_innovation
    "environment": "environment",
    "economy": "economy",
    "employment_skill": "employment",
    "social_welfare": "welfare",
    "infrastructure_transport": "roads",
    "industry_business": "startups",
    "energy": "solar",
    "governance_legal": "governance",
    "science_innovation": "research",
    "culture_tourism": "tourism",
    # "banking" and "bank" both tie with "economy" (see
    # test_banking_singular_ties_with_economy_by_dict_order below);
    # "banks" (plural) is the only banking-keyword not also listed under
    # economy, so it's the only single word that reaches "banking".
    "banking": "banks",
}


@pytest.mark.parametrize("expected_category,trigger_word", list(CATEGORY_TRIGGER_WORDS.items()))
def test_detect_category_for_each_supported_sector(chatbot_module, expected_category, trigger_word):
    assert chatbot_module.detect_category(trigger_word) == expected_category


@pytest.mark.parametrize("expected_category,trigger_word", list(CATEGORY_TRIGGER_WORDS.items()))
def test_get_category_policies_returns_matching_sector_only(chatbot_module, expected_category, trigger_word):
    policies = chatbot_module.get_category_policies(expected_category)
    assert len(policies) > 0
    for policy in policies:
        assert policy["sector"] == expected_category


def test_detect_category_no_keywords_returns_none(chatbot_module):
    assert chatbot_module.detect_category("zzz qqq xyz nonsense") is None


def test_detect_category_empty_string_returns_none(chatbot_module):
    assert chatbot_module.detect_category("") is None


def test_banking_singular_ties_with_economy_by_dict_order(chatbot_module):
    """Documented (not fixed) quirk: CATEGORY_KEYWORDS['economy'] and
    CATEGORY_KEYWORDS['banking'] both contain 'bank' and 'banking'. Because
    'economy' is defined earlier in the dict and detect_category() breaks
    ties by taking the first max-scoring category, single-word queries like
    'banking' or 'bank' currently route to the 'economy' category, never to
    'banking'. This test locks in that actual behavior so it doesn't change
    silently in a future refactor; it is not an assertion that this is the
    intended behavior.
    """
    assert chatbot_module.detect_category("banking") == "economy"
    assert chatbot_module.detect_category("bank") == "economy"


def test_all_fifteen_categories_have_at_least_one_policy(chatbot_module):
    for category in CATEGORY_TRIGGER_WORDS:
        assert len(chatbot_module.get_category_policies(category)) > 0, (
            f"No policies found for category '{category}'"
        )
