"""Give every test a local environment; tests exercising CI opt in explicitly."""

import os
from pathlib import Path
import sys

import pytest

from test_environment import TEST_PYTHON


@pytest.fixture(autouse=True)
def local_environment(monkeypatch):
    monkeypatch.setattr(sys, 'executable', TEST_PYTHON)
    monkeypatch.setenv('PATH', str(Path(TEST_PYTHON).parent) + os.pathsep + os.environ['PATH'])
    monkeypatch.delenv('CI', raising=False)
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)
