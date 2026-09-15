"""
Phase 9: Retrieval V2 - deterministic, database-backed policy retrieval
and ranking.

Independent of Gemini: this module never calls Gemini and Gemini never
influences which policies are considered relevant here. chatbot.py may
use this module's results as an input to a later Gemini call in a future
phase, but that grounding step is explicitly out of scope for Phase 9
(see chatbot.py's integration point for exactly where this plugs in).

Pipeline:
    user query
        -> normalize (reuses utils.clean_text - no LLM, no synonym table)
        -> tokenize (reuses utils.extract_keywords - strips stopwords/
           short words, same tokenizer chatbot.py's existing keyword
           matching already uses)
        -> bounded candidate retrieval from PostgreSQL (parameterized
           ILIKE across name/sector/category/sub_category/change/impact,
           capped at MAX_CANDIDATES rows - never an unbounded table scan,
           never the JSON files)
        -> deterministic Python-side scoring (see _score_policy)
        -> stable ranking (score DESC, normalized name ASC, id ASC)
        -> top MAX_RESULTS

Reuses backend/policy_service.py's Policy import rather than duplicating
data access, and backend/utils.py's existing normalization/tokenization
functions rather than inventing new ones or a synonym dictionary.
"""

from sqlalchemy import or_

from models import Policy
from utils import clean_text, extract_keywords

# Candidate fetch is bounded at the SQL level (never an unbounded table
# scan) - generous relative to the current 151-row dataset so it never
# artificially excludes a real match today, but still a genuine cap that
# protects a much larger future dataset.
MAX_CANDIDATES = 300

# Results returned to the caller after ranking - small and deliberate,
# matching chatbot.py's existing format_multiple() cap of 3 and giving
# callers room to show a short "here are the closest matches" list
# without dumping the whole candidate set.
MAX_RESULTS = 5

# Scoring weights - deliberately tiered to match the Phase 9 brief's
# stated priority order (name > category/sub_category > sector >
# change > impact keywords). Not intended to guarantee perfect
# lexicographic separation for every conceivable combination of partial
# matches (that would require a much more complex model for no real
# benefit here) - intended to be simple, explainable, and correct for
# representative queries, which is what "deterministic, explainable,
# testable" calls for.
SCORE_EXACT_NAME = 100
SCORE_NAME_TOKEN_MAX = 40          # scaled by (matching tokens / name tokens)
SCORE_CATEGORY_MATCH = 25
SCORE_SUB_CATEGORY_MATCH = 20
SCORE_SECTOR_MATCH = 15
SCORE_PER_CHANGE_TOKEN = 3
SCORE_PER_IMPACT_TOKEN = 2


def normalize_query(raw_query):
    """Case differences, repeated whitespace, and punctuation are all
    handled by the existing utils.clean_text() - reused here rather than
    duplicated. No LLM, no rewriting, no synonym table."""
    return clean_text(raw_query)


def _tokenize(text):
    """Returns a set of meaningful tokens (stopwords/short words already
    stripped by utils.extract_keywords, which chatbot.py's existing
    keyword-match step already relies on)."""
    return set(extract_keywords(text))


def _build_candidate_filter(tokens):
    conditions = []
    for token in tokens:
        pattern = f"%{token}%"
        conditions.append(Policy.name.ilike(pattern))
        conditions.append(Policy.sector.ilike(pattern))
        conditions.append(Policy.category.ilike(pattern))
        conditions.append(Policy.sub_category.ilike(pattern))
        conditions.append(Policy.change.ilike(pattern))
        conditions.append(Policy.impact.ilike(pattern))
    return or_(*conditions)


def get_candidate_policies(tokens, max_candidates=MAX_CANDIDATES):
    """Bounded, parameterized candidate fetch from PostgreSQL - never an
    unbounded scan, never raw string-interpolated SQL (every token is a
    bound ilike() parameter). Returns [] immediately for an empty token
    set rather than fetching anything."""
    if not tokens:
        return []
    return (
        Policy.query
        .filter(_build_candidate_filter(tokens))
        .limit(max_candidates)
        .all()
    )


def _score_policy(policy, query_tokens):
    """Deterministic, explainable score - see the module-level SCORE_*
    constants for the weighting rationale. Returns a float >= 0; a score
    of 0 means "no meaningful match" (see retrieve_policies(), which
    filters these out rather than returning arbitrary zero-score rows)."""
    score = 0.0

    name_tokens = _tokenize(policy.name)

    # 1. Exact policy name match - the strongest possible signal. Compared
    # as a normalized token set (order-independent) rather than a raw
    # string, since "national education policy" and "education policy
    # national" should both count as exact for this purpose.
    if name_tokens and query_tokens and name_tokens == query_tokens:
        score += SCORE_EXACT_NAME

    # 2. Strong (partial) name token match, scaled by how much of the
    # policy name the query actually covers.
    if name_tokens:
        overlap = len(name_tokens & query_tokens)
        if overlap:
            score += SCORE_NAME_TOKEN_MAX * (overlap / len(name_tokens))

    # 3. category / sub_category match.
    if query_tokens & _tokenize(policy.category):
        score += SCORE_CATEGORY_MATCH
    if query_tokens & _tokenize(policy.sub_category):
        score += SCORE_SUB_CATEGORY_MATCH

    # 4. sector match (sector values are filename-derived, e.g.
    # "employment_skill" - split on underscore so "employment" alone
    # still matches).
    sector_tokens = _tokenize(policy.sector.replace("_", " "))
    if query_tokens & sector_tokens:
        score += SCORE_SECTOR_MATCH

    # 5. meaningful keyword matches in change.
    change_overlap = len(query_tokens & _tokenize(policy.change))
    if change_overlap:
        score += SCORE_PER_CHANGE_TOKEN * change_overlap

    # 6. meaningful keyword matches in impact.
    impact_overlap = len(query_tokens & _tokenize(policy.impact))
    if impact_overlap:
        score += SCORE_PER_IMPACT_TOKEN * impact_overlap

    return score


def retrieve_policies(raw_query, limit=MAX_RESULTS):
    """The main Retrieval V2 entry point. Returns a list of dicts (each
    Policy.to_dict()'s shape plus `id` and `score`), ranked highest-score
    first, tie-broken by normalized name then id for full determinism.
    Returns [] for an empty/whitespace-only query, a query with no
    meaningful (non-stopword) tokens, or a query that matches nothing in
    the database - never returns arbitrary policies just because the
    table is non-empty.
    """
    normalized_query = normalize_query(raw_query)
    if not normalized_query:
        return []

    query_tokens = _tokenize(normalized_query)
    if not query_tokens:
        return []

    candidates = get_candidate_policies(query_tokens)
    if not candidates:
        return []

    scored = []
    for policy in candidates:
        score = _score_policy(policy, query_tokens)
        if score > 0:
            scored.append((score, policy))

    if not scored:
        return []

    # Deterministic tie-breaking: score DESC, normalized name ASC, id ASC.
    scored.sort(key=lambda pair: (-pair[0], clean_text(pair[1].name), pair[1].id))

    results = []
    for score, policy in scored[:limit]:
        data = policy.to_dict()
        data["id"] = policy.id
        data["score"] = score
        results.append(data)
    return results
