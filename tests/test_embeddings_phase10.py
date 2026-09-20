"""
Phase 10 - semantic search / embeddings.

No real network access and no real Gemini API key is assumed anywhere
in this file - every test that would otherwise call the embedding
provider monkeypatches embeddings.embed_text (or the google.genai SDK
one layer below it) instead. Storage tests use db_test_app (see
conftest.py) - the same offline flask_sqlalchemy shim/sqlite setup every
other database-backed test file in this suite already uses; no pgvector
of any kind is required (see backend/embeddings.py's module docstring
for why this schema never assumes pgvector is available).
"""

import json

import pytest


# --- embedding document construction -----------------------------------------

def test_build_embedding_document_is_deterministic():
    from embeddings import build_embedding_document

    policy = {
        "name": "Test Policy", "category": "Cat", "sub_category": "Sub",
        "change": "Something changed.", "impact": "Something improved.",
        "sector": "agriculture", "id": 1,
    }
    assert build_embedding_document(policy) == build_embedding_document(policy)


def test_build_embedding_document_includes_substantive_fields_not_sector():
    from embeddings import build_embedding_document

    policy = {
        "name": "UNIQUE_NAME_TOKEN", "category": "UNIQUE_CATEGORY_TOKEN",
        "sub_category": "UNIQUE_SUBCAT_TOKEN", "change": "UNIQUE_CHANGE_TOKEN",
        "impact": "UNIQUE_IMPACT_TOKEN", "sector": "UNIQUE_SECTOR_TOKEN", "id": 1,
    }
    doc = build_embedding_document(policy)
    for token in ("UNIQUE_NAME_TOKEN", "UNIQUE_CATEGORY_TOKEN", "UNIQUE_SUBCAT_TOKEN",
                  "UNIQUE_CHANGE_TOKEN", "UNIQUE_IMPACT_TOKEN"):
        assert token in doc
    # Sector is deliberately excluded - see build_embedding_document()'s docstring.
    assert "UNIQUE_SECTOR_TOKEN" not in doc


def test_build_embedding_document_changes_when_content_changes():
    from embeddings import build_embedding_document

    base = {"name": "A", "category": "B", "sub_category": "C", "change": "D", "impact": "E"}
    changed = dict(base, impact="DIFFERENT")
    assert build_embedding_document(base) != build_embedding_document(changed)


# --- content fingerprinting ----------------------------------------------------

def test_content_fingerprint_is_stable_for_identical_text():
    from embeddings import content_fingerprint
    assert content_fingerprint("hello world") == content_fingerprint("hello world")


def test_content_fingerprint_changes_for_different_text():
    from embeddings import content_fingerprint
    assert content_fingerprint("hello world") != content_fingerprint("hello there")


def test_content_fingerprint_is_a_sha256_hex_digest():
    from embeddings import content_fingerprint
    fp = content_fingerprint("anything")
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


# --- serialize / deserialize ----------------------------------------------------

def test_serialize_deserialize_roundtrip():
    from embeddings import deserialize_embedding, serialize_embedding
    vector = [0.1, -0.2, 0.3, 0.0]
    assert deserialize_embedding(serialize_embedding(vector)) == vector


def test_serialize_embedding_produces_valid_json():
    from embeddings import serialize_embedding
    raw = serialize_embedding([1.0, 2.0])
    assert json.loads(raw) == [1.0, 2.0]


def test_deserialize_embedding_accepts_a_list_directly():
    from embeddings import deserialize_embedding
    assert deserialize_embedding([1.0, 2.0]) == [1.0, 2.0]


# --- cosine similarity -----------------------------------------------------------

def test_cosine_similarity_identical_vectors_is_one():
    from embeddings import cosine_similarity
    assert cosine_similarity([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_similarity_orthogonal_vectors_is_zero():
    from embeddings import cosine_similarity
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_opposite_vectors_is_negative_one():
    from embeddings import cosine_similarity
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_zero_vector_returns_zero_not_error():
    from embeddings import cosine_similarity
    assert cosine_similarity([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_similarity_mismatched_length_raises():
    from embeddings import cosine_similarity
    with pytest.raises(ValueError):
        cosine_similarity([1.0, 2.0], [1.0, 2.0, 3.0])


# --- embed_text (mocked - never touches the real network) ----------------------

class _FakeEmbeddingValues:
    def __init__(self, values):
        self.values = values


class _FakeEmbedResponse:
    def __init__(self, values):
        self.embeddings = [_FakeEmbeddingValues(values)]


class _FakeModels:
    def __init__(self, values, exc=None):
        self._values = values
        self._exc = exc

    def embed_content(self, model, contents, config):
        if self._exc:
            raise self._exc
        return _FakeEmbedResponse(self._values)


class _FakeGenaiClient:
    def __init__(self, values=None, exc=None, api_key=None):
        self.models = _FakeModels(values, exc)


def _install_fake_genai(monkeypatch, values=None, exc=None):
    """embed_text() does `from google import genai` inside the function
    body (see embeddings.py) - patching sys.modules is the reliable way
    to intercept that regardless of whether the real google-genai
    package is installed in this environment."""
    import sys
    import types

    fake_google = types.ModuleType("google")
    fake_genai = types.ModuleType("google.genai")
    fake_types = types.ModuleType("google.genai.types")

    fake_genai.Client = lambda api_key=None: _FakeGenaiClient(values=values, exc=exc)
    fake_types.EmbedContentConfig = lambda **kwargs: kwargs
    fake_google.genai = fake_genai

    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.genai", fake_genai)
    monkeypatch.setitem(sys.modules, "google.genai.types", fake_types)


def test_embed_text_returns_normalized_vector_of_configured_dimension(monkeypatch):
    import config
    import embeddings

    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(config.Config, "EMBEDDING_DIMENSIONS", 3)
    _install_fake_genai(monkeypatch, values=[3.0, 4.0, 0.0])  # norm = 5

    vector = embeddings.embed_text("some policy text")

    assert len(vector) == 3
    norm = sum(x * x for x in vector) ** 0.5
    assert norm == pytest.approx(1.0)
    assert vector == pytest.approx([0.6, 0.8, 0.0])


def test_embed_text_raises_without_api_key(monkeypatch):
    import config
    import embeddings

    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", None)
    with pytest.raises(embeddings.EmbeddingError):
        embeddings.embed_text("some text")


def test_embed_text_raises_for_empty_text(monkeypatch):
    import config
    import embeddings

    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", "fake-key-for-test")
    with pytest.raises(embeddings.EmbeddingError):
        embeddings.embed_text("   ")


def test_embed_text_wraps_provider_failures(monkeypatch):
    import config
    import embeddings

    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", "fake-key-for-test")
    _install_fake_genai(monkeypatch, exc=RuntimeError("provider is down"))

    with pytest.raises(embeddings.EmbeddingError):
        embeddings.embed_text("some text")


def test_embed_text_raises_on_dimension_mismatch(monkeypatch):
    import config
    import embeddings

    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(config.Config, "EMBEDDING_DIMENSIONS", 768)
    _install_fake_genai(monkeypatch, values=[0.1, 0.2])  # wrong length

    with pytest.raises(embeddings.EmbeddingError):
        embeddings.embed_text("some text")


# --- embedding storage (backend/embedding_store.py) -----------------------------

@pytest.fixture
def embeddings_db_app(db_test_app, monkeypatch):
    """db_test_app (real table creation via the offline shim - see
    conftest.py) with Config.DATABASE_URL pointed at it, same pattern
    registration_client/policies_client already use. Deliberately does
    NOT import or touch anything policy-table-related - there is no
    `policies` table in this fixture's schema at all (Policy was removed
    - see backend/models.py), which is itself part of what proves
    embedding storage has no policy-table dependency (Phase 10 testing
    requirement #13)."""
    import config
    monkeypatch.setattr(config.Config, "DATABASE_URL", "sqlite:///:memory:")
    return db_test_app


def test_upsert_and_get_stored_embedding(embeddings_db_app):
    from embedding_store import get_stored_embedding, upsert_embedding
    from database import db

    with embeddings_db_app.app_context():
        upsert_embedding(policy_id=1, vector=[0.1, 0.2, 0.3], content_hash="abc123", model="test-model")
        db.session.commit()

        row = get_stored_embedding(1)
        assert row is not None
        assert row.policy_id == 1
        assert row.content_hash == "abc123"
        assert row.model == "test-model"
        assert row.embedding_dim == 3


def test_upsert_embedding_updates_existing_row_not_duplicates(embeddings_db_app):
    from embedding_store import list_stored_embeddings, upsert_embedding
    from database import db

    with embeddings_db_app.app_context():
        upsert_embedding(policy_id=1, vector=[0.1], content_hash="hash-v1")
        db.session.commit()
        upsert_embedding(policy_id=1, vector=[0.2, 0.3], content_hash="hash-v2")
        db.session.commit()

        rows = [r for r in list_stored_embeddings() if r.policy_id == 1]
        assert len(rows) == 1
        assert rows[0].content_hash == "hash-v2"
        assert rows[0].embedding_dim == 2


def test_get_stored_embedding_returns_none_when_missing(embeddings_db_app):
    from embedding_store import get_stored_embedding

    with embeddings_db_app.app_context():
        assert get_stored_embedding(999999) is None


def test_embedding_storage_requires_database(monkeypatch):
    import config
    from embedding_store import get_stored_embedding

    monkeypatch.setattr(config.Config, "DATABASE_URL", None)
    with pytest.raises(RuntimeError):
        get_stored_embedding(1)


# --- incremental generation (backend/embedding_store.generate_embeddings) ------

def test_generate_embeddings_generates_missing_embeddings(embeddings_db_app, monkeypatch):
    import embedding_store
    from embedding_store import generate_embeddings, list_stored_embeddings

    # embedding_store.py does `from embeddings import embed_text` (a name
    # import), so the function must be patched on embedding_store itself -
    # patching embeddings.embed_text would leave embedding_store's own
    # already-bound reference untouched and this mock would never run.
    monkeypatch.setattr(embedding_store, "embed_text", lambda text, task_type=None: [0.1, 0.2])

    with embeddings_db_app.app_context():
        report = generate_embeddings()

        assert report["total_policies"] == 151
        assert len(report["generated"]) == 151
        assert report["skipped_unchanged"] == []
        assert report["failed"] == []
        assert len(list_stored_embeddings()) == 151


def test_generate_embeddings_skips_unchanged_on_second_run(embeddings_db_app, monkeypatch):
    import embedding_store
    from embedding_store import generate_embeddings

    call_count = {"n": 0}

    def _fake_embed(text, task_type=None):
        call_count["n"] += 1
        return [0.1, 0.2]

    monkeypatch.setattr(embedding_store, "embed_text", _fake_embed)

    with embeddings_db_app.app_context():
        first = generate_embeddings()
        assert len(first["generated"]) == 151
        assert call_count["n"] == 151

        second = generate_embeddings()
        assert second["generated"] == []
        assert len(second["skipped_unchanged"]) == 151
        # No new embedding calls at all for unchanged content.
        assert call_count["n"] == 151


def test_generate_embeddings_regenerates_only_stale_entries(embeddings_db_app, monkeypatch):
    import embedding_store
    from embedding_store import generate_embeddings, get_stored_embedding, upsert_embedding
    from database import db
    from policy_loader import load_policies

    monkeypatch.setattr(embedding_store, "embed_text", lambda text, task_type=None: [0.9, 0.9])

    with embeddings_db_app.app_context():
        generate_embeddings()

        # Simulate policy #1's JSON content having changed since its
        # embedding was generated, by corrupting only its stored hash -
        # generate_embeddings() must treat this exactly like a genuine
        # content change (it has no way to distinguish the two; the
        # hash IS the staleness signal).
        stale_id = load_policies()[0]["id"]
        row = get_stored_embedding(stale_id)
        row.content_hash = "deliberately-wrong-hash"
        db.session.add(row)
        db.session.commit()

        call_count = {"n": 0}

        def _counting_embed(text, task_type=None):
            call_count["n"] += 1
            return [0.5, 0.5]
        monkeypatch.setattr(embedding_store, "embed_text", _counting_embed)

        report = generate_embeddings()

        assert report["generated"] == [stale_id]
        assert len(report["skipped_unchanged"]) == 150
        assert call_count["n"] == 1


def test_generate_embeddings_dry_run_does_not_call_provider_or_write(embeddings_db_app, monkeypatch):
    import embedding_store
    from embedding_store import generate_embeddings, list_stored_embeddings

    def _fail_if_called(text, task_type=None):
        raise AssertionError("embed_text should not be called during a dry run")
    monkeypatch.setattr(embedding_store, "embed_text", _fail_if_called)

    with embeddings_db_app.app_context():
        report = generate_embeddings(dry_run=True)

        assert len(report["generated"]) == 151
        assert list_stored_embeddings() == []


def test_generate_embeddings_continues_past_individual_failures(embeddings_db_app, monkeypatch):
    import embedding_store
    from embeddings import EmbeddingError, build_embedding_document
    from embedding_store import generate_embeddings
    from policy_loader import load_policies

    failing_policy = load_policies()[2]
    fail_id = failing_policy["id"]
    failing_doc = build_embedding_document(failing_policy)

    def _sometimes_fail(text, task_type=None):
        # Exact match on the one policy's own embedding document -
        # not a substring check, which could false-positive against
        # an unrelated policy's text (e.g. a shared year number).
        if text == failing_doc:
            raise EmbeddingError("simulated provider failure")
        return [0.1, 0.1]
    monkeypatch.setattr(embedding_store, "embed_text", _sometimes_fail)

    with embeddings_db_app.app_context():
        report = generate_embeddings()

        assert len(report["failed"]) == 1
        assert report["failed"][0]["policy_id"] == fail_id
        # Every other policy still got embedded despite the one failure.
        assert len(report["generated"]) == 150


def test_generate_embeddings_requires_database(monkeypatch):
    import config
    from embedding_store import generate_embeddings

    monkeypatch.setattr(config.Config, "DATABASE_URL", None)
    with pytest.raises(RuntimeError):
        generate_embeddings()


# --- semantic search (backend/semantic_retrieval.py) ----------------------------

def test_semantic_search_returns_empty_list_for_empty_query(embeddings_db_app):
    from semantic_retrieval import semantic_search
    with embeddings_db_app.app_context():
        assert semantic_search("") == []
        assert semantic_search("   ") == []


def test_semantic_search_returns_empty_list_for_invalid_limit(embeddings_db_app):
    from semantic_retrieval import semantic_search
    with embeddings_db_app.app_context():
        assert semantic_search("agriculture", limit=0) == []
        assert semantic_search("agriculture", limit=-1) == []
        assert semantic_search("agriculture", limit="3") == []


def test_semantic_search_returns_empty_list_without_database_configured(monkeypatch):
    import config
    from semantic_retrieval import semantic_search

    monkeypatch.setattr(config.Config, "DATABASE_URL", None)
    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", "fake-key-for-test")
    assert semantic_search("agriculture") == []


def test_semantic_search_returns_empty_list_without_api_key(embeddings_db_app, monkeypatch):
    import config
    from semantic_retrieval import semantic_search

    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", None)
    with embeddings_db_app.app_context():
        assert semantic_search("agriculture") == []


def test_semantic_search_returns_empty_list_when_no_embeddings_stored(embeddings_db_app, monkeypatch):
    import config
    import semantic_retrieval
    from semantic_retrieval import semantic_search

    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(semantic_retrieval, "embed_text", lambda text, task_type=None: [1.0, 0.0])

    with embeddings_db_app.app_context():
        assert semantic_search("agriculture") == []


def test_semantic_search_finds_the_closest_stored_embedding(embeddings_db_app, monkeypatch):
    import config
    import semantic_retrieval
    from embedding_store import upsert_embedding
    from database import db
    from policy_loader import load_policies
    from semantic_retrieval import semantic_search

    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", "fake-key-for-test")

    policies = load_policies()
    target_id = policies[0]["id"]
    other_id = policies[1]["id"]

    with embeddings_db_app.app_context():
        upsert_embedding(target_id, [1.0, 0.0], content_hash="h1")
        upsert_embedding(other_id, [0.0, 1.0], content_hash="h2")
        db.session.commit()

        # The "query" embeds to something close to the target's stored
        # vector and far from the other's.
        monkeypatch.setattr(semantic_retrieval, "embed_text", lambda text, task_type=None: [0.9, 0.1])

        results = semantic_search("anything", limit=2)

        assert results[0]["id"] == target_id
        assert results[0]["semantic_score"] > results[1]["semantic_score"]
        assert "semantic_score" in results[0]
        assert "score" not in results[0]


def test_semantic_search_returns_empty_list_on_unexpected_database_error(embeddings_db_app, monkeypatch):
    """DATABASE_URL being configured is not a promise the database is
    actually reachable right now - a real connection/query failure
    (simulated here directly, rather than trying to engineer a genuine
    DB outage) must degrade to [] exactly like every other unavailable
    case, never propagate out of semantic_search() (see that function's
    docstring for why the except clause around this is deliberately
    broad)."""
    import config
    import embedding_store
    from semantic_retrieval import semantic_search

    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", "fake-key-for-test")

    def _boom():
        raise RuntimeError("simulated connection drop mid-query")
    monkeypatch.setattr(embedding_store, "list_stored_embeddings", _boom)

    with embeddings_db_app.app_context():
        assert semantic_search("agriculture") == []


def test_semantic_search_skips_embeddings_for_policies_no_longer_in_json(embeddings_db_app, monkeypatch):
    """An embedding row whose policy_id has no matching current-JSON
    policy is inert (see PolicyEmbedding's docstring) - must be silently
    skipped, never surfaced as a result with missing/garbage fields."""
    import config
    import semantic_retrieval
    from embedding_store import upsert_embedding
    from database import db
    from semantic_retrieval import semantic_search

    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", "fake-key-for-test")
    monkeypatch.setattr(semantic_retrieval, "embed_text", lambda text, task_type=None: [1.0, 0.0])

    with embeddings_db_app.app_context():
        upsert_embedding(policy_id=999999, vector=[1.0, 0.0], content_hash="h")
        db.session.commit()

        assert semantic_search("anything") == []


# --- hybrid retrieval (backend/retrieval.py's hybrid_retrieve) ------------------

def test_hybrid_retrieve_returns_deterministic_results_tagged(monkeypatch):
    import config
    from retrieval import hybrid_retrieve

    # No embedding infra configured for this test - deliberately, not an
    # ambient assumption (this codebase's convention, see no_db_client/
    # test_policies_api.py, is that tests always control
    # Config.DATABASE_URL explicitly rather than trusting whatever a
    # local .env happens to have set).
    monkeypatch.setattr(config.Config, "DATABASE_URL", None)

    results = hybrid_retrieve("ISRO Formation Policy 1969")
    assert len(results) >= 1
    assert results[0]["name"] == "ISRO Formation Policy, 1969"
    assert results[0]["retrieval_source"] == "deterministic"


def test_hybrid_retrieve_falls_back_to_deterministic_only_when_semantic_unavailable(monkeypatch):
    """No DATABASE_URL configured (explicitly forced off here - see the
    note in the previous test) means semantic search has nothing to add
    - hybrid_retrieve() must return exactly what retrieve_policies()
    alone would, which is guaranteed by semantic_search() itself
    returning [] in every unavailable case (see its own dedicated tests
    above)."""
    import config
    from retrieval import hybrid_retrieve, retrieve_policies

    monkeypatch.setattr(config.Config, "DATABASE_URL", None)

    query = "purple elephants dancing on the moon"
    assert hybrid_retrieve(query) == retrieve_policies(query)


def test_hybrid_retrieve_never_returns_fewer_than_deterministic_alone():
    from retrieval import hybrid_retrieve, retrieve_policies

    query = "agriculture farmers crop insurance scheme"
    deterministic_only = retrieve_policies(query)
    hybrid = hybrid_retrieve(query)
    assert len(hybrid) >= len(deterministic_only)
    # The deterministic results, in the same order, are always a prefix.
    assert hybrid[:len(deterministic_only)] == [
        dict(r, retrieval_source="deterministic") for r in deterministic_only
    ]


def test_hybrid_retrieve_invalid_limit_returns_empty_list_without_calling_semantic():
    from retrieval import hybrid_retrieve

    assert hybrid_retrieve("agriculture", limit=0) == []
    assert hybrid_retrieve("agriculture", limit=-1) == []


def test_hybrid_retrieve_adds_semantic_only_matches_without_duplicating_ids(embeddings_db_app, monkeypatch):
    """Full integration of the hybrid path: a query that deterministic
    Retrieval V2 finds nothing for, but semantic search (mocked) has a
    stored match for, should surface that match tagged
    retrieval_source="semantic" - and a policy id already found
    deterministically must never appear twice."""
    import config
    import semantic_retrieval
    from database import db
    from embedding_store import upsert_embedding
    from policy_loader import load_policies
    import retrieval

    monkeypatch.setattr(config.Config, "GEMINI_API_KEY", "fake-key-for-test")

    semantic_only_policy = load_policies()[5]

    with embeddings_db_app.app_context():
        upsert_embedding(semantic_only_policy["id"], [1.0, 0.0], content_hash="h")
        db.session.commit()

        monkeypatch.setattr(semantic_retrieval, "embed_text", lambda text, task_type=None: [1.0, 0.0])

        # A nonsense query deterministic Retrieval V2 will not match at all.
        results = retrieval.hybrid_retrieve("zzz qqq xyz blorptastic nonexistent", limit=3)

        assert len(results) == 1
        assert results[0]["id"] == semantic_only_policy["id"]
        assert results[0]["retrieval_source"] == "semantic"
        assert "semantic_score" in results[0]
