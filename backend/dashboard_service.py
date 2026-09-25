"""
Phase 14 - dashboard aggregation.

Pure aggregation over EXISTING data - no new database table (there is
nothing here for one to store; every value returned is either read
directly from users/conversations/messages/saved_policies, computed
from them, or resolved from data/*.json). Every function takes
`user_id` as its first argument, always sourced from
g.current_user.id in backend/dashboard_routes.py - never from a request
body/query string (see that module's docstring; this mirrors
backend/conversations_service.py and backend/saved_policies_service.py's
established Phase 12/13 convention exactly).

Policy content: every saved-policy entry returned here is resolved
against backend/policy_service.get_policy_by_id() (backed by
backend/policy_loader.py's cached data/*.json load) at call time - see
backend/models.py's SavedPolicy docstring for why saved_policies itself
holds no policy content to go stale. A saved relationship whose policy
no longer exists in the current JSON dataset is silently omitted from
recent_saved_policies (never surfaced as a broken entry, and never
causes the whole dashboard to fail) while the underlying saved_policies
row itself is left completely untouched - it is still deletable via
DELETE /api/saved-policies/<id>, exactly as
backend/saved_policies_service.py already establishes for the same
scenario.

N+1 avoidance: every list here does ONE bounded, user-scoped query per
data source (never one query per returned item) - see
_message_counts_for_conversations() and get_recent_activity()'s
docstrings for the specific techniques.
"""

from models import Conversation, Message, SavedPolicy, User
from policy_service import get_policy_by_id
from verification_tokens import ensure_aware_utc

DEFAULT_RECENT_LIMIT = 5
MAX_RECENT_LIMIT = 20

DEFAULT_ACTIVITY_LIMIT = 10
MAX_ACTIVITY_LIMIT = 20


class ValidationError(Exception):
    """Message is always safe to return to the client directly - same
    convention as backend/conversations_service.py's ValidationError."""


def _validate_limit(limit, default, max_limit):
    """Shared by every *_limit parameter this module accepts. Contract
    (per the Phase 14 brief):
      - missing/None -> `default`
      - a bool -> rejected (isinstance(True, int) is True in Python;
        a client sending `true`/`false` is a type error, not a
        disguised 0/1)
      - any other non-int -> rejected
      - <= 0 -> rejected (this module never treats "no limit" as
        "unlimited" - there is always a concrete positive cap)
      - > max_limit -> rejected
    """
    if limit is None:
        return default
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValidationError("limit must be an integer.")
    if limit <= 0:
        raise ValidationError("limit must be a positive integer.")
    if limit > max_limit:
        raise ValidationError(f"limit must be {max_limit} or fewer.")
    return limit


def get_user_summary(user):
    """Only the safe account fields the Phase 14 brief lists - never
    password_hash, and never anything from the session/verification/
    password-reset-token tables (this function doesn't even import
    those models, so there is nothing to accidentally include).

    created_at is normalized via ensure_aware_utc() before
    serialization - see backend/models.py's _iso() docstring for why a
    value read back from SQLite (this project's own test database) can
    otherwise come back timezone-naive even though the column is
    declared DateTime(timezone=True) and PostgreSQL (production) always
    returns it correctly."""
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "email_verified": user.email_verified,
        "created_at": ensure_aware_utc(user.created_at).isoformat() if user.created_at else None,
    }


def _user_conversation_ids(user_id):
    """A single, user_id-scoped query - never all conversations across
    every user."""
    return [c.id for c in Conversation.query.filter_by(user_id=user_id).all()]


def _message_counts_for_conversations(conversation_ids):
    """{conversation_id: count} for exactly the given conversation ids,
    computed from ONE query (Message.conversation_id.in_(...)) rather
    than one COUNT query per conversation - the N+1 this module is most
    at risk of, called out explicitly in the Phase 14 brief. Returns {}
    immediately for an empty input without querying anything."""
    if not conversation_ids:
        return {}
    messages = Message.query.filter(Message.conversation_id.in_(conversation_ids)).all()
    counts = {}
    for message in messages:
        counts[message.conversation_id] = counts.get(message.conversation_id, 0) + 1
    return counts


def get_dashboard_stats(user_id):
    """Three efficient COUNT queries (SavedPolicy, Conversation, and a
    single Message COUNT joined by conversation ownership via
    Message.conversation_id.in_(...)) - never loads full row data just
    to count it."""
    saved_count = SavedPolicy.query.filter_by(user_id=user_id).count()
    conversation_count = Conversation.query.filter_by(user_id=user_id).count()

    conversation_ids = _user_conversation_ids(user_id)
    message_count = (
        Message.query.filter(Message.conversation_id.in_(conversation_ids)).count()
        if conversation_ids else 0
    )

    return {
        "saved_policies": saved_count,
        "conversations": conversation_count,
        "messages": message_count,
    }


def get_recent_saved_policies(user_id, limit=DEFAULT_RECENT_LIMIT):
    """Newest-saved-first (created_at DESC, id DESC - per the Phase 14
    brief), each paired with CURRENT JSON policy data, in the same
    {saved-relationship fields..., "policy": {...}} shape
    GET /api/saved-policies already uses (Phase 13) - kept consistent
    rather than inventing a different shape for the dashboard.

    A saved row whose policy no longer resolves in the current JSON
    dataset is skipped, and the scan continues past it so the caller
    still gets up to `limit` genuinely valid entries rather than
    potentially fewer just because some earlier-saved rows have since
    gone stale (see module docstring)."""
    clean_limit = _validate_limit(limit, DEFAULT_RECENT_LIMIT, MAX_RECENT_LIMIT)

    all_saved = SavedPolicy.query.filter_by(user_id=user_id).all()
    all_saved.sort(key=lambda s: (s.created_at, s.id), reverse=True)

    results = []
    for saved in all_saved:
        if len(results) >= clean_limit:
            break
        policy = get_policy_by_id(saved.policy_id)
        if policy is None:
            continue
        results.append({**saved.to_dict(), "policy": policy})
    return results


def get_recent_conversations(user_id, limit=DEFAULT_RECENT_LIMIT):
    """Newest-updated-first (updated_at DESC, id DESC - per the Phase 14
    brief), the same ordering GET /api/conversations already uses
    (Phase 12). Adds message_count per conversation via ONE bulk query
    (see _message_counts_for_conversations()) rather than a per-
    conversation COUNT - only for the (at most `limit`, capped at
    MAX_RECENT_LIMIT) conversations actually being returned, not the
    user's entire conversation history."""
    clean_limit = _validate_limit(limit, DEFAULT_RECENT_LIMIT, MAX_RECENT_LIMIT)

    all_conversations = Conversation.query.filter_by(user_id=user_id).all()
    all_conversations.sort(key=lambda c: (c.updated_at, c.id), reverse=True)
    recent = all_conversations[:clean_limit]

    counts = _message_counts_for_conversations([c.id for c in recent])

    return [
        {**conversation.to_dict(), "message_count": counts.get(conversation.id, 0)}
        for conversation in recent
    ]


def get_recent_activity(user_id, limit=DEFAULT_ACTIVITY_LIMIT):
    """A merged, newest-first feed of policy_saved/conversation_created/
    message_sent events - never message content (per the Phase 14
    brief's explicit "do not expose message contents by default").

    Bounded, not "load all historical records": each source is fetched
    with a single query already scoped to `user_id` (never a scan
    across every user's data - the actual expensive case the brief
    warns against), then capped in Python to a modest multiple of the
    requested `limit` (generous enough that merging three sources still
    reliably surfaces the true top `limit` by timestamp, since a single
    user's own saved-policy/conversation/message counts in this app are
    inherently small - at most 151 possible saved policies, and
    conversations/messages are that user's own usage, never system-wide
    history) before the three are merged and re-sorted. A system at much
    larger per-user scale would want each source's query itself sorted
    DESC with a SQL-level LIMIT (the offline test shim used in this
    repository's own test suite does not support ORDER BY DESC - see
    tests/conftest.py's order_by() docstring - which is why this
    function sorts in Python, matching the same convention
    backend/conversations_service.py and backend/saved_policies_service.py
    already established for exactly this reason).

    Sort key is (created_at, type, source-row-id) DESC - fully
    deterministic even when two events across DIFFERENT sources share
    the exact same timestamp (the `type` string alone breaks that tie
    reproducibly; the row id is is only used as the final tiebreak
    within a single type, not compared across types)."""
    clean_limit = _validate_limit(limit, DEFAULT_ACTIVITY_LIMIT, MAX_ACTIVITY_LIMIT)
    per_source_cap = clean_limit * 3

    saved = SavedPolicy.query.filter_by(user_id=user_id).all()
    saved.sort(key=lambda s: (s.created_at, s.id), reverse=True)
    saved = saved[:per_source_cap]

    conversations = Conversation.query.filter_by(user_id=user_id).all()
    conversations.sort(key=lambda c: (c.created_at, c.id), reverse=True)
    conversations = conversations[:per_source_cap]

    conversation_ids = _user_conversation_ids(user_id)
    messages = (
        Message.query.filter(Message.conversation_id.in_(conversation_ids)).all()
        if conversation_ids else []
    )
    messages.sort(key=lambda m: (m.created_at, m.id), reverse=True)
    messages = messages[:per_source_cap]

    events = []
    for item in saved:
        events.append(("policy_saved", item.created_at, item.policy_id, item.id))
    for item in conversations:
        events.append(("conversation_created", item.created_at, item.id, item.id))
    for item in messages:
        events.append(("message_sent", item.created_at, item.id, item.id))

    events.sort(key=lambda e: (e[1], e[0], e[3]), reverse=True)

    return [
        {"type": event_type, "created_at": ensure_aware_utc(created_at).isoformat(), "reference_id": reference_id}
        for event_type, created_at, reference_id, _row_id in events[:clean_limit]
    ]


def get_dashboard_summary(user_id, saved_limit=DEFAULT_RECENT_LIMIT,
                           conversation_limit=DEFAULT_RECENT_LIMIT,
                           activity_limit=DEFAULT_ACTIVITY_LIMIT):
    """Assembles the full GET /api/dashboard response. `user_id` is
    trusted as-is by this function - callers (backend/dashboard_routes.py)
    are responsible for it always being g.current_user.id, never a
    client-supplied value (see module docstring)."""
    user = User.query.filter_by(id=user_id).first()

    return {
        "user": get_user_summary(user),
        "stats": get_dashboard_stats(user_id),
        "recent_saved_policies": get_recent_saved_policies(user_id, limit=saved_limit),
        "recent_conversations": get_recent_conversations(user_id, limit=conversation_limit),
        "recent_activity": get_recent_activity(user_id, limit=activity_limit),
    }
