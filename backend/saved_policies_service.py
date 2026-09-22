"""
Phase 13 - saved (bookmarked) policy business logic.

Every function takes `user_id` as its first argument, sourced ONLY from
sessions.get_current_user()/g.current_user in
backend/saved_policies_routes.py - never from a request body/query
string (see that module's docstring). Ownership is enforced by
filtering every query on user_id directly, the same convention
backend/conversations_service.py already established in Phase 12.

JSON is the only source of truth for policy CONTENT: every function
here that returns policy details resolves policy_id against
backend/policy_service.get_policy_by_id() (backed by
backend/policy_loader.py's cached data/*.json load) at call time, never
against anything stored in the saved_policies table itself - that table
holds only user_id/policy_id/created_at (see backend/models.py's
SavedPolicy docstring). This means a saved policy's displayed details
always reflect the CURRENT JSON dataset, including if that policy's
data/*.json entry is edited after it was saved.

A saved relationship whose policy_id no longer resolves to any policy
in the current JSON dataset (the policy was later removed from
data/*.json entirely) is treated as NOT FOUND by every read here
(SavedPolicyNotFoundError / silently omitted from a listing) - the same
"inert row" treatment backend/models.py's PolicyEmbedding docstring
already establishes for exactly this situation: there is no policy data
left to return, so returning the bookmark on its own (with no policy
details) would misrepresent what this API promises ("the saved
relationship AND current policy data").
"""

from sqlalchemy.exc import IntegrityError

from database import db
from models import SavedPolicy
from policy_service import get_policy_by_id

DEFAULT_PER_PAGE = 20
MAX_PER_PAGE = 100


class SavedPolicyNotFoundError(Exception):
    """The user has no saved relationship for this policy_id - whether
    because they never saved it, because it belongs to a different user
    (see module docstring - ownership is always part of the query, so
    this looks identical to "never saved" for the same "don't reveal
    what another user has" reasoning backend/conversations_service.py's
    ConversationNotFoundError already established), or because the
    policy itself no longer exists in the current JSON dataset."""


class ValidationError(Exception):
    """Message is always safe to return to the client directly - see
    backend/conversations_service.py's ValidationError for the same
    convention."""


def _validate_policy_id(policy_id):
    """Type/presence validation ONLY - whether the id actually
    resolves to a real policy is a separate, later concern (see
    save_policy()/get_saved_policy(), which raise SavedPolicyNotFoundError
    for that, not ValidationError). Booleans are explicitly rejected
    even though `isinstance(True, int)` is true in Python - a client
    sending `true`/`false` for policy_id is a type error, not a
    disguised 0/1."""
    if policy_id is None:
        raise ValidationError("policy_id is required.")
    if isinstance(policy_id, bool) or not isinstance(policy_id, int):
        raise ValidationError("policy_id must be an integer.")
    return policy_id


def _resolve_policy_or_raise(policy_id):
    policy = get_policy_by_id(policy_id)
    if policy is None:
        raise SavedPolicyNotFoundError(str(policy_id))
    return policy


def save_policy(user_id, policy_id):
    """Validates policy_id (raises ValidationError), resolves it against
    the current JSON dataset (raises SavedPolicyNotFoundError if it
    doesn't exist - a 404, not a 400: the id is well-formed, it just
    doesn't refer to a real policy), then creates the saved relationship
    - or, if the user already saved this exact policy, returns the
    EXISTING relationship unchanged rather than erroring or creating a
    second row (see module/class docstrings on "handle duplicate
    insertion cleanly").

    Returns (SavedPolicy, policy_dict, created: bool) - `created` is
    False for the idempotent-duplicate case, letting the route return
    200 instead of 201 without a second query.

    Duplicate prevention is enforced at TWO levels, deliberately:
      1. An application-level pre-check (the common, non-racing case -
         avoids ever hitting the database's constraint machinery for a
         plain, sequential duplicate request).
      2. The database's UNIQUE(user_id, policy_id) constraint (see the
         Phase 13 migration) as the actual source of truth, for the
         race-condition window between step 1's check and this
         function's own insert - the exact same two-level pattern
         backend/auth_routes.py's register() already uses for duplicate
         emails (see that function for the precedent this follows).
         Hitting the constraint here is caught, rolled back, and
         resolved the same idempotent way as the pre-check finding an
         existing row - a client can never distinguish "no race
         occurred" from "one did and we handled it," which is exactly
         the point.
    """
    clean_policy_id = _validate_policy_id(policy_id)
    policy = _resolve_policy_or_raise(clean_policy_id)

    existing = SavedPolicy.query.filter_by(user_id=user_id, policy_id=clean_policy_id).first()
    if existing is not None:
        return existing, policy, False

    saved = SavedPolicy(user_id=user_id, policy_id=clean_policy_id)
    db.session.add(saved)
    try:
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        existing = SavedPolicy.query.filter_by(user_id=user_id, policy_id=clean_policy_id).first()
        if existing is not None:
            return existing, policy, False
        raise  # Genuinely unexpected - not a duplicate-save race after all.

    return saved, policy, True


def list_saved_policies(user_id, page=1, per_page=DEFAULT_PER_PAGE):
    """Newest-saved-first (created_at DESC, id DESC deterministic
    tiebreak - per the Phase 13 brief), each entry paired with its
    CURRENT JSON policy data (see module docstring). A saved_policies
    row whose policy no longer exists in the current JSON dataset is
    silently omitted from the listing (see module docstring's "inert
    row" note) rather than raising or returning a broken/partial entry
    - `total` still reflects only the entries actually returned, so
    pagination math stays consistent with what the caller can see."""
    all_saved = SavedPolicy.query.filter_by(user_id=user_id).all()
    all_saved.sort(key=lambda s: (s.created_at, s.id), reverse=True)

    resolved = []
    for saved in all_saved:
        policy = get_policy_by_id(saved.policy_id)
        if policy is None:
            continue
        resolved.append({**saved.to_dict(), "policy": policy})

    total = len(resolved)
    start = (page - 1) * per_page
    end = start + per_page
    return {"items": resolved[start:end], "total": total}


def get_saved_policy(user_id, policy_id):
    """Returns (SavedPolicy, policy_dict), or raises
    SavedPolicyNotFoundError - see module docstring for the two
    (indistinguishable to the caller) reasons that can happen."""
    saved = SavedPolicy.query.filter_by(user_id=user_id, policy_id=policy_id).first()
    if saved is None:
        raise SavedPolicyNotFoundError(str(policy_id))

    policy = get_policy_by_id(saved.policy_id)
    if policy is None:
        raise SavedPolicyNotFoundError(str(policy_id))

    return saved, policy


def delete_saved_policy(user_id, policy_id):
    """Deletes ONLY the authenticated user's own saved relationship for
    this policy_id - the user_id filter is not optional context here,
    it is the entire mechanism that makes another user's identically-
    policy_id'd saved row untouchable (see module docstring / the Phase
    13 IDOR requirement). Raises SavedPolicyNotFoundError if the user
    has no such saved relationship - deliberately does NOT require the
    policy to still exist in the current JSON dataset (unlike
    get_saved_policy()/list_saved_policies()): deleting a bookmark for a
    since-removed policy must still work, so a user can clean up a
    stale save rather than being stuck with an un-deletable row that
    read endpoints can no longer show them."""
    saved = SavedPolicy.query.filter_by(user_id=user_id, policy_id=policy_id).first()
    if saved is None:
        raise SavedPolicyNotFoundError(str(policy_id))

    db.session.delete(saved)
    db.session.commit()
