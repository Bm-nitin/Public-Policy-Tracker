"""
Phase 12 - conversation/message business logic.

Every function that touches a specific conversation (get_owned_conversation,
list_messages, add_message, delete_conversation) takes `user_id` as its
first argument and enforces ownership as the FIRST thing it does, before
any other work - this is the one place that guarantee lives, so
backend/conversations_routes.py and app.py's /chat route both get it for
free rather than having to remember to check it themselves at every call
site. `user_id` must always come from sessions.get_current_user() (a
server-validated session), never from a client-supplied value anywhere
in a request body/query string/header - see conversations_routes.py.

ConversationNotFoundError is raised identically whether a conversation_id
does not exist at all OR exists but belongs to a different user -
callers (conversations_routes.py) must map it to a single generic 404 in
both cases, so a client can never distinguish "not yours" from "doesn't
exist" (the Phase 12 brief's explicit "do not expose whether another
user's conversation exists" requirement).
"""

import json
from datetime import timedelta

from database import db
from models import (
    ALLOWED_MESSAGE_ROLES,
    MAX_MESSAGE_CONTENT_LENGTH,
    MAX_TITLE_LENGTH,
    Conversation,
    Message,
    _utcnow,
)
from verification_tokens import ensure_aware_utc

DEFAULT_CONVERSATIONS_PER_PAGE = 20
MAX_CONVERSATIONS_PER_PAGE = 100

DEFAULT_MESSAGES_PER_PAGE = 50
MAX_MESSAGES_PER_PAGE = 200

# Deliberately conservative - metadata is for lightweight bookkeeping
# only (see models.Message's docstring), never a duplicated policy
# record, so a generous-but-bounded cap catches accidental misuse
# (e.g. someone pasting a full policy dict in) without needing to
# understand the metadata's shape to reject it.
MAX_METADATA_SERIALIZED_LENGTH = 2000

DEFAULT_TITLE = "New Conversation"
TITLE_ELLIPSIS = "..."


class ConversationNotFoundError(Exception):
    """Conversation does not exist, or does not belong to the requesting
    user - see module docstring for why these two cases are always
    indistinguishable to a caller."""


class ValidationError(Exception):
    """Message is always safe to return to the client directly (a
    validation error describes what was wrong with THEIR input, never
    internal state) - see conversations_routes.py, which does exactly
    that."""


def generate_title_from_message(first_message):
    """A safe, deterministic title derived from the first user message -
    no Gemini call, per the Phase 12 brief ("Do not use Gemini solely to
    generate titles"). Collapses whitespace, then truncates to
    MAX_TITLE_LENGTH, preferring a whole-word boundary when one is
    reasonably close to the cutoff so a title doesn't end mid-word.
    Falls back to DEFAULT_TITLE for an empty/whitespace-only message."""
    if not first_message or not first_message.strip():
        return DEFAULT_TITLE

    collapsed = " ".join(first_message.split())
    limit = MAX_TITLE_LENGTH - len(TITLE_ELLIPSIS)
    if len(collapsed) <= MAX_TITLE_LENGTH:
        return collapsed

    truncated = collapsed[:limit]
    last_space = truncated.rfind(" ")
    # Only break on a word boundary if it doesn't throw away a large
    # chunk of the available length - otherwise a short first word
    # followed by one very long word would produce a near-empty title.
    if last_space > limit * 0.6:
        truncated = truncated[:last_space]
    return truncated.rstrip() + TITLE_ELLIPSIS


def _validate_title(title):
    if title is None:
        return None
    if not isinstance(title, str):
        raise ValidationError("title must be a string.")
    title = title.strip()
    if not title:
        return None
    if len(title) > MAX_TITLE_LENGTH:
        raise ValidationError(f"title must be {MAX_TITLE_LENGTH} characters or fewer.")
    return title


def create_conversation(user_id, title=None, first_message=None):
    """Creates and persists a new Conversation for user_id. `title` wins
    if provided (validated - see _validate_title()); otherwise, if
    `first_message` is given, a deterministic title is derived from it
    (generate_title_from_message()); otherwise DEFAULT_TITLE."""
    clean_title = _validate_title(title)
    if clean_title is None:
        clean_title = generate_title_from_message(first_message) if first_message else DEFAULT_TITLE

    conversation = Conversation(user_id=user_id, title=clean_title)
    db.session.add(conversation)
    db.session.commit()
    return conversation


def list_conversations(user_id, page=1, per_page=DEFAULT_CONVERSATIONS_PER_PAGE):
    """Newest-updated-first (per the Phase 12 brief), id DESC as a
    deterministic tie-break for two conversations updated in the same
    instant. Filters by user_id FIRST, as a query predicate - a
    conversation belonging to another user is never even fetched, let
    alone returned (see module docstring)."""
    query = (
        Conversation.query
        .filter_by(user_id=user_id)
        .order_by(Conversation.updated_at)
    )
    all_matching = query.all()
    # The offline test shim's order_by() only supports a single ascending
    # column (see tests/conftest.py) - sort descending here in Python,
    # which works identically against real SQLAlchemy/PostgreSQL too and
    # keeps this function's behavior verified by the same test suite
    # either way. Real usage (151 policies, a handful of conversations
    # per user) never makes this a performance concern.
    all_matching.sort(key=lambda c: (c.updated_at, c.id), reverse=True)

    total = len(all_matching)
    start = (page - 1) * per_page
    end = start + per_page
    items = [c.to_dict() for c in all_matching[start:end]]
    return {"items": items, "total": total}


def get_owned_conversation(user_id, conversation_id):
    """Returns the Conversation, or raises ConversationNotFoundError -
    see module docstring for why a wrong-owner conversation raises the
    exact same exception as a nonexistent one."""
    conversation = Conversation.query.filter_by(id=conversation_id, user_id=user_id).first()
    if conversation is None:
        raise ConversationNotFoundError(str(conversation_id))
    return conversation


def list_messages(user_id, conversation_id, page=1, per_page=DEFAULT_MESSAGES_PER_PAGE):
    """Verifies ownership first (raises ConversationNotFoundError,
    exactly as get_owned_conversation() does, if not). Oldest-first
    within the conversation (per the Phase 12 brief - normal chat history
    reads top-to-bottom), id ASC as a deterministic tie-break."""
    get_owned_conversation(user_id, conversation_id)  # ownership check; raises if not owned

    all_matching = (
        Message.query
        .filter_by(conversation_id=conversation_id)
        .order_by(Message.created_at)
        .all()
    )
    all_matching.sort(key=lambda m: (m.created_at, m.id))

    total = len(all_matching)
    start = (page - 1) * per_page
    end = start + per_page
    items = [m.to_dict() for m in all_matching[start:end]]
    return {"items": items, "total": total}


def _validate_role(role):
    if role not in ALLOWED_MESSAGE_ROLES:
        raise ValidationError(
            f"role must be one of {list(ALLOWED_MESSAGE_ROLES)}."
        )
    return role


def _validate_content(content):
    if not isinstance(content, str):
        raise ValidationError("content must be a string.")
    if not content.strip():
        raise ValidationError("content must not be empty.")
    if len(content) > MAX_MESSAGE_CONTENT_LENGTH:
        raise ValidationError(
            f"content must be {MAX_MESSAGE_CONTENT_LENGTH} characters or fewer."
        )
    # Content is stored and later displayed as plain text/JSON - never
    # evaluated, executed, or treated as an instruction to this backend
    # (see the Phase 12 brief's explicit requirement). No further
    # sanitization is performed here for that reason: there is nothing
    # for this string to do except be stored and echoed back as inert
    # data, the same treatment backend/grounding.py already gives
    # retrieved policy text and the same treatment
    # backend/gemini_service.py gives the user's own query (see that
    # module's docstring on why data and instructions are never the
    # same channel).
    return content


def _validate_metadata(metadata):
    """None is always valid (no metadata). Otherwise must be a plain
    JSON-serializable dict, within MAX_METADATA_SERIALIZED_LENGTH once
    serialized - see that constant's docstring for why this is
    deliberately conservative."""
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise ValidationError("metadata must be a JSON object.")
    try:
        serialized = json.dumps(metadata)
    except (TypeError, ValueError) as e:
        raise ValidationError("metadata must be JSON-serializable.") from e
    if len(serialized) > MAX_METADATA_SERIALIZED_LENGTH:
        raise ValidationError(
            f"metadata must be {MAX_METADATA_SERIALIZED_LENGTH} characters or fewer once serialized."
        )
    return serialized


def add_message(user_id, conversation_id, role, content, metadata=None):
    """Validates role/content/metadata (raises ValidationError - see that
    class's docstring on safety of surfacing its message to the client),
    verifies ownership (raises ConversationNotFoundError), persists the
    Message, and bumps the parent Conversation's updated_at (so
    list_conversations()'s "newest updated first" ordering reflects
    actual activity, not just conversation-creation time) - all in one
    commit, so a caller never observes a message written without its
    parent conversation's updated_at also having moved.

    ROOT CAUSE, NOW CONFIRMED (this is the FIFTH version of this
    function - see git history for the previous four; the fourth's own
    monotonic-timestamp fix was correct in principle but crashed
    outright with `TypeError: can't compare offset-naive and
    offset-aware datetimes` the moment real PostgreSQL/SQLAlchemy on
    Windows actually exercised it):

    SQLite - this project's own test database (tests/conftest.py's
    db_test_app fixture uses "sqlite:///:memory:"; this is real
    SQLAlchemy against a real sqlite3 driver in that environment, not
    this repository's offline shim) - has no native timezone-aware
    storage. A column declared DateTime(timezone=True) still round-trips
    through it, but SQLAlchemy's sqlite dialect does not restore tzinfo
    on read: a value written as timezone-aware UTC comes back
    timezone-NAIVE the next time it's queried fresh from the database
    (PostgreSQL, this app's real production database, does not have
    this limitation and returns the offset correctly - this is
    SQLite-specific). This exact behavior - some Conversation.updated_at
    reads coming back naive, others (freshly computed via _utcnow(),
    never round-tripped) staying aware - is also the real explanation
    for every previous version's "identical microsecond timestamps"
    symptom: Conversation.query.filter_by(...).first() calls throughout
    this module (get_owned_conversation() included) return the
    naive-on-SQLite value, so a caller comparing two such reads was
    never actually comparing what it thought it was.

    The fix: ensure_aware_utc() (backend/verification_tokens.py) -
    already this codebase's established remedy for exactly this pattern
    (see backend/auth_routes.py's token-expiry checks) - normalizes
    conversation.updated_at before it is ever compared against a fresh
    _utcnow() value. Every datetime this application writes is already
    UTC (see models._utcnow()), so a naive value read back always means
    "naive but actually UTC" - safe to reattach tzinfo=utc directly,
    never a guess. With both sides guaranteed timezone-aware, the
    monotonic-advancement logic below (introduced in the fourth version,
    unchanged here) works as designed: if the freshly-computed timestamp
    isn't STRICTLY later than the conversation's current updated_at
    (whether from genuine clock-resolution coarseness or, now that the
    TypeError is gone, the ordinary case of comparing two valid aware
    datetimes), advance by the smallest possible increment - one
    microsecond - past the current value instead. This guarantees
    monotonic advancement deterministically, on every platform, database
    backend, and clock resolution, without ever sleeping (the Phase 12
    brief that first reported this bug explicitly forbids sleep()-based
    fixes for exactly this reason: they trade a reproducible bug for an
    unreproducible flake under different timing).

    backend/models.py's to_dict() methods received the same
    ensure_aware_utc() treatment (via that module's _iso() helper) for
    the same underlying reason, applied to serialization rather than
    comparison: an API response's timestamp must never silently omit
    its UTC offset depending on which database backend happened to
    handle the request (see the Phase 14 brief's explicit "do not
    introduce naive datetime handling" requirement, which this bug
    would otherwise have quietly violated for every conversation/
    message/saved-policy/user timestamp this application returns, not
    just the one this function writes).

    See tests/test_chat_history.py's
    test_add_message_bumps_conversation_updated_at() and
    test_add_message_advances_updated_at_even_when_clock_does_not() for
    the regression coverage - the latter forces the exact failure
    condition directly (a mocked _utcnow() returning a value equal to
    the conversation's current updated_at) so it does not depend on
    reproducing either the clock-coarseness or the naive/aware
    discrepancy for real.

    db.session.expire_all() is kept below - correct, harmless practice
    for any future caller that might reuse `conversation` (or any other
    already-loaded object) after this bulk update, even though it was
    never itself the fix for either of this function's two real bugs.
    """
    clean_role = _validate_role(role)
    clean_content = _validate_content(content)
    clean_metadata = _validate_metadata(metadata)

    conversation = get_owned_conversation(user_id, conversation_id)

    message = Message(
        conversation_id=conversation.id, role=clean_role,
        content=clean_content, metadata_json=clean_metadata,
    )
    db.session.add(message)

    now = _utcnow()
    current_updated_at = ensure_aware_utc(conversation.updated_at) if conversation.updated_at is not None else None
    if current_updated_at is not None and now <= current_updated_at:
        # Clock resolution wasn't fine enough to distinguish "now" from
        # the conversation's current updated_at - advance by the
        # smallest possible increment instead of trusting the clock.
        # See docstring above.
        now = current_updated_at + timedelta(microseconds=1)
    Conversation.query.filter_by(id=conversation.id).update({"updated_at": now})

    db.session.commit()
    db.session.expire_all()

    return message


def delete_conversation(user_id, conversation_id):
    """Verifies ownership (raises ConversationNotFoundError), deletes
    every message in the conversation, then the conversation itself, in
    one commit - never leaves an orphaned message even on a database
    that doesn't enforce messages.conversation_id's ON DELETE CASCADE
    (e.g. this project's offline test shim - see
    tests/conftest.py's _ShimForeignKey docstring), and never partially
    deletes (some messages gone, conversation and the rest still
    present) if something fails partway, since nothing is committed
    until every delete in this function has been staged."""
    conversation = get_owned_conversation(user_id, conversation_id)

    Message.query.filter_by(conversation_id=conversation.id).delete()
    db.session.delete(conversation)
    db.session.commit()
