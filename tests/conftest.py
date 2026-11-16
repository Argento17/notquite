"""Give every test a local environment; tests exercising CI opt in explicitly."""

import os
from pathlib import Path
import sys

import pytest

from test_environment import TEST_PYTHON


@pytest.fixture
def observable_git_environment(tmp_path, monkeypatch):
    """Give cache-budget tests Git inputs whose dependencies can be observed."""
    # Keep exact discovery budgets independent of ambient includes, filters
    # and attributes. Git for Windows defaults are covered without this fixture
    # by the git-cache and Windows regression tests.
    for name in list(os.environ):
        if name.startswith('GIT_'):
            monkeypatch.delenv(name)
    home = tmp_path / 'cache-git-home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(home / '.config'))
    monkeypatch.setenv('GIT_CONFIG_NOSYSTEM', '1')


@pytest.fixture(autouse=True)
def local_environment(monkeypatch):
    monkeypatch.setattr(sys, 'executable', TEST_PYTHON)
    monkeypatch.setenv('PATH', str(Path(TEST_PYTHON).parent) + os.pathsep + os.environ['PATH'])
    monkeypatch.delenv('CI', raising=False)
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
