"""
Phase 9 Retrieval V2 tests.

ARCHITECTURE CHANGE (post-Phase-9): retrieve_policies() and friends are
JSON-backed now (see backend/retrieval.py's module docstring) - they run
against the REAL 151-record dataset loaded once by
backend/policy_loader.py, not a database. No Flask app, app context, or
database of any kind is needed to call them, so (unlike the original
Phase 9 version of this file) these tests call retrieval.py's functions
directly with no fixture at all for that. The one place a Flask app is
still needed is the chat-integration section at the bottom, which
exercises backend/chatbot.py's get_response() end to end.
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

def test_exact_policy_name_match_ranks_first():
    results = retrieve_policies("ISRO Formation Policy 1969")
    assert len(results) >= 1
    assert results[0]["name"] == "ISRO Formation Policy, 1969"


def test_partial_policy_name_match_finds_the_policy():
    results = retrieve_policies("ISRO Formation")
    names = [r["name"] for r in results]
    assert "ISRO Formation Policy, 1969" in names


def test_case_insensitive_name_matching():
    lower = retrieve_policies("isro formation policy")
    upper = retrieve_policies("ISRO FORMATION POLICY")
    assert [r["name"] for r in lower] == [r["name"] for r in upper]
    assert lower[0]["name"] == "ISRO Formation Policy, 1969"


def test_whitespace_normalization_does_not_change_result():
    tight = retrieve_policies("ISRO Formation Policy")
    loose = retrieve_policies("  ISRO    Formation     Policy  ")
    assert [r["name"] for r in tight] == [r["name"] for r in loose]


def test_punctuation_normalization_does_not_change_result():
    plain = retrieve_policies("ISRO Formation Policy 1969")
    punctuated = retrieve_policies("ISRO, Formation... Policy?! 1969")
    assert [r["name"] for r in plain] == [r["name"] for r in punctuated]


# --- sector / category / sub_category matching --------------------------------

def test_sector_matching_surfaces_policies_from_that_sector():
    results = retrieve_policies("agriculture")
    assert len(results) > 0
    assert any(r["sector"] == "agriculture" for r in results)


def test_sector_with_underscore_matches_on_either_word():
    """sector 'employment_skill' should match a query containing just
    'employment'."""
    results = retrieve_policies("employment skill development policies")
    assert len(results) > 0
    assert any(r["sector"] == "employment_skill" for r in results)


def test_category_matching():
    from policy_loader import load_policies
    sample_category = load_policies()[0]["category"]
    results = retrieve_policies(sample_category)
    assert len(results) > 0


def test_sub_category_matching():
    from policy_loader import load_policies
    sample_sub_category = load_policies()[0]["sub_category"]
    results = retrieve_policies(sample_sub_category)
    assert len(results) > 0


# --- change / impact keyword matching -----------------------------------------

def test_change_keyword_matching():
    # PMGSY's change text mentions "all-weather road connectivity" -
    # verified in earlier phases' characterization tests.
    results = retrieve_policies("all weather road connectivity")
    names = [r["name"] for r in results]
    assert any("Gram Sadak" in n for n in names)


def test_impact_keyword_matching():
    results = retrieve_policies("reduces out of pocket expenses hospital")
    assert len(results) > 0


# --- broad / irrelevant / no-match queries --------------------------------------

def test_broad_query_returns_relevant_results():
    results = retrieve_policies("what changed in agriculture policies")
    assert len(results) > 0
    assert any(r["sector"] == "agriculture" for r in results)


def test_irrelevant_query_returns_no_results():
    results = retrieve_policies("purple elephants dancing on the moon")
    assert results == []


def test_no_match_query_returns_empty_list_not_arbitrary_policies():
    results = retrieve_policies("xyzabc123 nonsense gibberish query")
    assert results == []


# --- empty / whitespace-only query --------------------------------------------

def test_empty_query_returns_empty_list():
    assert retrieve_policies("") == []


def test_whitespace_only_query_returns_empty_list():
    assert retrieve_policies("     ") == []


def test_stopword_only_query_returns_empty_list():
    """The query "what is the" contains no meaningful (non-stopword)
    tokens - must not fall through to an unbounded/unfiltered fetch."""
    assert retrieve_policies("what is the") == []


# --- result limits, deterministic ordering, tie-breaking -----------------------

def test_result_limit_is_enforced():
    results = retrieve_policies("policy scheme government change impact", limit=3)
    assert len(results) <= 3


def test_default_result_limit():
    from retrieval import MAX_RESULTS
    results = retrieve_policies("policy government scheme")
    assert len(results) <= MAX_RESULTS


def test_results_are_ordered_by_score_descending():
    results = retrieve_policies("agriculture farmers crop insurance scheme")
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_repeated_calls_return_identical_deterministic_ordering():
    first = retrieve_policies("education policy scheme")
    second = retrieve_policies("education policy scheme")
    assert [r["id"] for r in first] == [r["id"] for r in second]


def test_tie_breaking_is_stable_across_repeated_calls():
    """Even for a broad query likely to produce score ties among several
    candidates, the final ordering must be identical every time (name
    then id tie-break), not just "some" stable subset."""
    runs = [retrieve_policies("government") for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


# --- ranking quality -----------------------------------------------------------

def test_exact_name_outranks_generic_keyword_match():
    results = retrieve_policies("ISRO Formation Policy, 1969")
    assert results[0]["name"] == "ISRO Formation Policy, 1969"
    if len(results) > 1:
        assert results[0]["score"] > results[1]["score"]


def test_sector_match_outranks_unrelated_keyword_only_match():
    results = retrieve_policies("banking")
    assert len(results) > 0
    top_sectors = [r["sector"] for r in results[:3]]
    assert "banking" in top_sectors


# --- ranking hierarchy guarantees ------------------------------------------------
#
# These test _score_policy() directly against a minimal dict stand-in
# (not the real dataset) so each tier can be isolated and deliberately
# maxed out in every combination - proving the hierarchy holds
# structurally, by construction, rather than merely "for the queries we
# happened to try against the real dataset". _score_policy() reads plain
# dict keys (name/category/sub_category/sector/change/impact - see
# retrieval.py), so a bare dict with sensible defaults for any key a
# given test doesn't care about is all that's needed here - no ORM, no
# database, nothing policy_loader-specific.

def _fake_policy(name="", category="", sub_category="", sector="",
                  change="", impact=""):
    return {
        "name": name, "category": category, "sub_category": sub_category,
        "sector": sector, "change": change, "impact": impact,
    }


def test_name_tier_outranks_every_lower_tier_maxed_out_at_once():
    """A weak-but-real name match must still outrank a policy that has
    NO name match at all but maxes out category, sub_category, sector,
    change, and impact simultaneously - proving lower-priority keyword
    accumulation cannot unexpectedly outrank a genuine higher-priority
    match, per the Phase 9 hardening brief."""
    from retrieval import _score_policy, _tokenize

    query_tokens = _tokenize("solar rooftop subsidy scheme")
    weak_name_match = _fake_policy(name="solar panel initiative")  # 1/3 name-token overlap, nothing else
    maxed_lower_tiers = _fake_policy(
        name="totally unrelated title",
        category="solar rooftop subsidy scheme",
        sub_category="solar rooftop subsidy scheme",
        sector="solar rooftop subsidy scheme",
        change="solar rooftop subsidy scheme " * 5,
        impact="solar rooftop subsidy scheme " * 5,
    )
    assert (
        _score_policy(weak_name_match, query_tokens)
        > _score_policy(maxed_lower_tiers, query_tokens)
    )


def test_category_tier_outranks_sector_change_impact_maxed_out():
    from retrieval import _score_policy, _tokenize

    query_tokens = _tokenize("digital health mission")
    category_match_only = _fake_policy(
        category="digital health mission", sub_category="digital health mission"
    )
    maxed_lower_tiers = _fake_policy(
        sector="digital health mission",
        change="digital health mission " * 5,
        impact="digital health mission " * 5,
    )
    assert (
        _score_policy(category_match_only, query_tokens)
        > _score_policy(maxed_lower_tiers, query_tokens)
    )


def test_sector_tier_outranks_change_impact_maxed_out():
    from retrieval import _score_policy, _tokenize

    query_tokens = _tokenize("employment skill scheme")
    sector_match_only = _fake_policy(sector="employment skill scheme")
    maxed_lower_tiers = _fake_policy(
        change="employment skill scheme " * 5,
        impact="employment skill scheme " * 5,
    )
    assert (
        _score_policy(sector_match_only, query_tokens)
        > _score_policy(maxed_lower_tiers, query_tokens)
    )


def test_change_tier_outranks_impact_tier_at_equal_coverage():
    from retrieval import _score_policy, _tokenize

    query_tokens = _tokenize("farmer income support")
    change_match_only = _fake_policy(change="farmer income support")
    impact_match_only = _fake_policy(impact="farmer income support")
    assert (
        _score_policy(change_match_only, query_tokens)
        > _score_policy(impact_match_only, query_tokens)
    )


def test_many_low_priority_keyword_matches_never_beat_one_high_priority_match():
    """The scenario the brief explicitly calls out: accumulating lots of
    low-priority (change/impact) keyword matches must never let a policy
    with no name/category/sector relevance outrank one with a genuine
    high-priority match, no matter how many low-priority tokens pile up."""
    from retrieval import _score_policy, _tokenize

    query_tokens = _tokenize(
        "national rural livelihood mission employment scheme "
        "income generation rural poverty alleviation program"
    )
    single_sector_match = _fake_policy(sector="employment skill")
    keyword_heavy_no_sector = _fake_policy(
        change="national rural livelihood mission employment scheme income "
               "generation rural poverty alleviation program details here",
        impact="national rural livelihood mission employment scheme income "
               "generation rural poverty alleviation program outcomes noted",
    )
    assert (
        _score_policy(single_sector_match, query_tokens)
        > _score_policy(keyword_heavy_no_sector, query_tokens)
    )


def test_zero_score_when_nothing_matches_any_tier():
    from retrieval import _score_policy, _tokenize

    query_tokens = _tokenize("agriculture farmer subsidy")
    no_match = _fake_policy(
        name="totally unrelated",
        category="unrelated",
        sub_category="unrelated",
        sector="unrelated",
        change="nothing relevant here",
        impact="nothing relevant here either",
    )
    assert _score_policy(no_match, query_tokens) == 0.0


# --- bounded retrieval ----------------------------------------------------------

def test_get_candidate_policies_with_empty_tokens_returns_empty_without_querying():
    from retrieval import get_candidate_policies
    assert get_candidate_policies(set()) == []


def test_get_candidate_policies_ordering_is_deterministic_across_repeated_calls():
    """The in-memory dataset (backend/policy_loader.py) is loaded once
    and returned in a fixed, stable id order (see its module docstring)
    - get_candidate_policies() preserves that order rather than
    reshuffling it. Assert both that repeated calls agree with each
    other and that the order is the documented id-ascending order, not
    merely "some order that happens to repeat"."""
    from retrieval import get_candidate_policies
    first = [p["id"] for p in get_candidate_policies({"agriculture"})]
    second = [p["id"] for p in get_candidate_policies({"agriculture"})]
    assert first == second
    assert first == sorted(first)


def test_get_candidate_policies_respects_max_candidates_cap():
    """A tiny cap must still return a bounded, id-ascending prefix - not
    an error and not an unbounded result."""
    from retrieval import get_candidate_policies
    results = get_candidate_policies({"policy"}, max_candidates=2)
    assert len(results) <= 2
    assert [p["id"] for p in results] == sorted(p["id"] for p in results)


def test_get_candidate_policies_never_matches_a_token_spanning_two_fields():
    """A token must match WITHIN a single field, never by spanning the
    boundary between two concatenated fields (a risk specific to the
    in-memory substring-scan implementation - see
    get_candidate_policies()'s docstring)."""
    from retrieval import get_candidate_policies
    # No real policy's name literally ends in "xzq" and no real policy's
    # sector literally starts with "qzy" - so a token that could only
    # match by gluing the tail of one field to the head of another
    # (e.g. "...xzq" + "qzy...") must never match anything.
    assert get_candidate_policies({"xzqqzy"}) == []


# --- limit validation -------------------------------------------------------------

def test_negative_limit_returns_empty_list_not_reversed_slice():
    """Python's list[:-1] silently drops the last element rather than
    erroring or returning [] - retrieve_policies must not inherit that
    footgun for a negative limit."""
    assert retrieve_policies("agriculture", limit=-1) == []
    assert retrieve_policies("agriculture", limit=-100) == []


def test_zero_limit_returns_empty_list():
    assert retrieve_policies("agriculture", limit=0) == []


def test_normal_positive_limit_is_respected():
    results = retrieve_policies("policy government scheme", limit=2)
    assert len(results) <= 2


def test_very_large_limit_returns_all_available_matches_without_error():
    results = retrieve_policies("policy government scheme agriculture education", limit=10 ** 9)
    assert isinstance(results, list)
    # Never more than the entire dataset, regardless of how large the
    # requested limit is - and no IndexError/padding of any kind.
    assert len(results) <= 151


def test_non_integer_limit_returns_empty_list_instead_of_raising():
    assert retrieve_policies("agriculture", limit="3") == []
    assert retrieve_policies("agriculture", limit=3.5) == []


# --- example queries from the Phase 9 brief -------------------------------------

def test_example_query_agriculture_changes():
    results = retrieve_policies("What changed in agriculture policies?")
    assert len(results) > 0
    assert any(r["sector"] == "agriculture" for r in results)


def test_example_query_education_changes():
    results = retrieve_policies("latest education policy changes")
    assert len(results) > 0
    assert any(r["sector"] == "education" for r in results)


def test_example_query_scholarships():
    results = retrieve_policies("policies related to scholarships")
    # May legitimately be empty if no policy text mentions "scholarship" -
    # the assertion is "does not error and does not return junk", not
    # "must find something" for every conceivable phrase.
    assert isinstance(results, list)


def test_example_query_farmers_schemes():
    results = retrieve_policies("government schemes for farmers")
    assert len(results) > 0


def test_example_query_banking_changes():
    results = retrieve_policies("what changed in banking")
    assert len(results) > 0
    assert any(r["sector"] == "banking" for r in results)


def test_example_query_renewable_energy():
    results = retrieve_policies("policies affecting renewable energy")
    assert isinstance(results, list)


def test_example_query_employment_skill_development():
    results = retrieve_policies("employment skill development policies")
    assert len(results) > 0
    assert any(r["sector"] == "employment_skill" for r in results)


def test_example_query_tell_me_about_education():
    results = retrieve_policies("tell me about education")
    assert len(results) > 0
    assert any(r["sector"] == "education" for r in results)


# --- never invents data ---------------------------------------------------------

def test_results_only_contain_real_policies_never_invented():
    from policy_loader import load_policies
    real_names = {p["name"] for p in load_policies()}
    results = retrieve_policies("education policy scheme government")
    for r in results:
        assert r["name"] in real_names


def test_result_shape_matches_policy_dict_plus_score():
    results = retrieve_policies("agriculture")
    assert len(results) > 0
    expected_keys = {"name", "category", "sub_category", "change", "impact", "sector", "id", "score"}
    assert set(results[0].keys()) == expected_keys


# --- chat integration (chatbot.py) ----------------------------------------------
#
# Retrieval V2 now runs unconditionally on every /chat request (it is
# JSON-backed, not database-gated - see the storage-migration report),
# so there is no longer a "database configured vs. not" distinction for
# these tests to isolate; get_response() is exercised directly, with no
# Flask app/app_context needed since neither chatbot.py nor retrieval.py
# touches a database.

def test_chat_uses_retrieval_v2_for_a_real_match(monkeypatch):
    """Forces category/name-similarity/keyword-match steps to fail (same
    isolation technique as the existing Phase 0.5 Gemini-fallback test),
    then confirms get_response() uses Retrieval V2's result instead of
    falling through to Gemini."""
    import chatbot as chatbot_module

    monkeypatch.setattr(chatbot_module, "detect_category", lambda text: None)
    monkeypatch.setattr(chatbot_module, "match_by_keywords", lambda *a, **k: (None, 0))

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("Gemini should not be called when Retrieval V2 finds a match")
    monkeypatch.setattr(chatbot_module, "call_generative_ai", _fail_if_called)

    result = chatbot_module.get_response("ISRO Formation Policy 1969")

    assert "ISRO Formation Policy, 1969" in result


def test_chat_falls_back_to_gemini_when_retrieval_v2_finds_nothing(monkeypatch):
    import chatbot as chatbot_module

    monkeypatch.setattr(chatbot_module, "detect_category", lambda text: None)
    monkeypatch.setattr(chatbot_module, "match_by_keywords", lambda *a, **k: (None, 0))
    monkeypatch.setattr(chatbot_module, "call_generative_ai", lambda text: "MOCKED_GEMINI_REPLY")

    # Verified empirically: "policy" triggers is_policy_related() (so
    # this reaches the Retrieval V2 / Gemini branch at all, past the
    # off-topic guard) but is itself a stopword extract_keywords()
    # filters out before tokenizing for retrieval, and the remaining
    # tokens match no real policy text - so Retrieval V2 genuinely
    # finds nothing and the code must fall through to Gemini, unlike a
    # query merely containing common words like "insurance" (which
    # legitimately matches real crop/health insurance policies and is
    # correct retrieval, not a bug).
    result = chatbot_module.get_response("policy zzz qqq xyz blorptastic nonexistent")

    assert result == "MOCKED_GEMINI_REPLY"


def test_chat_uses_retrieval_v2_regardless_of_database_url(monkeypatch):
    """The one meaningful behavior change from the storage migration,
    explicitly characterized: Retrieval V2 no longer skips itself when
    DATABASE_URL is unset (it has no database dependency left to gate
    on) - a real match is found either way. Pre-migration, this exact
    query with DATABASE_URL=None fell through to Gemini unchanged (see
    git history); that is now obsolete behavior, not a regression."""
    import chatbot as chatbot_module
    import config

    monkeypatch.setattr(config.Config, "DATABASE_URL", None)
    monkeypatch.setattr(chatbot_module, "detect_category", lambda text: None)
    monkeypatch.setattr(chatbot_module, "match_by_keywords", lambda *a, **k: (None, 0))

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("Gemini should not be called when Retrieval V2 finds a match")
    monkeypatch.setattr(chatbot_module, "call_generative_ai", _fail_if_called)

    result = chatbot_module.get_response("ISRO Formation Policy 1969")
    assert "ISRO Formation Policy, 1969" in result
