"""
Phase 13 - saved (bookmarked) policy tests.

Reuses the exact db_test_app + real-session-cookie authentication
pattern tests/test_chat_history.py (Phase 12) already established - see
that file's module docstring for why (skips the full register/verify/
login HTTP round trip, which Phase 3/4/5 already cover end to end).
No real Gemini API call anywhere in this file - saved policies never
touch Gemini at all.
"""

import pytest


# =================================================================
# fixtures
# =================================================================

@pytest.fixture
def saved_app(db_test_app, monkeypatch):
    import config
    from auth_routes import auth_bp
    from saved_policies_routes import saved_policies_bp

    monkeypatch.setattr(config.Config, "DATABASE_URL", "sqlite:///:memory:")
    db_test_app.register_blueprint(auth_bp)
    db_test_app.register_blueprint(saved_policies_bp)
    return db_test_app


def _create_user(app, email):
    from database import db
    from models import User

    with app.app_context():
        user = User(name="Test User", email=email, password_hash="x", email_verified=True)
        db.session.add(user)
        db.session.commit()
        return user.id


def _login(app, client, user_id):
    """Same real-session-cookie mechanism as
    tests/test_chat_history.py's _login()."""
    import config
    from database import db
    from models import UserSession
    from sessions import generate_session_token

    raw_token, token_hash, expires_at = generate_session_token()
    with app.app_context():
        db.session.add(UserSession(user_id=user_id, session_token_hash=token_hash, expires_at=expires_at))
        db.session.commit()
    client.set_cookie(config.Config.SESSION_COOKIE_NAME, raw_token)


@pytest.fixture
def two_users_and_clients(saved_app):
    """(client_a, user_a_id, client_b, user_b_id) - two independently
    authenticated clients, for cross-user IDOR tests. Plain (non-context-
    manager) test clients - see tests/test_chat_history.py's
    two_users_and_clients fixture docstring for why interleaving two
    context-manager test clients corrupts Werkzeug's context stack."""
    client_a = saved_app.test_client()
    client_b = saved_app.test_client()
    user_a_id = _create_user(saved_app, "alice@example.com")
    user_b_id = _create_user(saved_app, "bob@example.com")
    _login(saved_app, client_a, user_a_id)
    _login(saved_app, client_b, user_b_id)
    return client_a, user_a_id, client_b, user_b_id


@pytest.fixture
def authed_client(saved_app):
    client = saved_app.test_client()
    user_id = _create_user(saved_app, "solo@example.com")
    _login(saved_app, client, user_id)
    return client, user_id


def _real_policy_id():
    """A real, currently-loaded policy id - id 1 always exists (151
    policies, ids 1..151, deterministically assigned - see
    policy_loader.py)."""
    return 1


def _real_policy_id_2():
    return 2


# =================================================================
# 1-3. authentication required
# =================================================================

def test_unauthenticated_post_returns_401(saved_app):
    client = saved_app.test_client()
    resp = client.post("/api/saved-policies", json={"policy_id": _real_policy_id()})
    assert resp.status_code == 401


def test_unauthenticated_get_returns_401(saved_app):
    client = saved_app.test_client()
    resp = client.get("/api/saved-policies")
    assert resp.status_code == 401


def test_unauthenticated_delete_returns_401(saved_app):
    client = saved_app.test_client()
    resp = client.delete(f"/api/saved-policies/{_real_policy_id()}")
    assert resp.status_code == 401


def test_unauthenticated_get_single_returns_401(saved_app):
    client = saved_app.test_client()
    resp = client.get(f"/api/saved-policies/{_real_policy_id()}")
    assert resp.status_code == 401


# =================================================================
# 4-5. authenticated save succeeds, data comes from JSON
# =================================================================

def test_authenticated_save_succeeds(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/saved-policies", json={"policy_id": _real_policy_id()})
    assert resp.status_code == 201
    body = resp.get_json()["data"]
    assert body["policy_id"] == _real_policy_id()
    assert "created_at" in body
    assert "policy" in body


def test_saved_policy_data_comes_from_json_loader(authed_client):
    from policy_loader import get_policy_by_id as loader_get

    client, user_id = authed_client
    resp = client.post("/api/saved-policies", json={"policy_id": _real_policy_id()})
    returned_policy = resp.get_json()["data"]["policy"]

    real_policy = loader_get(_real_policy_id())
    assert returned_policy["name"] == real_policy["name"]
    assert returned_policy["category"] == real_policy["category"]
    assert returned_policy["change"] == real_policy["change"]
    assert returned_policy["impact"] == real_policy["impact"]


# =================================================================
# 6-10. validation / edge cases
# =================================================================

def test_saving_nonexistent_policy_returns_404(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/saved-policies", json={"policy_id": 999999})
    assert resp.status_code == 404


def test_missing_policy_id_returns_400(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/saved-policies", json={})
    assert resp.status_code == 400


def test_null_policy_id_returns_400(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/saved-policies", json={"policy_id": None})
    assert resp.status_code == 400


def test_string_policy_id_returns_400(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/saved-policies", json={"policy_id": "42"})
    assert resp.status_code == 400


def test_boolean_policy_id_returns_400(authed_client):
    """isinstance(True, int) is True in Python - must not be silently
    accepted as policy_id=1."""
    client, user_id = authed_client
    resp = client.post("/api/saved-policies", json={"policy_id": True})
    assert resp.status_code == 400


def test_zero_policy_id_handled_cleanly(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/saved-policies", json={"policy_id": 0})
    assert resp.status_code == 404  # well-formed integer, just no such policy


def test_negative_policy_id_handled_cleanly(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/saved-policies", json={"policy_id": -5})
    assert resp.status_code == 404


def test_extremely_large_policy_id_handled_cleanly(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/saved-policies", json={"policy_id": 99999999999999})
    assert resp.status_code == 404


def test_malformed_request_body_returns_400_not_500(authed_client):
    client, user_id = authed_client
    resp = client.post("/api/saved-policies", data="not json", content_type="text/plain")
    assert resp.status_code == 400


# =================================================================
# 11-12. duplicate handling
# =================================================================

def test_duplicate_save_does_not_create_duplicate_rows(authed_client):
    client, user_id = authed_client
    first = client.post("/api/saved-policies", json={"policy_id": _real_policy_id()})
    second = client.post("/api/saved-policies", json={"policy_id": _real_policy_id()})

    assert first.status_code == 201
    assert second.status_code == 200  # idempotent, not an error
    assert first.get_json()["data"]["id"] == second.get_json()["data"]["id"]

    listing = client.get("/api/saved-policies").get_json()
    assert listing["pagination"]["total"] == 1


def test_database_unique_constraint_exists(saved_app):
    """Proves the (user_id, policy_id) UNIQUE constraint is genuinely
    enforced at the database level, bypassing the service layer's own
    pre-check entirely - inserting two rows directly and committing in
    the SAME transaction must raise, exactly the race-condition
    scenario the service layer's IntegrityError handling exists for."""
    from database import db
    from models import SavedPolicy, User
    from sqlalchemy.exc import IntegrityError

    with saved_app.app_context():
        user = User(name="T", email="constraint@example.com", password_hash="x", email_verified=True)
        db.session.add(user)
        db.session.commit()

        db.session.add(SavedPolicy(user_id=user.id, policy_id=1))
        db.session.add(SavedPolicy(user_id=user.id, policy_id=1))
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()


def test_save_policy_handles_constraint_race_cleanly(saved_app, monkeypatch):
    """Simulates the race window save_policy()'s IntegrityError handler
    exists for: the pre-check finds nothing (another request hasn't
    committed yet), but by the time THIS request commits, the row
    already exists - must resolve to the existing row, not a raw
    IntegrityError/500."""
    import saved_policies_service as sps
    from database import db
    from models import SavedPolicy, User

    with saved_app.app_context():
        user = User(name="T", email="race@example.com", password_hash="x", email_verified=True)
        db.session.add(user)
        db.session.commit()

        # Pre-create the row, then make the pre-check query report
        # "nothing found" exactly once (simulating the race window),
        # forcing save_policy() down the insert path where it must hit
        # (and cleanly recover from) the real UNIQUE constraint.
        db.session.add(SavedPolicy(user_id=user.id, policy_id=1))
        db.session.commit()

        original_filter_by = SavedPolicy.query.__class__.filter_by
        call_count = {"n": 0}

        class _FakeQueryResult:
            def first(self_inner):
                return None

        def _patched_filter_by(self_inner, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _FakeQueryResult()
            return original_filter_by(self_inner, **kwargs)

        monkeypatch.setattr(SavedPolicy.query.__class__, "filter_by", _patched_filter_by)

        saved, policy, created = sps.save_policy(user.id, 1)
        assert created is False
        assert saved.policy_id == 1

        rows = original_filter_by(SavedPolicy.query, user_id=user.id, policy_id=1).all()
        assert len(rows) == 1


# =================================================================
# 13-15. list own saved policies, empty list, ordering
# =================================================================

def test_user_can_list_own_saved_policies(authed_client):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": 1})
    client.post("/api/saved-policies", json={"policy_id": 2})

    resp = client.get("/api/saved-policies")
    assert resp.status_code == 200
    policy_ids = {item["policy_id"] for item in resp.get_json()["data"]}
    assert policy_ids == {1, 2}


def test_empty_list_works(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/saved-policies")
    assert resp.status_code == 200
    assert resp.get_json()["data"] == []
    assert resp.get_json()["pagination"]["total"] == 0


def test_newest_first_ordering(authed_client):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": 1})
    client.post("/api/saved-policies", json={"policy_id": 2})
    client.post("/api/saved-policies", json={"policy_id": 3})

    resp = client.get("/api/saved-policies")
    ids_in_order = [item["policy_id"] for item in resp.get_json()["data"]]
    assert ids_in_order == [3, 2, 1]


def test_list_pagination_works(authed_client):
    client, user_id = authed_client
    for policy_id in (1, 2, 3, 4, 5):
        client.post("/api/saved-policies", json={"policy_id": policy_id})

    resp = client.get("/api/saved-policies?page=1&per_page=2")
    body = resp.get_json()
    assert len(body["data"]) == 2
    assert body["pagination"] == {"page": 1, "per_page": 2, "total": 5, "pages": 3}


# =================================================================
# 16-19. retrieve / delete own saved policy
# =================================================================

def test_retrieve_own_saved_policy(authed_client):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": _real_policy_id()})

    resp = client.get(f"/api/saved-policies/{_real_policy_id()}")
    assert resp.status_code == 200
    assert resp.get_json()["data"]["policy_id"] == _real_policy_id()
    assert "policy" in resp.get_json()["data"]


def test_retrieve_unsaved_policy_returns_404(authed_client):
    client, user_id = authed_client
    resp = client.get(f"/api/saved-policies/{_real_policy_id()}")
    assert resp.status_code == 404


def test_delete_own_saved_policy(authed_client):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": _real_policy_id()})

    resp = client.delete(f"/api/saved-policies/{_real_policy_id()}")
    assert resp.status_code == 200

    get_resp = client.get(f"/api/saved-policies/{_real_policy_id()}")
    assert get_resp.status_code == 404


def test_delete_unsaved_policy_returns_404(authed_client):
    client, user_id = authed_client
    resp = client.delete(f"/api/saved-policies/{_real_policy_id()}")
    assert resp.status_code == 404


# =================================================================
# 20-23. IDOR / cross-user isolation, mass assignment
# =================================================================

def test_user_b_cannot_retrieve_user_as_saved_policy(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    client_a.post("/api/saved-policies", json={"policy_id": _real_policy_id()})

    resp = client_b.get(f"/api/saved-policies/{_real_policy_id()}")
    assert resp.status_code == 404


def test_user_b_cannot_delete_user_as_saved_policy(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    client_a.post("/api/saved-policies", json={"policy_id": _real_policy_id()})

    resp = client_b.delete(f"/api/saved-policies/{_real_policy_id()}")
    assert resp.status_code == 404

    assert client_a.get(f"/api/saved-policies/{_real_policy_id()}").status_code == 200


def test_both_users_can_independently_save_same_policy(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients

    resp_a = client_a.post("/api/saved-policies", json={"policy_id": _real_policy_id()})
    resp_b = client_b.post("/api/saved-policies", json={"policy_id": _real_policy_id()})

    assert resp_a.status_code == 201
    assert resp_b.status_code == 201
    assert resp_a.get_json()["data"]["id"] != resp_b.get_json()["data"]["id"]

    assert len(client_a.get("/api/saved-policies").get_json()["data"]) == 1
    assert len(client_b.get("/api/saved-policies").get_json()["data"]) == 1


def test_deleting_one_users_save_does_not_affect_the_others(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients
    client_a.post("/api/saved-policies", json={"policy_id": _real_policy_id()})
    client_b.post("/api/saved-policies", json={"policy_id": _real_policy_id()})

    client_a.delete(f"/api/saved-policies/{_real_policy_id()}")

    assert client_a.get(f"/api/saved-policies/{_real_policy_id()}").status_code == 404
    assert client_b.get(f"/api/saved-policies/{_real_policy_id()}").status_code == 200


def test_user_id_mass_assignment_is_ignored(two_users_and_clients):
    client_a, user_a, client_b, user_b = two_users_and_clients

    resp = client_a.post(
        "/api/saved-policies",
        json={"policy_id": _real_policy_id(), "user_id": user_b},
    )
    assert resp.status_code == 201

    assert client_a.get(f"/api/saved-policies/{_real_policy_id()}").status_code == 200
    assert client_b.get(f"/api/saved-policies/{_real_policy_id()}").status_code == 404


# =================================================================
# 24. policy JSON remains untouched
# =================================================================

def test_policy_json_files_are_never_modified_by_saved_policies(authed_client):
    import hashlib
    import os

    data_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data"
    )
    before = {}
    for fname in os.listdir(data_dir):
        if fname.endswith(".json"):
            with open(os.path.join(data_dir, fname), "rb") as f:
                before[fname] = hashlib.sha256(f.read()).hexdigest()

    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": _real_policy_id()})
    client.delete(f"/api/saved-policies/{_real_policy_id()}")

    after = {}
    for fname in os.listdir(data_dir):
        if fname.endswith(".json"):
            with open(os.path.join(data_dir, fname), "rb") as f:
                after[fname] = hashlib.sha256(f.read()).hexdigest()

    assert before == after


# =================================================================
# 25. database errors do not leak internals
# =================================================================

def test_not_found_error_never_leaks_internal_details(authed_client):
    client, user_id = authed_client
    resp = client.get(f"/api/saved-policies/{_real_policy_id()}")
    body = resp.get_json()
    assert set(body.keys()) == {"error"}
    assert "sql" not in body["error"].lower()
    assert "traceback" not in body["error"].lower()


def test_invalid_policy_id_path_format_is_rejected_cleanly(authed_client):
    client, user_id = authed_client
    resp = client.get("/api/saved-policies/not-a-number")
    assert resp.status_code == 400
    assert set(resp.get_json().keys()) == {"error"}


# =================================================================
# 26. current JSON data returned, not stale DB data
# =================================================================

def test_returns_current_json_data_not_a_stale_copy(authed_client, monkeypatch):
    """Proves saved_policies_service resolves policy content fresh from
    the JSON loader on every read, never from anything cached in the
    saved_policies table itself (which has no name/category/change/
    impact columns at all - see models.py's SavedPolicy docstring) - by
    monkeypatching the loader's return value AFTER saving and confirming
    the change is immediately visible. Patched on saved_policies_service
    itself (not policy_service) - saved_policies_service.py does `from
    policy_service import get_policy_by_id` (a name import), so it has
    its own separately-bound reference; patching policy_service's copy
    would leave that reference untouched."""
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": _real_policy_id()})

    import saved_policies_service

    def _patched_get_policy_by_id(policy_id):
        return {
            "id": policy_id, "name": "UPDATED NAME AFTER SAVE",
            "sector": "test", "category": "test", "sub_category": "test",
            "change": "test", "impact": "test",
        }
    monkeypatch.setattr(saved_policies_service, "get_policy_by_id", _patched_get_policy_by_id)

    resp = client.get(f"/api/saved-policies/{_real_policy_id()}")
    assert resp.get_json()["data"]["policy"]["name"] == "UPDATED NAME AFTER SAVE"


def test_saved_policy_for_a_since_removed_policy_is_omitted_from_listing(authed_client, monkeypatch):
    client, user_id = authed_client
    client.post("/api/saved-policies", json={"policy_id": _real_policy_id()})

    import saved_policies_service
    monkeypatch.setattr(saved_policies_service, "get_policy_by_id", lambda policy_id: None)

    resp = client.get("/api/saved-policies")
    assert resp.get_json()["data"] == []
    assert resp.get_json()["pagination"]["total"] == 0


# =================================================================
# 27. migration creates the required table and constraint
# =================================================================

def test_migration_file_creates_saved_policies_table_and_constraint():
    import os

    migrations_dir = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "migrations", "versions",
    )
    matches = [f for f in os.listdir(migrations_dir) if "saved_policies" in f]
    assert len(matches) == 1

    with open(os.path.join(migrations_dir, matches[0]), encoding="utf-8") as f:
        source = f.read()

    assert "down_revision = 'b7d2f4a1c8e6'" in source
    assert "'saved_policies'" in source
    assert "'user_id'" in source and "'policy_id'" in source and "'created_at'" in source
    assert "UniqueConstraint('user_id', 'policy_id'" in source
    assert "sa.ForeignKey('users.id')" in source
    # Explicitly must NOT reference the old policies table.
    assert "ForeignKey('policies" not in source
