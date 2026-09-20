"""
Phase 9 Retrieval V2 - deterministic policy retrieval and ranking.

ARCHITECTURE CHANGE (post-Phase-9): this module no longer queries
PostgreSQL at all. Policy data now comes entirely from
backend/policy_loader.py's cached data/*.json load (the same dataset
backend/policy_service.py's `GET /api/policies*` blueprint and the
legacy `GET /policies` route both use) - see policy_loader.py's module
docstring for how it assigns each policy a stable integer id. The
Phase 9 hardening this module was built around - deterministic candidate
ordering, the tier-based ranking hierarchy guarantee, limit validation,
cached tokenization - is unchanged in substance; only the data source
changed, from `Policy.query` to a plain Python list of dicts. Every
policy dict a candidate is drawn from already has the keys
name/category/sub_category/change/impact/sector/id - no ORM object,
no `.attribute` access, no session, no app context required.

Independent of Gemini: this module never calls Gemini and Gemini never
influences which policies are considered relevant here. chatbot.py may
use this module's results as an input to a later Gemini call in a future
phase, but that grounding step is explicitly out of scope here (see
chatbot.py's integration point for exactly where this plugs in).

Pipeline:
    user query
        -> normalize (reuses utils.clean_text - no LLM, no synonym table)
        -> tokenize (reuses utils.extract_keywords - strips stopwords/
           short words, same tokenizer chatbot.py's existing keyword
           matching already uses)
        -> bounded, deterministically-ordered candidate retrieval from
           the in-memory JSON-loaded dataset (substring match across
           name/sector/category/sub_category/change/impact, capped at
           MAX_CANDIDATES and in the dataset's own stable id order - see
           get_candidate_policies()'s docstring)
        -> deterministic Python-side scoring, hierarchy-guaranteed by
           construction (see _score_policy)
        -> stable ranking (score DESC, normalized name ASC, id ASC)
        -> top `limit` (validated - see retrieve_policies())

Reuses backend/policy_loader.py's cached loader rather than duplicating
data access, and backend/utils.py's existing normalization/tokenization
functions rather than inventing new ones or a synonym dictionary.

PHASE 10 ADDITION: hybrid_retrieve() (bottom of this file) adds semantic
search (backend/semantic_retrieval.py, embedding-based) as an optional
second capability alongside everything above, which is entirely
unchanged - retrieve_policies() and get_candidate_policies() still work
exactly as they did in Phase 9, with no database, embedding, or network
dependency whatsoever. See hybrid_retrieve()'s own docstring for the
"controlled hybrid ranking" strategy (deterministic-first union, scores
never blended) and for why deterministic Retrieval V2 is always the
floor even when semantic search is fully unavailable.
"""

from functools import lru_cache

from policy_loader import load_policies
from utils import clean_text, extract_keywords

# Candidate fetch is bounded - generous relative to the current 151-row
# dataset so it never artificially excludes a real match today, but
# still a genuine cap that protects a much larger future dataset from
# scoring every single record on every query.
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
# Rather than tuning five independent additive constants and hoping no
# combination of lower-tier matches ever adds up past a higher tier,
# each tier is scored as a normalized "digit" in [0.0, 1.0] and placed
# at a fixed positional weight (a power of TIER_BASE), the same trick as
# place-value in a base-TIER_BASE number system (ones/tens/hundreds,
# just with fractional digits and TIER_BASE instead of 10):
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
# category/sub_category match, and so on up the hierarchy - a
# mathematical guarantee of the ranking, not a "we picked big enough
# constants" hope. See tests/test_retrieval.py's "ranking hierarchy
# guarantees" section, which proves this for every adjacent tier pair by
# deliberately maxing out every lower tier at once and confirming the
# higher tier still wins.
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
    caller.
    """
    return frozenset(extract_keywords(text))


def get_candidate_policies(tokens, max_candidates=MAX_CANDIDATES):
    """Bounded, deterministically-ordered candidate fetch from the
    in-memory JSON-loaded dataset (backend/policy_loader.py). Returns []
    immediately for an empty token set rather than scanning anything.

    Ordering: policy_loader.load_policies() already returns policies in
    a fixed, stable order (ascending id, itself assigned deterministically
    from sorted filenames + JSON array order - see that module's
    docstring), and this function preserves that order rather than
    reshuffling it. This is a meaningfully simpler story than the old
    PostgreSQL-backed version of this function needed: a SQL
    `LIMIT` with no `ORDER BY` gives the database no ordering guarantee
    (see git history / the Phase 9 hardening report for the full
    reasoning), but there is no query planner here at all - `matching`
    is a plain Python list comprehension over an already-ordered
    in-memory list, so truncating it with `[:max_candidates]` is
    trivially and unconditionally deterministic on every call, with
    nothing further required to guarantee it.

    Candidate *selection* only needs to be reproducible, not
    relevance-ordered - actual relevance ordering is entirely the job of
    the Python-side scoring/ranking step that runs after this.
    """
    if not tokens:
        return []

    matching = []
    for policy in load_policies():
        haystacks = (
            policy["name"].lower(), policy["sector"].lower(), policy["category"].lower(),
            policy["sub_category"].lower(), policy["change"].lower(), policy["impact"].lower(),
        )
        # Checked per-field (not one joined string) so a token can never
        # false-positive by spanning the boundary between two fields.
        if any(token in field for field in haystacks for token in tokens):
            matching.append(policy)
            if len(matching) >= max_candidates:
                break

    return matching


def _score_policy(policy, query_tokens):
    """Deterministic, explainable score. `policy` is a plain dict (from
    policy_loader.py or a test stand-in) with name/category/
    sub_category/sector/change/impact keys - no ORM object, no database
    access. See the TIER_BASE discussion above for why the hierarchy
    (name > category/sub_category > sector > change > impact) is
    guaranteed rather than merely "usually true". Returns a float >= 0;
    a score of 0 means "no meaningful match" (see retrieve_policies(),
    which filters these out rather than returning arbitrary zero-score
    rows)."""
    name_tokens = _tokenize(policy["name"])
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
    if query_tokens & _tokenize(policy["category"]):
        category_component += 0.5
    if query_tokens & _tokenize(policy["sub_category"]):
        category_component += 0.5

    # Tier 3 - sector. Sector values are filename-derived (e.g.
    # "employment_skill") - split on underscore so "employment" alone
    # still matches. A sector match is boolean (present/absent), not a
    # partial-coverage ratio, so it's either 0.0 or the tier max of 1.0.
    sector_component = 0.0
    if query_tokens & _tokenize(policy["sector"].replace("_", " ")):
        sector_component = 1.0

    # Tier 4 - change keyword matches, normalized by how much of the
    # *query* they cover (not the field's length, which is often long
    # free text) so this tier is also capped at 1.0.
    change_overlap = len(query_tokens & _tokenize(policy["change"]))
    change_component = change_overlap / query_token_count if change_overlap else 0.0

    # Tier 5 - impact keyword matches, same normalization as change.
    impact_overlap = len(query_tokens & _tokenize(policy["impact"]))
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
    the policy's own dict - name/category/sub_category/change/impact/
    sector/id - plus `score`), ranked highest-score first, tie-broken by
    normalized name then id for full determinism. Returns [] for an
    empty/whitespace-only query, a query with no meaningful
    (non-stopword) tokens, an invalid `limit`, or a query that matches
    nothing in the dataset - never returns arbitrary policies just
    because the dataset is non-empty. No Flask app context, database
    connection, or PostgreSQL of any kind is required to call this.

    `limit` contract (unchanged public signature - retrieve_policies(query,
    limit=...) - only its handling of edge values is explicit):
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
    scored.sort(key=lambda pair: (-pair[0], clean_text(pair[1]["name"]), pair[1]["id"]))

    results = []
    for score, policy in scored[:limit]:
        data = dict(policy)
        data["score"] = score
        results.append(data)
    return results


def hybrid_retrieve(raw_query, limit=MAX_RESULTS):
    """Phase 10's "controlled hybrid ranking": deterministic-first union,
    never a blended/renormalized score.

    Deterministic Retrieval V2's score (see TIER_BASE above) and semantic
    search's cosine similarity (backend/semantic_retrieval.py) are
    different, incomparable scales built for different purposes - there
    is no principled way to average or otherwise combine them into one
    number without an arbitrary weighting decision (the Phase 10 brief
    explicitly forbids exactly this: "Do not combine their scores
    arbitrarily without tests and a documented strategy"). Rather than
    invent such a weighting, this function keeps them as two separate,
    ordered lists and unions them positionally:

      1. Run deterministic Retrieval V2 first, in full, exactly as
         retrieve_policies() already ranks it - every result tagged
         retrieval_source="deterministic". This is the guaranteed,
         hierarchy-proven ranking from Phase 9; hybrid_retrieve() never
         reorders or reweights it.
      2. Only if that didn't fill `limit` results, semantic search
         (backend/semantic_retrieval.py) is tried for the remainder,
         in ITS OWN score order, skipping anything already returned by
         step 1 (by id) - tagged retrieval_source="semantic".
      3. If semantic search is unavailable for any reason (no
         DATABASE_URL, no GEMINI_API_KEY, no embeddings generated yet,
         a provider error) semantic_search() itself already returns []
         (see that module's docstring) - hybrid_retrieve() treats that
         identically to "semantic search found nothing" and simply
         returns whatever deterministic Retrieval V2 found. Retrieval
         V2 is therefore ALWAYS the floor: hybrid_retrieve() can never
         return fewer or worse deterministic results than
         retrieve_policies() would have on its own, only additional
         ones semantic search can supply on top.

    Every result dict gets a `retrieval_source` key so a caller can
    always tell which system produced it - this is deliberate, not
    incidental: silently mixing two differently-scored result sets
    without a way to tell them apart would make the hybrid ranking's
    behavior opaque to test and to reason about, which is exactly what
    the "documented strategy" requirement is about.
    """
    deterministic_results = retrieve_policies(raw_query, limit=limit)
    for result in deterministic_results:
        result["retrieval_source"] = "deterministic"

    if len(deterministic_results) >= (limit if isinstance(limit, int) and not isinstance(limit, bool) else 0):
        return deterministic_results

    try:
        from semantic_retrieval import semantic_search
    except ImportError:
        return deterministic_results

    seen_ids = {result["id"] for result in deterministic_results}
    remaining = limit - len(deterministic_results)

    semantic_results = semantic_search(raw_query, limit=limit)
    added = []
    for result in semantic_results:
        if result["id"] in seen_ids:
            continue
        result["retrieval_source"] = "semantic"
        added.append(result)
        if len(added) >= remaining:
            break

    return deterministic_results + added
