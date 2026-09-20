"""
Phase 10 - embedding generation building blocks.

DESIGN (see the Phase 10 storage-migration/design report for the full
reasoning; summarized here for anyone reading this file directly):

Provider: Google's Gemini Embeddings API, via the `google-genai` SDK this
app already depends on (requirements.txt) and is already configured with
(Config.GEMINI_API_KEY - the same key backend/chatbot.py's chat
generation uses). No new dependency, no new secret.

Model: gemini-embedding-001 (Config.EMBEDDING_MODEL, overridable via the
EMBEDDING_MODEL env var). This is Google's current stable embedding
model as of this writing. An earlier, smaller model
(text-embedding-004) is NOT used here because Google deprecated it in
January 2026 - building Phase 10 on an already-retired model would be a
non-starter.

Dimensions: 768 (Config.EMBEDDING_DIMENSIONS), via gemini-embedding-001's
Matryoshka Representation Learning support (output_dimensionality=768).
The model's native output is 3072-dimensional; published MTEB benchmarks
show truncating to 768 costs about 0.26% quality (67.99 vs 68.17) for a
75% storage reduction - a clearly worthwhile tradeoff for a 151-document
corpus where nothing here is remotely benchmark-sensitive. 768 is also
one of Google's own explicitly recommended truncation points (alongside
1536), not an arbitrary choice.

Embeddings other than the default 3072 dimensions are NOT pre-normalized
by the API (Google's documented behavior) - embed_text() L2-normalizes
every vector it returns before handing it back, so every stored
embedding is unit-length and plain dot product is directly usable as
cosine similarity (see cosine_similarity() below, which still divides by
the norms defensively rather than assuming this).
"""

import hashlib
import json
import math

from config import Config


class EmbeddingError(Exception):
    """Raised for any embedding-provider failure (missing API key,
    network error, malformed response, rate limit, etc.) - callers
    (backend/semantic_retrieval.py, the embedding-generation CLI command)
    catch this specifically and degrade gracefully rather than letting a
    provider outage take anything else down. Deterministic Retrieval V2
    (backend/retrieval.py) never raises or catches this - it has no
    embedding dependency at all."""


def build_embedding_document(policy):
    """The exact text that gets embedded for a policy - deterministic and
    reproducible from the policy dict alone (name/category/sub_category/
    change/impact - the same fields backend/retrieval.py's deterministic
    scoring already reads), so re-running this on unchanged JSON always
    produces byte-identical text, which is what makes content_fingerprint()
    below a meaningful staleness check. Sector is deliberately NOT
    included: it's a filing/routing attribute (derived from the source
    filename - see policy_loader.py), not part of the policy's own
    substantive content, and Retrieval V2 already handles sector matching
    on its own dedicated tier - duplicating it into the embedded text
    would just dilute the semantic signal from the fields that actually
    describe what the policy does."""
    return (
        f"{policy['name']}\n"
        f"Category: {policy['category']} / {policy['sub_category']}\n"
        f"Change: {policy['change']}\n"
        f"Impact: {policy['impact']}"
    )


def content_fingerprint(text):
    """SHA-256 hex digest of the exact embedded text. Stored alongside
    each embedding (see backend/models.py's PolicyEmbedding) so a future
    embedding-generation run can tell, without re-embedding anything or
    calling the provider at all, whether a policy's JSON content has
    changed since its embedding was generated: recompute
    build_embedding_document() + content_fingerprint() for the current
    JSON and compare against the stored hash. A mismatch means stale;
    a match means "leave this one alone, incremental generation must not
    regenerate embeddings unnecessarily" (see the Phase 10 brief's
    performance requirement)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(vector):
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0:
        return vector
    return [x / norm for x in vector]


def embed_text(text, task_type="RETRIEVAL_DOCUMENT"):
    """Calls the Gemini Embeddings API for a single piece of text.
    Returns a plain list[float] of length Config.EMBEDDING_DIMENSIONS,
    L2-normalized (see module docstring). Raises EmbeddingError - never
    lets google-genai's own exception types, a missing API key, or a
    malformed response propagate to the caller as something unexpected.

    task_type: "RETRIEVAL_DOCUMENT" when embedding a policy (indexing
    time - see build_embedding_document()) vs "RETRIEVAL_QUERY" when
    embedding a user's search query (backend/semantic_retrieval.py) -
    the Gemini API uses this to optimize the embedding space
    asymmetrically for retrieval, matching Google's documented usage
    (this is not a made-up parameter - the same task_type distinction
    appears in Google's own embeddings quickstart)."""
    if not Config.GEMINI_API_KEY:
        raise EmbeddingError("GEMINI_API_KEY is not configured - cannot generate embeddings.")
    if not text or not text.strip():
        raise EmbeddingError("Cannot embed empty text.")

    try:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=Config.GEMINI_API_KEY)
        response = client.models.embed_content(
            model=Config.EMBEDDING_MODEL,
            contents=text,
            config=types.EmbedContentConfig(
                task_type=task_type,
                output_dimensionality=Config.EMBEDDING_DIMENSIONS,
            ),
        )
        values = list(response.embeddings[0].values)
    except EmbeddingError:
        raise
    except Exception as e:  # noqa: BLE001 - any provider/SDK failure degrades the same way
        raise EmbeddingError(f"Embedding request failed: {e}") from e

    if len(values) != Config.EMBEDDING_DIMENSIONS:
        raise EmbeddingError(
            f"Embedding provider returned {len(values)} dimensions, "
            f"expected {Config.EMBEDDING_DIMENSIONS}."
        )

    return _normalize(values)


def serialize_embedding(vector):
    """list[float] -> the exact TEXT this app stores in PostgreSQL (see
    backend/models.py's PolicyEmbedding.embedding column and the Phase 10
    design report's "vector storage approach" section for why this is
    plain JSON text rather than a pgvector `vector` column: it works on
    any PostgreSQL version/plan with zero extension dependency, which
    this app cannot assume is available - see that report's Render/
    pgvector compatibility finding). Plain json.dumps of a flat float
    list - no numpy, no custom binary format, trivially portable and
    human-inspectable."""
    return json.dumps(vector)


def deserialize_embedding(raw):
    """The inverse of serialize_embedding() - also tolerant of a value
    that's already a list (some callers/tests construct one directly
    rather than round-tripping through JSON)."""
    if isinstance(raw, list):
        return raw
    return json.loads(raw)


def cosine_similarity(a, b):
    """Standard cosine similarity, computed defensively (dividing by each
    vector's own norm) rather than assuming both inputs are already
    unit-length, even though every embedding this module produces already
    is (see module docstring) - this function is also exercised directly
    in tests with hand-constructed non-normalized vectors. Returns 0.0
    for a zero vector rather than raising a division error - a
    same-length all-zero embedding is degenerate input, not something
    worth crashing retrieval over."""
    if len(a) != len(b):
        raise ValueError(f"vector length mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
