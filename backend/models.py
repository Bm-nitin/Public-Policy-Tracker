"""
Phase 3: User model.

The `users` table, defined via Flask-SQLAlchemy. This is the first model
in the app - Phase 2 deliberately defined none (see backend/database.py's
docstring for why). Only the fields Phase 3 explicitly requires are
present here:

    id, name, email, password_hash, email_verified,
    created_at, updated_at, last_login

No auth/session/verification-token/OTP fields are included - those belong
to later phases per the Phase 3 brief ("Do NOT implement ... those belong
to later phases").
"""

from datetime import datetime, timezone

from database import db
from verification_tokens import ensure_aware_utc
import json


def _utcnow():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(255), nullable=False, unique=True, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    email_verified = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    last_login = db.Column(db.DateTime(timezone=True), nullable=True)

    def to_public_dict(self):
        """The only representation of a User that should ever leave this
        process via an API response. Deliberately excludes password_hash
        and any future sensitive field - see auth_routes.py, which must
        always go through this method rather than serializing the model
        directly."""
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "email_verified": self.email_verified,
        }

    def __repr__(self):
        # Never include password_hash in repr/logs.
        return f"<User id={self.id} email={self.email!r}>"


class EmailVerificationToken(db.Model):
    """Phase 4. Stores only a SHA-256 hash of each verification token,
    never the raw token itself - see backend/verification_tokens.py for
    generation/hashing and its docstring for why SHA-256 (not Argon2id) is
    the right choice here.

    No ORM-level ForeignKey()/relationship() is used for user_id - it's a
    plain indexed integer column. The real foreign-key constraint (with
    ON DELETE CASCADE) is created in the migration, which is genuine SQL
    unaffected by the test environment. This keeps the model simple and
    keeps the offline test double (see tests/conftest.py) honest about
    what it actually implements, per the Phase 4 brief's "do not add
    unnecessary complexity."
    """

    __tablename__ = "email_verification_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)

    def is_valid(self, now=None):
        """True if this token has not been used and has not expired.
        Does not check anything about the associated user (e.g. whether
        they're already verified) - that's handled by the caller.

        expires_at is normalized via ensure_aware_utc() before comparing,
        because some database backends (SQLite, notably - see that
        function's docstring) return timezone-naive datetimes even for
        columns declared DateTime(timezone=True). `now` is always
        timezone-aware (datetime.now(timezone.utc)); normalizing
        expires_at up to match it, rather than stripping tzinfo from
        `now`, keeps the comparison exact rather than silently less
        precise.
        """
        now = now or _utcnow()
        if self.used_at is not None:
            return False
        expires_at = ensure_aware_utc(self.expires_at)
        return expires_at is not None and expires_at > now

    def __repr__(self):
        # Never include token_hash in repr/logs - it's a hash, not the
        # secret itself, but there's no reason to print it either.
        return f"<EmailVerificationToken id={self.id} user_id={self.user_id}>"


class UserSession(db.Model):
    """Phase 5. A genuine server-side session: the row here is the
    authoritative source of truth for whether a session is valid, and is
    independently revocable (logout just sets revoked_at) - unlike a
    Flask signed-cookie session, which is client-held and can only be
    invalidated by rotating the app's secret for everyone at once.

    Same pattern as EmailVerificationToken (see backend/sessions.py for
    generation/hashing): only session_token_hash is ever stored, never
    the raw token - the raw token exists only in the HttpOnly cookie on
    the client.
    """

    __tablename__ = "user_sessions"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    session_token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    revoked_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def is_valid(self, now=None):
        """Same shape as EmailVerificationToken.is_valid() - see that
        docstring for why expires_at is normalized via ensure_aware_utc()
        before comparing (SQLite round-trips timezone-aware columns as
        naive)."""
        now = now or _utcnow()
        if self.revoked_at is not None:
            return False
        expires_at = ensure_aware_utc(self.expires_at)
        return expires_at is not None and expires_at > now

    def __repr__(self):
        # Never include session_token_hash in repr/logs.
        return f"<UserSession id={self.id} user_id={self.user_id}>"


class PasswordResetToken(db.Model):
    """Phase 6. Same pattern as EmailVerificationToken (Phase 4) and
    UserSession (Phase 5): only a SHA-256 hash of the raw token is ever
    stored - see verification_tokens.generate_password_reset_token().

    used_at does double duty as both "this token was successfully used to
    reset a password" and "this token was superseded by a newer request
    before it was ever used" - the same established convention
    resend_verification() already uses for EmailVerificationToken in
    Phase 4, kept consistent here rather than adding a separate
    invalidated_at column for a distinction the application never needs
    to act on differently (either way, the token must never work again).
    """

    __tablename__ = "password_reset_tokens"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, nullable=False, index=True)
    token_hash = db.Column(db.String(64), nullable=False, unique=True, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)
    used_at = db.Column(db.DateTime(timezone=True), nullable=True)

    def is_valid(self, now=None):
        now = now or _utcnow()
        if self.used_at is not None:
            return False
        expires_at = ensure_aware_utc(self.expires_at)
        return expires_at is not None and expires_at > now

    def __repr__(self):
        # Never include token_hash in repr/logs.
        return f"<PasswordResetToken id={self.id} user_id={self.user_id}>"


# NOTE: the Policy model (policies table) that previously lived here has
# been retired - policy data is now sourced entirely from data/*.json via
# backend/policy_loader.py / backend/policy_service.py (see those modules'
# docstrings). It was used ONLY for policy storage, never for anything
# authentication-related, so removing it does not touch User/
# EmailVerificationToken/UserSession/PasswordResetToken above. The historical
# migrations that created/altered the `policies` table remain in
# migrations/versions/ unchanged (removing/rewriting historical migrations
# was explicitly out of scope) - they simply have no corresponding model
# here any more, the same as any other retired table would.


class PolicyEmbedding(db.Model):
    """Phase 10: a vector SEARCH INDEX, not a second copy of policy data.
    data/*.json (via policy_loader.py) remains the sole source of truth
    for policy content - this table exists only so semantic search has
    somewhere to persist embeddings between requests/restarts, isolated
    from every authentication table above (no FK, no shared table, no
    schema coupling) and, just as deliberately, isolated from ever
    becoming a second Policy model: it stores nothing but an id, a
    vector, and bookkeeping to detect staleness - never name/category/
    sub_category/change/impact.

    policy_id is a plain Integer, not a ForeignKey: there is no
    `policies` table to reference any more (see the NOTE above), and
    policy_id's only real "foreign key" is policy_loader.py's stable,
    deterministically-assigned id (see that module's docstring). A
    row here whose policy_id no longer exists in the current JSON
    dataset (a policy was removed) is inert - it simply never gets
    matched against any live policy - rather than being a referential-
    integrity violation, since nothing in this schema can express that
    constraint against a JSON file anyway.

    embedding is stored as JSON text (a flat list[float]), not a
    pgvector `vector` column - see backend/embeddings.py's module
    docstring and the Phase 10 design report for why: this table works
    on any PostgreSQL version/plan with zero extension dependency, and
    at 151 rows a brute-force Python cosine-similarity scan (see
    backend/semantic_retrieval.py) is already sub-millisecond, so
    pgvector's ANN indexing would add a hard extension dependency for
    zero real performance benefit at this scale. If the dataset ever
    grows enough for that tradeoff to flip, migrating this one column
    to pgvector's `vector` type is a schema change to this table alone
    - not "a completely different architecture" (see the Phase 10
    brief's own phrasing for exactly this scenario).

    content_hash is a SHA-256 hex digest of the exact text that was
    embedded (backend/embeddings.py's build_embedding_document() +
    content_fingerprint()) - comparing this against a freshly computed
    hash for the current JSON content is the entire staleness check
    (see backend/embeddings.py's content_fingerprint() docstring); no
    reference back to the JSON content itself is stored here.
    """
    __tablename__ = "policy_embeddings"

    id = db.Column(db.Integer, primary_key=True)
    policy_id = db.Column(db.Integer, nullable=False, unique=True, index=True)
    embedding = db.Column(db.Text, nullable=False)
    embedding_dim = db.Column(db.Integer, nullable=False)
    content_hash = db.Column(db.String(64), nullable=False)
    model = db.Column(db.String(120), nullable=False)
    created_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def __repr__(self):
        return f"<PolicyEmbedding policy_id={self.policy_id} model={self.model!r}>"


MAX_TITLE_LENGTH = 200
MAX_MESSAGE_CONTENT_LENGTH = 8000
ALLOWED_MESSAGE_ROLES = ("user", "assistant")


class Conversation(db.Model):
    """Phase 12 - one persisted chat history thread, owned by exactly one
    User. Deliberately does not store or duplicate any policy content -
    see Message.metadata_json's docstring for the same discipline applied
    to individual messages. `user_id` has no ORM-level cascade configured
    (deleting a User does not currently delete their conversations - out
    of scope for this phase, which only asked for conversation-delete-
    cascades-messages; see Message's FK for that one). Ownership is
    always re-checked at the query layer in
    backend/conversations_routes.py (see that module's docstring) - the
    FK constraint here is a data-integrity backstop, never the
    authorization mechanism itself."""
    __tablename__ = "conversations"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("users.id"), nullable=False, index=True
    )
    title = db.Column(db.String(MAX_TITLE_LENGTH), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow,
        onupdate=_utcnow, index=True,
    )

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self):
        return f"<Conversation id={self.id} user_id={self.user_id}>"


class Message(db.Model):
    """Phase 12 - one message within a Conversation. `role` is
    constrained to models.ALLOWED_MESSAGE_ROLES at the application layer
    (backend/conversations_routes.py validates before ever constructing
    a Message - see that module) rather than a DB-level CHECK constraint,
    matching this codebase's existing validation style (e.g.
    backend/policies_routes.py's pagination parameter validation) and
    keeping this portable across the offline test shim, which has no
    CHECK-constraint support, and real PostgreSQL alike.

    `metadata_json` (mapped from the conceptual "metadata" field the
    Phase 12 brief describes) is stored as a JSON-encoded TEXT column,
    same representation choice as PolicyEmbedding.embedding - plain,
    portable, no JSON column type dependency. It is for LIGHTWEIGHT,
    non-authoritative bookkeeping only (e.g. {"retrieval_mode": "hybrid",
    "policy_ids": [12, 45, 78]} - which policies informed an assistant
    reply) - it must never contain a duplicated copy of policy content
    (name/category/sub_category/change/impact). data/*.json via
    backend/policy_loader.py remains the only source of truth for that;
    a policy_id here is only ever meaningful as a pointer back to it, and
    a stale/missing id (a policy later removed from the JSON dataset) is
    inert here for exactly the same reason it's inert for PolicyEmbedding
    - see that model's docstring."""
    __tablename__ = "messages"

    id = db.Column(db.Integer, primary_key=True)
    conversation_id = db.Column(
        db.Integer, db.ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    role = db.Column(db.String(20), nullable=False)
    content = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow, index=True)
    metadata_json = db.Column(db.Text, nullable=True)

    def to_dict(self):
        metadata = None
        if self.metadata_json:
            try:
                metadata = json.loads(self.metadata_json)
            except (TypeError, ValueError):
                # Never let a malformed stored value break a listing -
                # see conversations_routes.py's write-side validation,
                # which is what actually prevents this from happening
                # for any row written through the API; this is only a
                # defensive read-side fallback.
                metadata = None
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "role": self.role,
            "content": self.content,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "metadata": metadata,
        }

    def __repr__(self):
        return f"<Message id={self.id} conversation_id={self.conversation_id} role={self.role!r}>"


class SavedPolicy(db.Model):
    """Phase 13 - a bookmark: "this user saved this policy", nothing
    more. Exactly like PolicyEmbedding (Phase 10) and Message.metadata_json
    (Phase 12), this table stores ONLY a relationship/pointer, never a
    copy of policy content - data/*.json (via backend/policy_loader.py /
    backend/policy_service.py) remains the sole source of truth for
    name/category/sub_category/change/impact. backend/saved_policies_service.py
    always resolves policy_id against the CURRENT JSON data at read
    time, never against anything cached here, so a saved policy's
    displayed details automatically stay in sync with the JSON dataset
    (including reflecting an edit to that policy's data, or - see that
    module's docstring - going stale/absent if the policy is later
    removed from the JSON entirely).

    policy_id is a plain Integer, not a ForeignKey - same reasoning as
    PolicyEmbedding.policy_id (see that model's docstring): there is no
    `policies` table any more to reference, and this column's only real
    "foreign key" is policy_loader.py's stable, deterministically-
    assigned id.

    The (user_id, policy_id) unique constraint is the actual
    duplicate-prevention mechanism at the database level - genuinely
    enforced, not just documentation (see
    backend/saved_policies_service.py's save_policy() for how
    application code handles a race that reaches this constraint
    anyway, and tests/conftest.py's _ShimUniqueConstraint, which already
    enforces composite UNIQUE constraints for this same reason the
    now-retired Policy(name, sector) constraint needed to)."""
    __tablename__ = "saved_policies"
    __table_args__ = (
        db.UniqueConstraint("user_id", "policy_id", name="uq_saved_policies_user_policy"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    policy_id = db.Column(db.Integer, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "policy_id": self.policy_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self):
        return f"<SavedPolicy id={self.id} user_id={self.user_id} policy_id={self.policy_id}>"
