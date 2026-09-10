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
