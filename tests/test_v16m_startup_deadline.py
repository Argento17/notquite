"""Pytest import verification allows startup work without extending test deadlines."""
import os

import pytest

from workorder import python_imports, store
from test_v16g_import_guard import comparison
from test_workorder import PYTHON, local_repo


def delayed_startup(tmp_path, monkeypatch):
    startup = tmp_path / 'startup'
    startup.mkdir()
    completed = tmp_path / 'startup-completed'
    (startup / 'sitecustomize.py').write_text(
        'import time\ntime.sleep(0.5)\n'
        f'from pathlib import Path\nPath({str(completed)!r}).touch()\n')
    monkeypatch.setenv('PYTHONPATH', str(startup))
    # Keep a generous allowance for pytest itself on slow CI runners. The
    # deliberate delay still exceeds the standalone probe's scaled deadline.
    monkeypatch.setattr(python_imports, 'PROBE_TIMEOUT', 0.25)
    monkeypatch.setattr(python_imports, 'GUARD_STARTUP_TIMEOUT', 20, raising=False)
    return completed


@pytest.mark.parametrize('timeout', [None, 30])
def test_slow_pytest_startup_can_exceed_standalone_probe_deadline(local_repo, tmp_path,
                                                               monkeypatch, timeout):
    destination = comparison(local_repo, tmp_path)
    completed = delayed_startup(tmp_path, monkeypatch)
    command = [PYTHON, '-m', 'pytest', '-q', 'tests/test_calc.py']
    run = store.cached_test(command, destination, timeout=timeout, cache_root=local_repo, fresh=True)
    assert run.returncode == 0, (run.returncode, run.stdout, run.stderr)
    assert completed.exists()
    assert '1 passed' in run.stdout
    assert not run.cached


def test_standalone_probe_keeps_its_shorter_deadline(local_repo, tmp_path, monkeypatch):
    destination = comparison(local_repo, tmp_path)
    completed = delayed_startup(tmp_path, monkeypatch)
    command = [PYTHON, '-m', 'pytest', '-q', 'tests/test_calc.py']
    failure = python_imports.check(destination, local_repo, command,
                                   python_imports.environment(destination, os.environ.copy()))
    assert isinstance(failure, python_imports.ImportFailure)
    assert 'timed out after 0.25 seconds' in failure
    assert not completed.exists()


def test_hung_test_after_import_verification_keeps_declared_timeout(local_repo, tmp_path):
    destination = comparison(local_repo, tmp_path)
    command = [PYTHON, '-m', 'pytest', '-q', 'tests/test_calc.py']
    # Warm dependency bytecode before bounding the test body on slow machines.
    warmup = store.cached_test(command, destination, cache_root=local_repo, fresh=True)
    assert warmup.returncode == 0, (warmup.returncode, warmup.stdout, warmup.stderr)
    started, completed = tmp_path / 'test-started', tmp_path / 'test-completed'
    (destination / 'tests/test_calc.py').write_text(
        'from pathlib import Path\nimport time\n'
        'def test_hangs():\n'
        f'    Path({str(started)!r}).touch()\n'
        '    time.sleep(30)\n'
        f'    Path({str(completed)!r}).touch()\n')
    run = store.cached_test(command, destination, timeout=10, cache_root=local_repo, fresh=True)
    assert started.exists(), (run.returncode, run.stdout, run.stderr)
    assert run.returncode == 124
    assert 'test timed out after 10 seconds (exit 124)' in run.stderr
    assert not completed.exists()
