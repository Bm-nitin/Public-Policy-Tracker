"""
Phase 10 - semantic retrieval.

Read-only: embeds the user's query, brute-force-scans every stored
PolicyEmbedding row (backend/embedding_store.py) with cosine similarity,
and returns the top matches joined back against the CURRENT data/*.json
dataset (backend/policy_loader.py) - never against any cached/stale
content baked into the embedding row itself (there isn't any - see
backend/models.py's PolicyEmbedding docstring). A stored embedding whose
policy_id no longer exists in the current JSON is silently skipped (see
that same docstring's "inert row" note), not an error.

Brute-force, not an index: at 151 rows, scoring every stored embedding
against the query vector in plain Python is sub-millisecond - there is
no need for pgvector's ANN indexing at this scale (see
backend/embeddings.py's module docstring and the Phase 10 design report
for the full reasoning). If this dataset ever grows enough for that to
change, this is the one function that would need a query-plan change -
nothing about the schema or the rest of this module.

Never raises: any failure (no DATABASE_URL, no GEMINI_API_KEY, provider
error, empty stored-embedding table) results in an empty list, exactly
like backend/retrieval.py's retrieve_policies() returns [] rather than
raising for its own "nothing to return" cases. This is what lets
semantic search be an optional, additive capability - see
hybrid_retrieve() in backend/retrieval.py, which calls this and treats
[] and "not available at all" identically.
"""

from config import Config
from embeddings import cosine_similarity, deserialize_embedding, embed_text
from policy_loader import get_policy_by_id

MAX_RESULTS = 5


def semantic_search(raw_query, limit=MAX_RESULTS):
    """Returns a list of policy dicts (the same shape
    backend/retrieval.py's retrieve_policies() returns) each with a
    `semantic_score` key (cosine similarity, range [-1, 1] - in practice
    always > 0 for genuinely related text with this model) instead of
    `score`. Requires an active Flask app context (embedding_store.py's
    database access needs one) - the same requirement the old DB-backed
    Retrieval V1/V2 had before the JSON storage migration; unlike
    deterministic Retrieval V2 today, semantic search still has a real
    database dependency (the embedding index), which is exactly why
    backend/retrieval.py's hybrid_retrieve() treats it as optional and
    always falls back to the deterministic path if this fails.

    `limit` contract: identical to retrieve_policies() - non-positive or
    non-int returns [] immediately, without embedding the query or
    touching the database at all.
    """
    if not isinstance(limit, int) or isinstance(limit, bool) or limit <= 0:
        return []
    if not raw_query or not raw_query.strip():
        return []
    if not Config.DATABASE_URL or not Config.GEMINI_API_KEY:
        return []

    try:
        # Imported lazily so importing this module never requires a
        # database connection to be configured (matches
        # backend/retrieval.py's "importable with no DB at all" property)
        # - only actually calling semantic_search() does.
        from embedding_store import list_stored_embeddings

        stored = list_stored_embeddings()
        if not stored:
            return []

        query_vector = embed_text(raw_query, task_type="RETRIEVAL_QUERY")

        scored = []
        for row in stored:
            policy = get_policy_by_id(row.policy_id)
            if policy is None:
                # Embedding for a policy_id no longer in the current JSON
                # dataset - inert, see module docstring.
                continue
            vector = deserialize_embedding(row.embedding)
            similarity = cosine_similarity(query_vector, vector)
            scored.append((similarity, policy))
    except Exception:
        # ANY failure here - a real database error (unreachable DB, the
        # policy_embeddings table not migrated yet, a connection drop
        # mid-query), a malformed stored row, an embedding-provider
        # error not already caught inside embed_text() as EmbeddingError,
        # anything - degrades to "semantic search found nothing", never
        # propagates. This is deliberately broader than catching just
        # EmbeddingError: Config.DATABASE_URL being set is not a promise
        # that the database is actually reachable right now, and a
        # transient DB problem must not be able to break
        # hybrid_retrieve()'s guarantee that deterministic Retrieval V2
        # keeps working regardless (see that function's docstring).
        return []

    if not scored:
        return []

    # Deterministic tie-breaking, same convention as retrieve_policies():
    # score DESC, name ASC, id ASC.
    scored.sort(key=lambda pair: (-pair[0], pair[1]["name"], pair[1]["id"]))

    results = []
    for similarity, policy in scored[:limit]:
        data = dict(policy)
        data["semantic_score"] = similarity
        results.append(data)
    return results
