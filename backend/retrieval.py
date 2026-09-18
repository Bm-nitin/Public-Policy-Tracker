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
        -> bounded, deterministically-ordered candidate retrieval from
           PostgreSQL (parameterized ILIKE across name/sector/category/
           sub_category/change/impact, capped at MAX_CANDIDATES rows and
           ordered by primary key - never an unbounded table scan, never
           the JSON files; see get_candidate_policies()'s docstring for
           why the explicit ordering matters even though it plays no
           part in relevance)
        -> deterministic Python-side scoring, hierarchy-guaranteed by
           construction (see _score_policy)
        -> stable ranking (score DESC, normalized name ASC, id ASC)
        -> top `limit` (validated - see retrieve_policies())

Reuses backend/policy_service.py's Policy import rather than duplicating
data access, and backend/utils.py's existing normalization/tokenization
functions rather than inventing new ones or a synonym dictionary.
"""

from functools import lru_cache

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

# --- ranking design ----------------------------------------------------
#
# Priority order required by the Phase 9 brief:
#
#     name  >  category/sub_category  >  sector  >  change  >  impact
#
# Rather than tuning five independent additive constants (SCORE_EXACT_NAME
# = 100, SCORE_NAME_TOKEN_MAX = 40, ...) and hoping no combination of
# lower-tier matches ever adds up past a higher tier - which is exactly
# the bug being hardened here - each tier is scored as a normalized
# "digit" in [0.0, 1.0] and placed at a fixed positional weight (a power
# of TIER_BASE), the same trick as place-value in a base-TIER_BASE number
# system (ones/tens/hundreds, just with fractional digits and TIER_BASE
# instead of 10):
#
#     score = name_component      * TIER_BASE**4
#           + category_component  * TIER_BASE**3
#           + sector_component    * TIER_BASE**2
#           + change_component    * TIER_BASE**1
#           + impact_component    * TIER_BASE**0
#
# Because every component is capped to at most 1.0, the maximum possible
# combined contribution of ALL tiers below a given tier is strictly less
# than one unit of that tier:
#
#     TIER_BASE**3 + TIER_BASE**2 + TIER_BASE**1 + TIER_BASE**0
#         < TIER_BASE**4        (for any TIER_BASE > 1; true a fortiori
#                                 at TIER_BASE = 1000)
#
# so no amount of change/impact keyword accumulation can ever outrank a
# genuine sector match, no sector-only match can ever outrank a
# category/sub_category match, and so on up the hierarchy. This is a
# mathematical guarantee of the ranking, not a "we picked big enough
# constants" hope - see tests/test_retrieval.py's
# "ranking hierarchy guarantees" section, which proves this for every
# adjacent tier pair by deliberately maxing out every lower tier at once
# and confirming the higher tier still wins.
#
# TIER_BASE = 1000 keeps a wide safety margin over the finest realistic
# granularity of a component (roughly 1 / (number of tokens in a policy
# name or query), which in this dataset is well under 50), while still
# keeping the raw score value a normal, printable float.
TIER_BASE = 1000


def normalize_query(raw_query):
    """Case differences, repeated whitespace, and punctuation are all
    handled by the existing utils.clean_text() - reused here rather than
    duplicated. No LLM, no rewriting, no synonym table."""
    return clean_text(raw_query)


@lru_cache(maxsize=1024)
def _tokenize(text):
    """Returns a frozenset of meaningful tokens (stopwords/short words
    already stripped by utils.extract_keywords, which chatbot.py's
    existing keyword-match step already relies on).

    Memoized: category/sub_category/sector values in particular repeat
    across many of the 151 policy rows (e.g. dozens of rows share the
    same sector), so within a single retrieve_policies() call - and
    across calls, for the lifetime of the process - the same field value
    is tokenized once rather than re-running clean_text()/split() every
    time it's encountered. Safe to cache because _tokenize is a pure
    function of its string input and the frozenset it returns is
    immutable, so no caller can corrupt a cached result for another
    caller. Not a premature-optimization concern at 151 rows either way
    - this is a straightforward, low-risk elimination of genuinely
    repeated work, not a redesign of the scoring itself.
    """
    return frozenset(extract_keywords(text))


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
    """Bounded, parameterized, deterministically-ordered candidate fetch
    from PostgreSQL - never an unbounded scan, never raw string-
    interpolated SQL (every token is a bound ilike() parameter). Returns
    [] immediately for an empty token set rather than fetching anything.

    Ordering tradeoff (read before removing the order_by):
    LIMIT alone bounds *how many* rows PostgreSQL returns, but says
    nothing about *which* rows they are or in what order, unless the
    query is explicitly ordered - Postgres is free to hand back any
    matching N rows, in any order, and that choice can silently change
    between runs (parallel sequential/bitmap-heap scan worker
    scheduling, autovacuum reshuffling pages, a replica with a different
    physical layout than the primary, a future index that changes the
    cheapest plan, etc). At the current dataset size (151 rows, always
    under MAX_CANDIDATES) that non-determinism happens to be unobservable
    - every matching row is returned regardless of order - but it stops
    being unobservable the moment a token match count exceeds
    MAX_CANDIDATES, at which point *which* rows get truncated away would
    be silently unstable, breaking the "repeated calls return identical
    results" and tie-breaking guarantees this module promises.

    Policy.id (the primary key - already indexed, no migration needed) is
    used as that explicit order (ascending, SQL's default when no
    direction is specified). It is a deliberately "meaningless"
    tie-break for this purpose - id order carries no relevance signal -
    but that is exactly the point: candidate *selection* only needs to
    be reproducible, not relevance-ordered, because actual relevance
    ordering is entirely the job of the Python-side scoring/ranking step
    that runs after this. A more elaborate "order by relevance-ish
    heuristic before LIMIT" was considered and rejected as needless
    complexity duplicating work _score_policy already does properly.
    """
    if not tokens:
        return []
    return (
        Policy.query
        .filter(_build_candidate_filter(tokens))
        .order_by(Policy.id)
        .limit(max_candidates)
        .all()
    )


def _score_policy(policy, query_tokens):
    """Deterministic, explainable score. See the TIER_BASE discussion
    above for why the hierarchy (name > category/sub_category > sector >
    change > impact) is guaranteed rather than merely "usually true".
    Returns a float >= 0; a score of 0 means "no meaningful match" (see
    retrieve_policies(), which filters these out rather than returning
    arbitrary zero-score rows)."""
    name_tokens = _tokenize(policy.name)
    query_token_count = len(query_tokens) or 1  # guard: never called with empty query_tokens in practice

    # Tier 1 - name. Compared as a normalized token set (order-
    # independent) rather than a raw string, since "national education
    # policy" and "education policy national" should both count as full
    # coverage for this purpose. A query that fully covers the policy's
    # name tokens (whether or not the query also contains extra words)
    # reaches the tier maximum of 1.0 - there is no higher tier above
    # this one for a partial-vs-exact distinction to matter to.
    name_component = 0.0
    if name_tokens:
        overlap = len(name_tokens & query_tokens)
        if overlap:
            name_component = overlap / len(name_tokens)

    # Tier 2 - category / sub_category, combined into a single tier as
    # the brief specifies ("category/sub_category" as one priority
    # level). Each half is worth up to 0.5 so a match on both still caps
    # at the tier maximum of 1.0.
    category_component = 0.0
    if query_tokens & _tokenize(policy.category):
        category_component += 0.5
    if query_tokens & _tokenize(policy.sub_category):
        category_component += 0.5

    # Tier 3 - sector. Sector values are filename-derived (e.g.
    # "employment_skill") - split on underscore so "employment" alone
    # still matches. A sector match is boolean (present/absent), not a
    # partial-coverage ratio, so it's either 0.0 or the tier max of 1.0.
    sector_component = 0.0
    if query_tokens & _tokenize(policy.sector.replace("_", " ")):
        sector_component = 1.0

    # Tier 4 - change keyword matches, normalized by how much of the
    # *query* they cover (not the field's length, which is often long
    # free text) so this tier is also capped at 1.0.
    change_overlap = len(query_tokens & _tokenize(policy.change))
    change_component = change_overlap / query_token_count if change_overlap else 0.0

    # Tier 5 - impact keyword matches, same normalization as change.
    impact_overlap = len(query_tokens & _tokenize(policy.impact))
    impact_component = impact_overlap / query_token_count if impact_overlap else 0.0

    return (
        name_component * TIER_BASE ** 4
        + category_component * TIER_BASE ** 3
        + sector_component * TIER_BASE ** 2
        + change_component * TIER_BASE ** 1
        + impact_component * TIER_BASE ** 0
    )


def retrieve_policies(raw_query, limit=MAX_RESULTS):
    """The main Retrieval V2 entry point. Returns a list of dicts (each
    Policy.to_dict()'s shape plus `id` and `score`), ranked highest-score
    first, tie-broken by normalized name then id for full determinism.
    Returns [] for an empty/whitespace-only query, a query with no
    meaningful (non-stopword) tokens, an invalid `limit`, or a query that
    matches nothing in the database - never returns arbitrary policies
    just because the table is non-empty.

    `limit` contract (unchanged public signature - retrieve_policies(query,
    limit=...) - only its handling of edge values is now explicit):
      - limit <= 0 (including negative values): returns [] immediately,
        before any candidate retrieval or scoring is attempted. This is
        deliberate, not merely "whatever falls out of list slicing" -
        Python's `some_list[:-1]` silently drops the *last* element
        rather than raising or returning [], which would be a confusing,
        easy-to-miss bug for a caller who passes a negative limit by
        mistake (e.g. from an off-by-one on `count - 1`). Treating any
        non-positive limit as "zero results, requested explicitly or
        not" is the one contract that can never surprise a caller.
      - limit is not an int (e.g. a string or float): same treatment,
        returns [] rather than risking a confusing TypeError deep inside
        list slicing.
      - a very large limit (larger than the number of scored matches):
        no special handling needed or added - Python slicing already
        returns however many results actually exist, in full, matching
        the natural "give me up to N" contract with no risk of an
        IndexError or padding with anything invented.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return []

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
