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


class Policy(db.Model):
    """Phase 7 (finalized schema, approved). Database-backed mirror of
    the curated JSON dataset in data/*.json, loaded today by
    backend/policy_loader.py.

    Field names/types are taken directly from what the JSON actually
    contains - every one of the 151 source records has exactly these 5
    fields (name, category, sub_category, change, impact), verified by
    inspecting all 15 files before writing this model. Fields suggested
    in earlier drafts of the Phase 7 brief that do NOT exist in the
    source data (description, eligibility, benefits, application_process,
    documents_required, official_link) are deliberately NOT columns here
    - adding them now would mean 151 permanently-NULL columns invented
    ahead of any real data. Adding any of them later is a trivial,
    low-risk nullable-column migration once real data for them exists.

    (name, sector) is NOT globally unique on name alone: 8 real policy
    names legitimately appear under two different sectors with distinct,
    sector-specific category/change/impact text (e.g. "Make in India
    Initiative, 2014" under both economy and industry_business) - these
    are not duplicates, they're two different curated write-ups of the
    same real-world policy. The natural identity is the (name, sector)
    pair, which IS unique across all 151 current records (verified) and
    is enforced here as a composite UNIQUE constraint.

    source_file (approved schema change - replaces the earlier
    source_json draft) stores the original JSON filename (e.g.
    "agriculture.json") for provenance - where a given row came from -
    rather than a full copy of the source record. The full record isn't
    retained because there is no concrete, demonstrated case of
    information loss: all 5 source keys map 1:1 to columns above, so a
    raw-JSON safety-net column has no actual data to protect right now.
    If a future JSON structure introduces new fields, that's a
    deliberate, separate schema migration - not something a speculative
    blob column should silently paper over.

    This model is not wired into chatbot.py/policy_loader.py in this
    phase - see backend/policy_service.py for the parallel DB-backed
    loader, and Phase 9 for when retrieval actually switches over.
    """

    __tablename__ = "policies"
    __table_args__ = (
        db.UniqueConstraint("name", "sector", name="uq_policies_name_sector"),
    )

    id = db.Column(
        # SQLite only treats a column as its autoincrementing rowid alias
        # when it's declared literally as "INTEGER PRIMARY KEY" - a bare
        # BigInteger primary key compiles to "BIGINT", which SQLite does
        # NOT alias to rowid, so no autoincrement happens there and every
        # insert fails with "NOT NULL constraint failed: policies.id".
        # with_variant() is the standard SQLAlchemy fix: PostgreSQL still
        # gets genuine BIGINT (the approved production type, unchanged),
        # and only the sqlite dialect (used for local/test databases)
        # gets Integer instead. This is a per-dialect DDL type choice,
        # not a weakening of the production schema.
        db.BigInteger().with_variant(db.Integer, "sqlite"),
        primary_key=True,
    )
    name = db.Column(db.String(255), nullable=False, index=True)
    sector = db.Column(db.String(64), nullable=False, index=True)
    category = db.Column(db.String(120), nullable=False, index=True)
    sub_category = db.Column(db.String(120), nullable=False, index=True)
    change = db.Column(db.Text, nullable=False)
    impact = db.Column(db.Text, nullable=False)
    source_file = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=_utcnow)
    updated_at = db.Column(
        db.DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    def to_dict(self):
        """Same key shape backend/policy_loader.py already produces
        (name, category, sub_category, change, impact, sector) - so any
        code written against the JSON loader's output (chatbot.py today)
        would work unchanged against this, without needing to know or
        care where the dict came from. Intentionally excludes id/
        source_file/timestamps - chatbot.py's existing code never uses
        them and this keeps the dict shape identical to the JSON
        loader's, not a superset that could behave differently."""
        return {
            "name": self.name,
            "category": self.category,
            "sub_category": self.sub_category,
            "change": self.change,
            "impact": self.impact,
            "sector": self.sector,
        }

    def __repr__(self):
        return f"<Policy id={self.id} name={self.name!r} sector={self.sector!r}>"
