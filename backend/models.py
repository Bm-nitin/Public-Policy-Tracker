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
