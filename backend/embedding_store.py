"""
Phase 10 - embedding storage (PostgreSQL, via backend/models.py's
PolicyEmbedding) and incremental generation.

Every function here that touches the database requires an active Flask
app context (the same convention backend/import_policies.py's now-retired
DB-writing half used) and Config.DATABASE_URL to be configured - this
module raises a clear RuntimeError rather than a confusing SQLAlchemy
error if called without either, and generate_embeddings() in particular
checks this itself so a caller (the CLI command below, or a test) gets
one clean failure mode.

Semantic search (backend/semantic_retrieval.py) is a read-only consumer
of list_stored_embeddings() - it never writes here and never calls the
embedding-generation functions below.
"""

from config import Config
from database import db
from embeddings import (
    EmbeddingError,
    build_embedding_document,
    content_fingerprint,
    embed_text,
    serialize_embedding,
)
from models import PolicyEmbedding
from policy_loader import load_policies


def _require_database():
    if not Config.DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not configured - embedding storage requires a database."
        )


def get_stored_embedding(policy_id):
    _require_database()
    return PolicyEmbedding.query.filter_by(policy_id=policy_id).first()


def list_stored_embeddings():
    """Every currently stored embedding row - used by
    backend/semantic_retrieval.py's brute-force similarity scan (see
    that module for why no vector-index query is needed at 151 rows)."""
    _require_database()
    return PolicyEmbedding.query.all()


def upsert_embedding(policy_id, vector, content_hash, model=None):
    """Creates or updates the one embedding row for policy_id (policy_id
    is unique - see PolicyEmbedding's schema). Does not commit - the
    caller (generate_embeddings() below) commits once per batch, not once
    per row, so an interrupted run leaves either fully-committed rows or
    none, never a half-written row."""
    _require_database()
    model = model or Config.EMBEDDING_MODEL

    row = PolicyEmbedding.query.filter_by(policy_id=policy_id).first()
    if row is None:
        row = PolicyEmbedding(policy_id=policy_id)

    row.embedding = serialize_embedding(vector)
    row.embedding_dim = len(vector)
    row.content_hash = content_hash
    row.model = model
    db.session.add(row)
    return row


def generate_embeddings(dry_run=False):
    """Incremental embedding generation: for every policy in the current
    data/*.json dataset (via policy_loader.load_policies()), embed it
    ONLY if there is no stored embedding for its id yet, or the stored
    content_hash no longer matches the policy's current content (see
    backend/embeddings.py's content_fingerprint() docstring for exactly
    what "stale" means here) - an unchanged policy's embedding is never
    regenerated, and this never runs automatically on app startup (see
    the Phase 10 brief's performance requirement; this is CLI-only, same
    pattern as the retired `flask import-policies` command).

    Returns a report dict: {"total_policies", "generated": [policy_id,...],
    "skipped_unchanged": [policy_id,...], "failed": [{"policy_id",
    "error"},...]}. A single policy's embedding failure (provider error,
    network issue) is recorded in "failed" and does NOT abort the run -
    every other policy is still attempted, unlike backend/import_policies.py's
    former all-or-nothing transaction (embeddings are independent rows
    with no cross-row constraint, so partial progress is safe and useful
    here in a way it wasn't for the old policies-table import).

    dry_run=True computes and reports what WOULD be generated/skipped
    without calling the embedding provider or writing anything - useful
    for estimating provider cost/quota before actually spending it.
    """
    _require_database()

    report = {"total_policies": 0, "generated": [], "skipped_unchanged": [], "failed": []}
    policies = load_policies()
    report["total_policies"] = len(policies)

    for policy in policies:
        doc = build_embedding_document(policy)
        fingerprint = content_fingerprint(doc)

        existing = get_stored_embedding(policy["id"])
        if existing is not None and existing.content_hash == fingerprint:
            report["skipped_unchanged"].append(policy["id"])
            continue

        if dry_run:
            report["generated"].append(policy["id"])
            continue

        try:
            vector = embed_text(doc, task_type="RETRIEVAL_DOCUMENT")
        except EmbeddingError as e:
            report["failed"].append({"policy_id": policy["id"], "error": str(e)})
            continue

        upsert_embedding(policy["id"], vector, fingerprint)
        report["generated"].append(policy["id"])

    if not dry_run and (report["generated"]):
        db.session.commit()

    return report
