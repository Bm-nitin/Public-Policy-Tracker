"""
Area 1: Text utilities (backend/utils.py)

Pure functions, fully deterministic, no I/O - straightforward characterization
of current behavior.
"""


def test_clean_text_lowercases_and_strips_punctuation(utils_module):
    assert utils_module.clean_text("Hello, World! 123") == "hello world 123"


def test_clean_text_collapses_repeated_whitespace(utils_module):
    assert utils_module.clean_text("a    b\tc\n d") == "a b c d"


def test_clean_text_strips_leading_trailing_space(utils_module):
    assert utils_module.clean_text("   education policy   ") == "education policy"


def test_clean_text_non_string_input_returns_empty_string(utils_module):
    assert utils_module.clean_text(None) == ""
    assert utils_module.clean_text(12345) == ""
    assert utils_module.clean_text(["a", "b"]) == ""


def test_clean_text_empty_string_returns_empty_string(utils_module):
    assert utils_module.clean_text("") == ""


def test_similarity_score_identical_strings_is_1(utils_module):
    assert utils_module.similarity_score(
        "National Education Policy", "National Education Policy"
    ) == 1.0


def test_similarity_score_is_case_and_punctuation_insensitive(utils_module):
    # similarity_score cleans both inputs first, so casing/punctuation
    # differences alone should not lower the score.
    assert utils_module.similarity_score(
        "National Education Policy!", "national education policy"
    ) == 1.0


def test_similarity_score_empty_input_returns_zero(utils_module):
    assert utils_module.similarity_score("", "something") == 0
    assert utils_module.similarity_score("something", "") == 0
    assert utils_module.similarity_score("", "") == 0


def test_similarity_score_partial_match_is_between_zero_and_one(utils_module):
    score = utils_module.similarity_score(
        "National Education Policy", "national eduction plicy"
    )
    assert 0 < score < 1


def test_similarity_score_completely_different_strings_is_low(utils_module):
    score = utils_module.similarity_score(
        "National Education Policy, 2020", "zzz qqq xyz 999"
    )
    assert score < 0.3


def test_extract_keywords_removes_known_stopwords(utils_module):
    keywords = utils_module.extract_keywords(
        "What is the latest education policy change please"
    )
    for stopword in ("what", "is", "the", "latest", "policy", "policies",
                      "change", "please"):
        assert stopword not in keywords


def test_extract_keywords_keeps_meaningful_words(utils_module):
    keywords = utils_module.extract_keywords(
        "What is the latest education policy change please"
    )
    assert "education" in keywords


def test_extract_keywords_drops_words_of_length_2_or_less(utils_module):
    keywords = utils_module.extract_keywords("is it ok go up")
    # "it", "ok", "go", "up" are all length <= 2 and should be dropped;
    # "is" is also a stopword.
    assert keywords == []


def test_extract_keywords_empty_input_returns_empty_list(utils_module):
    assert utils_module.extract_keywords("") == []


def test_match_by_keywords_finds_best_scoring_policy(utils_module):
    policies = [
        {
            "name": "Alpha Health Scheme",
            "change": "improves rural clinics",
            "impact": "better care",
            "category": "",
            "sub_category": "",
            "sector": "healthcare",
        },
        {
            "name": "Beta Farming Act",
            "change": "supports crop insurance",
            "impact": "helps farmers directly",
            "category": "",
            "sub_category": "",
            "sector": "agriculture",
        },
    ]
    match, score = utils_module.match_by_keywords(
        "Tell me about farmers crop insurance", policies
    )
    assert match["name"] == "Beta Farming Act"
    assert score >= 0.30


def test_match_by_keywords_below_threshold_returns_none(utils_module):
    policies = [
        {
            "name": "Alpha Health Scheme",
            "change": "improves rural clinics",
            "impact": "better care",
            "category": "",
            "sub_category": "",
            "sector": "healthcare",
        }
    ]
    match, score = utils_module.match_by_keywords(
        "completely unrelated gibberish zzz qqq", policies
    )
    assert match is None
    assert score == 0


def test_match_by_keywords_no_extractable_keywords_returns_none(utils_module):
    # "is", "the", "of" are all stopwords -> extract_keywords returns []
    match, score = utils_module.match_by_keywords("is the of", [])
    assert match is None
    assert score == 0


def test_match_by_keywords_empty_policy_list_returns_none(utils_module):
    match, score = utils_module.match_by_keywords("farmers crop insurance", [])
    assert match is None
    assert score == 0
