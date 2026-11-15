"""Give every test a local environment; tests exercising CI opt in explicitly."""

import pytest


@pytest.fixture(autouse=True)
def local_environment(monkeypatch):
    monkeypatch.delenv('CI', raising=False)
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
