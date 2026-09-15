"""
Phase 9: backend/retrieval.py tests.

Uses populated_db_app (see conftest.py) - an isolated in-memory database
pre-populated with the REAL 151-record dataset via the actual import
pipeline, so these tests exercise real data end to end, not synthetic
fixtures. See conftest.py's flask_sqlalchemy shim docstring for what is/
isn't validated in this offline sandbox vs. a real environment with
flask-sqlalchemy + PostgreSQL installed - Phase 9 additionally extends
that shim with real parameterized ilike()/or_()/filter() support (see
conftest.py's _ShimBinaryExpression), needed for genuinely bounded,
parameterized candidate retrieval.
"""

from retrieval import normalize_query, retrieve_policies


# --- query normalization -----------------------------------------------------

def test_normalize_query_lowercases():
    assert normalize_query("EDUCATION Policy") == "education policy"


def test_normalize_query_collapses_whitespace():
    assert normalize_query("education    policy\t\tchanges") == "education policy changes"


def test_normalize_query_strips_punctuation():
    # clean_text() replaces punctuation with a space (not deletion), so
    # "What's" becomes "what s" (two tokens), not "whats" (one token) -
    # this matches clean_text's documented, already-characterized
    # behavior (see test_utils.py), not a new assumption invented here.
    assert normalize_query("What's changed in agriculture?!") == "what s changed in agriculture"


def test_normalize_query_empty_string():
    assert normalize_query("") == ""


def test_normalize_query_whitespace_only():
    assert normalize_query("     ") == ""


# --- exact / partial name matching -------------------------------------------

def test_exact_policy_name_match_ranks_first(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("ISRO Formation Policy 1969")
    assert len(results) >= 1
    assert results[0]["name"] == "ISRO Formation Policy, 1969"


def test_partial_policy_name_match_finds_the_policy(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("ISRO Formation")
    names = [r["name"] for r in results]
    assert "ISRO Formation Policy, 1969" in names


def test_case_insensitive_name_matching(populated_db_app):
    with populated_db_app.app_context():
        lower = retrieve_policies("isro formation policy")
        upper = retrieve_policies("ISRO FORMATION POLICY")
    assert [r["name"] for r in lower] == [r["name"] for r in upper]
    assert lower[0]["name"] == "ISRO Formation Policy, 1969"


def test_whitespace_normalization_does_not_change_result(populated_db_app):
    with populated_db_app.app_context():
        tight = retrieve_policies("ISRO Formation Policy")
        loose = retrieve_policies("  ISRO    Formation     Policy  ")
    assert [r["name"] for r in tight] == [r["name"] for r in loose]


def test_punctuation_normalization_does_not_change_result(populated_db_app):
    with populated_db_app.app_context():
        plain = retrieve_policies("ISRO Formation Policy 1969")
        punctuated = retrieve_policies("ISRO, Formation... Policy?! 1969")
    assert [r["name"] for r in plain] == [r["name"] for r in punctuated]


# --- sector / category / sub_category matching --------------------------------

def test_sector_matching_surfaces_policies_from_that_sector(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("agriculture")
    assert len(results) > 0
    assert any(r["sector"] == "agriculture" for r in results)


def test_sector_with_underscore_matches_on_either_word(populated_db_app):
    """sector 'employment_skill' should match a query containing just
    'employment'."""
    with populated_db_app.app_context():
        results = retrieve_policies("employment skill development policies")
    assert len(results) > 0
    assert any(r["sector"] == "employment_skill" for r in results)


def test_category_matching(populated_db_app):
    with populated_db_app.app_context():
        from models import Policy
        sample_category = Policy.query.first().category
        results = retrieve_policies(sample_category)
    assert len(results) > 0


def test_sub_category_matching(populated_db_app):
    with populated_db_app.app_context():
        from models import Policy
        sample_sub_category = Policy.query.first().sub_category
        results = retrieve_policies(sample_sub_category)
    assert len(results) > 0


# --- change / impact keyword matching -----------------------------------------

def test_change_keyword_matching(populated_db_app):
    with populated_db_app.app_context():
        # PMGSY's change text mentions "all-weather road connectivity" -
        # verified in earlier phases' characterization tests.
        results = retrieve_policies("all weather road connectivity")
    names = [r["name"] for r in results]
    assert any("Gram Sadak" in n for n in names)


def test_impact_keyword_matching(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("reduces out of pocket expenses hospital")
    assert len(results) > 0


# --- broad / irrelevant / no-match queries --------------------------------------

def test_broad_query_returns_relevant_results(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("what changed in agriculture policies")
    assert len(results) > 0
    assert any(r["sector"] == "agriculture" for r in results)


def test_irrelevant_query_returns_no_results(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("purple elephants dancing on the moon")
    assert results == []


def test_no_match_query_returns_empty_list_not_arbitrary_policies(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("xyzabc123 nonsense gibberish query")
    assert results == []


# --- empty / whitespace-only query --------------------------------------------

def test_empty_query_returns_empty_list(populated_db_app):
    with populated_db_app.app_context():
        assert retrieve_policies("") == []


def test_whitespace_only_query_returns_empty_list(populated_db_app):
    with populated_db_app.app_context():
        assert retrieve_policies("     ") == []


def test_stopword_only_query_returns_empty_list(populated_db_app):
    """"what is the" contains no meaningful (non-stopword) tokens - must
    not fall through to an unbounded/unfiltered fetch."""
    with populated_db_app.app_context():
        assert retrieve_policies("what is the") == []


# --- result limits, deterministic ordering, tie-breaking -----------------------

def test_result_limit_is_enforced(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("policy scheme government change impact", limit=3)
    assert len(results) <= 3


def test_default_result_limit(populated_db_app):
    from retrieval import MAX_RESULTS
    with populated_db_app.app_context():
        results = retrieve_policies("policy government scheme")
    assert len(results) <= MAX_RESULTS


def test_results_are_ordered_by_score_descending(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("agriculture farmers crop insurance scheme")
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_repeated_calls_return_identical_deterministic_ordering(populated_db_app):
    with populated_db_app.app_context():
        first = retrieve_policies("education policy scheme")
        second = retrieve_policies("education policy scheme")
    assert [r["id"] for r in first] == [r["id"] for r in second]


def test_tie_breaking_is_stable_across_repeated_calls(populated_db_app):
    """Even for a broad query likely to produce score ties among several
    candidates, the final ordering must be identical every time (name
    then id tie-break), not just "some" stable subset."""
    with populated_db_app.app_context():
        runs = [retrieve_policies("government") for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


# --- ranking quality -----------------------------------------------------------

def test_exact_name_outranks_generic_keyword_match(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("ISRO Formation Policy, 1969")
    assert results[0]["name"] == "ISRO Formation Policy, 1969"
    if len(results) > 1:
        assert results[0]["score"] > results[1]["score"]


def test_sector_match_outranks_unrelated_keyword_only_match(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("banking")
    assert len(results) > 0
    top_sectors = [r["sector"] for r in results[:3]]
    assert "banking" in top_sectors


# --- bounded retrieval ----------------------------------------------------------

def test_get_candidate_policies_with_empty_tokens_returns_empty_without_querying(populated_db_app):
    from retrieval import get_candidate_policies
    with populated_db_app.app_context():
        assert get_candidate_policies(set()) == []


# --- example queries from the Phase 9 brief -------------------------------------

def test_example_query_agriculture_changes(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("What changed in agriculture policies?")
    assert len(results) > 0
    assert any(r["sector"] == "agriculture" for r in results)


def test_example_query_education_changes(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("latest education policy changes")
    assert len(results) > 0
    assert any(r["sector"] == "education" for r in results)


def test_example_query_scholarships(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("policies related to scholarships")
    # May legitimately be empty if no policy text mentions "scholarship" -
    # the assertion is "does not error and does not return junk", not
    # "must find something" for every conceivable phrase.
    assert isinstance(results, list)


def test_example_query_farmers_schemes(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("government schemes for farmers")
    assert len(results) > 0


def test_example_query_banking_changes(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("what changed in banking")
    assert len(results) > 0
    assert any(r["sector"] == "banking" for r in results)


def test_example_query_renewable_energy(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("policies affecting renewable energy")
    assert isinstance(results, list)


def test_example_query_employment_skill_development(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("employment skill development policies")
    assert len(results) > 0
    assert any(r["sector"] == "employment_skill" for r in results)


def test_example_query_tell_me_about_education(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("tell me about education")
    assert len(results) > 0
    assert any(r["sector"] == "education" for r in results)


# --- never invents data ---------------------------------------------------------

def test_results_only_contain_real_policies_never_invented(populated_db_app):
    with populated_db_app.app_context():
        from models import Policy
        real_names = {p.name for p in Policy.query.all()}
        results = retrieve_policies("education policy scheme government")
        for r in results:
            assert r["name"] in real_names


def test_result_shape_matches_policy_to_dict_plus_id_and_score(populated_db_app):
    with populated_db_app.app_context():
        results = retrieve_policies("agriculture")
    assert len(results) > 0
    expected_keys = {"name", "category", "sub_category", "change", "impact", "sector", "id", "score"}
    assert set(results[0].keys()) == expected_keys


# --- chat integration (chatbot.py) ----------------------------------------------

def test_chat_uses_retrieval_v2_when_database_configured(populated_db_app, monkeypatch):
    """Forces category/name-similarity/keyword-match steps to fail (same
    isolation technique as the existing Phase 0.5 Gemini-fallback test),
    then confirms get_response() uses Retrieval V2's DB-backed result
    instead of falling through to Gemini, when a database IS configured."""
    import chatbot as chatbot_module
    import config

    monkeypatch.setattr(config.Config, "DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(chatbot_module, "detect_category", lambda text: None)
    monkeypatch.setattr(chatbot_module, "match_by_keywords", lambda *a, **k: (None, 0))

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("Gemini should not be called when Retrieval V2 finds a match")
    monkeypatch.setattr(chatbot_module, "call_generative_ai", _fail_if_called)

    with populated_db_app.app_context():
        result = chatbot_module.get_response("ISRO Formation Policy 1969")

    assert "ISRO Formation Policy, 1969" in result


def test_chat_falls_back_to_gemini_when_retrieval_v2_finds_nothing(populated_db_app, monkeypatch):
    import chatbot as chatbot_module
    import config

    monkeypatch.setattr(config.Config, "DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setattr(chatbot_module, "detect_category", lambda text: None)
    monkeypatch.setattr(chatbot_module, "match_by_keywords", lambda *a, **k: (None, 0))
    monkeypatch.setattr(chatbot_module, "call_generative_ai", lambda text: "MOCKED_GEMINI_REPLY")

    with populated_db_app.app_context():
        # Verified empirically: "policy" triggers is_policy_related() (so
        # this reaches the Retrieval V2 / Gemini branch at all, past the
        # off-topic guard) but is itself a stopword extract_keywords()
        # filters out before tokenizing for retrieval, and the remaining
        # tokens match no real policy text - so Retrieval V2 genuinely
        # finds nothing and the code must fall through to Gemini, unlike
        # a query merely containing common words like "insurance" (which
        # legitimately matches real crop/health insurance policies and is
        # correct retrieval, not a bug).
        result = chatbot_module.get_response("policy zzz qqq xyz blorptastic nonexistent")

    assert result == "MOCKED_GEMINI_REPLY"


def test_chat_without_database_configured_falls_back_to_gemini_unchanged(monkeypatch):
    """The exact pre-Phase-9 characterization path, re-confirmed: with no
    database configured, Retrieval V2 is skipped entirely (not even
    attempted) and behavior is byte-identical to before this phase."""
    import chatbot as chatbot_module
    import config

    monkeypatch.setattr(config.Config, "DATABASE_URL", None)
    monkeypatch.setattr(chatbot_module, "detect_category", lambda text: None)
    monkeypatch.setattr(chatbot_module, "match_by_keywords", lambda *a, **k: (None, 0))
    monkeypatch.setattr(chatbot_module, "call_generative_ai", lambda text: "MOCKED_GEMINI_REPLY")

    result = chatbot_module.get_response("insurance zzz qqq clarification xyz needed")
    assert result == "MOCKED_GEMINI_REPLY"
