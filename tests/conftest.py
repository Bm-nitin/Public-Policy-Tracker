"""
Shared pytest fixtures for the Phase 0.5 characterization/regression suite.

Design notes
------------
- backend/*.py uses flat imports (`from chatbot import get_response`, etc.),
  so the `backend/` directory must be on sys.path before those modules are
  imported. We do that here, once, for the whole test session.

- These tests must run without network access and without calling the real
  Gemini API (see task requirements). Every test that reaches the Gemini
  code path monkeypatches `chatbot.client` / `chatbot.call_generative_ai`
  directly - real network calls are never made from this suite.

- OFFLINE SANDBOX SHIM (read this before deleting it):
  `flask-cors` and `google-genai` are listed in requirements.txt but may not
  be installed in every environment this suite runs in (e.g. a network-
  restricted CI sandbox used to author/validate this suite). The try/except
  blocks below only install a minimal fake module into sys.modules, and only
  if the real package import fails. In any environment where
  `pip install -r requirements.txt` has actually been run (local dev,
  Render, a normal CI runner), the real packages import successfully and
  these shims are never touched. They exist purely so `pytest` can *collect
  and run* this suite in a bare sandbox - they do not change, wrap, or
  monkeypatch any backend/*.py source file.
"""

import os
import sys
import types

import pytest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND_DIR = os.path.join(ROOT_DIR, "backend")
DATA_DIR = os.path.join(ROOT_DIR, "data")

if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# --- flask_cors shim (only if the real package isn't installed) -----------
try:
    import flask_cors  # noqa: F401
except ImportError:
    _fake_flask_cors = types.ModuleType("flask_cors")

    def _fake_cors(app, *args, **kwargs):
        return app

    _fake_flask_cors.CORS = _fake_cors
    sys.modules["flask_cors"] = _fake_flask_cors


# --- google-genai shim (only if the real package isn't installed) ---------
try:
    from google import genai  # noqa: F401
except ImportError:
    _fake_google = sys.modules.get("google") or types.ModuleType("google")
    _fake_genai = types.ModuleType("google.genai")

    class _OfflineFakeModels:
        def generate_content(self, model=None, contents=None):
            raise RuntimeError(
                "A real Gemini call was attempted through the offline test "
                "shim. Every test that reaches call_generative_ai() must "
                "monkeypatch chatbot.client (or chatbot.call_generative_ai "
                "directly) - see test_gemini_mocked.py for the pattern."
            )

    class _OfflineFakeClient:
        def __init__(self, api_key=None):
            self.api_key = api_key
            self.models = _OfflineFakeModels()

    _fake_genai.Client = _OfflineFakeClient
    _fake_google.genai = _fake_genai
    sys.modules["google"] = _fake_google
    sys.modules["google.genai"] = _fake_genai


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def utils_module():
    import utils
    return utils


@pytest.fixture(scope="session")
def policy_loader_module():
    import policy_loader
    return policy_loader


@pytest.fixture(scope="session")
def chatbot_module():
    import chatbot
    return chatbot


@pytest.fixture(scope="session")
def flask_app_module():
    import app
    app.app.testing = True
    return app


@pytest.fixture
def app_client(flask_app_module):
    with flask_app_module.app.test_client() as client:
        yield client


@pytest.fixture
def reset_policy_cache(policy_loader_module):
    """Ensure load_policies() re-reads from disk instead of returning the
    module-level cache. Restores the real cached data afterwards so later
    tests (and the chatbot module, which loaded it once at import time)
    keep working against the real dataset."""
    original_cache = policy_loader_module._cached_policies
    policy_loader_module._cached_policies = None
    yield
    policy_loader_module._cached_policies = original_cache


@pytest.fixture
def real_data_dir():
    return DATA_DIR


@pytest.fixture
def mock_gemini_client(monkeypatch, chatbot_module):
    """Factory fixture: call with the text you want generate_content() to
    return (or an exception instance to raise it instead)."""

    def _install(reply_text=None, raise_exc=None):
        class _FakeResponse:
            text = reply_text

        class _FakeModels:
            def generate_content(self, model=None, contents=None):
                if raise_exc is not None:
                    raise raise_exc
                return _FakeResponse()

        class _FakeClient:
            def __init__(self):
                self.models = _FakeModels()

        monkeypatch.setattr(chatbot_module, "client", _FakeClient())
        monkeypatch.setattr(chatbot_module, "API_KEY", "test-key-not-real")

    return _install
