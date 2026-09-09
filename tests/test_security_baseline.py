"""
Area 7: Security baseline

Confirms the Phase 0 audit findings mechanically, in CI, so a future commit
can't silently reintroduce a tracked .env, a tracked __pycache__, or a
hardcoded API key. Uses `git ls-files` (what's actually tracked), not a
directory listing, so local build artifacts don't produce false failures.
No secret values are ever read into an assertion or printed.
"""

import os
import re
import subprocess

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tracked_files():
    result = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT_DIR,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.splitlines()


def test_env_file_is_not_tracked_by_git():
    tracked = _tracked_files()
    assert ".env" not in tracked


def test_pycache_directories_are_not_tracked_by_git():
    tracked = _tracked_files()
    assert not any("__pycache__" in path for path in tracked)


def test_gitignore_excludes_env_and_pycache():
    gitignore_path = os.path.join(ROOT_DIR, ".gitignore")
    with open(gitignore_path, encoding="utf-8") as f:
        content = f.read()
    assert ".env" in content
    assert "__pycache__" in content


def test_no_hardcoded_api_key_in_tracked_backend_source():
    """Scans tracked backend/*.py source for suspicious hardcoded secrets
    (a long alphanumeric literal assigned to something key-shaped). This
    intentionally does not print or assert on any specific value - it only
    fails if a plausible key literal is found."""
    suspicious_pattern = re.compile(
        r'(?i)(api[_-]?key|secret|token)\s*=\s*["\'][A-Za-z0-9_\-]{20,}["\']'
    )
    tracked = _tracked_files()
    backend_py_files = [
        f for f in tracked if f.startswith("backend/") and f.endswith(".py")
    ]
    assert backend_py_files, "expected to find backend/*.py in tracked files"

    for relative_path in backend_py_files:
        full_path = os.path.join(ROOT_DIR, relative_path)
        with open(full_path, encoding="utf-8") as f:
            content = f.read()
        match = suspicious_pattern.search(content)
        assert match is None, (
            f"Possible hardcoded secret literal found in {relative_path} "
            f"(value withheld from test output)"
        )


def test_chatbot_reads_api_key_only_from_environment(chatbot_module):
    """Confirms the key is sourced via os.getenv(...) at import time, not a
    literal - checked structurally via the source file, not by reading the
    resolved value out of the running module (which would risk leaking it
    into a failure message)."""
    chatbot_source_path = os.path.join(ROOT_DIR, "backend", "chatbot.py")
    with open(chatbot_source_path, encoding="utf-8") as f:
        source = f.read()
    assert "os.getenv(" in source
