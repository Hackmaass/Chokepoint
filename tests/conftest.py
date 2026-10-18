"""No test may reach the real ops repo: a token in .env or the environment is dropped, and the GitHub API points nowhere."""

import pytest


@pytest.fixture(autouse=True)
def _no_real_dispatch(monkeypatch):
    monkeypatch.delenv("OPS_GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("OPS_GITHUB_API", "http://127.0.0.1:9")
