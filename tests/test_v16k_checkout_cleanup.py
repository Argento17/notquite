"""Temporary checkouts must release Git children before Windows removes them."""
import os
from contextlib import nullcontext
from pathlib import Path
import subprocess
import tempfile

import pytest

from workorder import gate, store
from test_workorder import PYTHON, local_repo
from test_v07b_comparison import setup_tests


def _children_in(directory):
    """Inspect live children independently of Workorder's batch bookkeeping."""
    held = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            status = (entry / 'status').read_text()
            if f'PPid:\t{os.getpid()}\n' not in status:
                continue
            cwd = Path(os.readlink(entry / 'cwd'))
        except (FileNotFoundError, ProcessLookupError):
            continue
        if cwd.is_relative_to(directory):
            held.append((int(entry.name), str(cwd)))
    return held


@pytest.mark.parametrize('guard', ['proc', 'windows'])
def test_comparison_releases_children_before_checkout_removal(local_repo, monkeypatch, guard):
    if guard == 'proc' and not Path('/proc/self/cwd').exists():
        pytest.skip('requires /proc process cwd inspection')
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    probe, base = setup_tests(root, 'def test_old():\n    assert False\n')
    probe.unlink()
    (probe.parent / 'test_new.py').write_text('def test_new():\n    assert 1 == 1\n')
    batches, removed = [], []
    popen, cleanup = subprocess.Popen, tempfile.TemporaryDirectory.cleanup

    def start(argv, *args, **kwargs):
        process = popen(argv, *args, **kwargs)
        if argv[:3] == ['git', 'cat-file', '--batch']:
            batches.append((Path(kwargs['cwd']).resolve(), process))
        return process

    def checked_cleanup(directory):
        path = Path(directory.name).resolve()
        if path.name.startswith('workorder-tests-'):
            if guard == 'proc':
                assert not _children_in(path), 'live child cwd inside checkout at removal'
            else:
                held = [process.pid for cwd, process in batches
                        if cwd.is_relative_to(path) and process.poll() is None]
                if held:
                    raise PermissionError(32, f'checkout is being used by Git processes {held}', str(path))
            cleanup(directory)
            removed.append(path)
            if guard == 'proc':
                assert not _children_in(path), 'live child cwd inside removed checkout'
        else:
            cleanup(directory)

    monkeypatch.setattr(subprocess, 'Popen', start)
    monkeypatch.setattr(tempfile.TemporaryDirectory, 'cleanup', checked_cleanup)
    with store.read_session():
        problems = gate._test_change_problems(root, base, store.record(root, 'TASK-1'), allow=True)
        assert problems == []
        assert removed and all(not path.exists() for path in removed)
    assert any(cwd.name == 'code' for cwd, _ in batches)
    assert any(cwd.name.startswith('ref-') for cwd, _ in batches)


@pytest.mark.parametrize('outcome', ['return', 'error', 'timeout', 'killed_batch'])
@pytest.mark.usefixtures('observable_git_environment')
def test_checkout_cleanup_reaps_nested_batches_and_preserves_source(local_repo, monkeypatch, outcome):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    batches = []
    popen, cleanup = subprocess.Popen, tempfile.TemporaryDirectory.cleanup

    def start(argv, *args, **kwargs):
        process = popen(argv, *args, **kwargs)
        if argv[:3] == ['git', 'cat-file', '--batch']:
            batches.append((Path(kwargs['cwd']).resolve(), process))
        return process

    def checked_cleanup(directory):
        path = Path(directory.name).resolve()
        held = [process.pid for cwd, process in batches
                if cwd.is_relative_to(path) and process.poll() is None]
        if held:
            raise PermissionError(32, f'checkout held by {held}', str(path))
        cleanup(directory)

    monkeypatch.setattr(subprocess, 'Popen', start)
    monkeypatch.setattr(tempfile.TemporaryDirectory, 'cleanup', checked_cleanup)
    with store.read_session():
        assert store.blob_bytes(root, 'HEAD:app.py') == b'items = []\n'
        source = batches[-1][1]
        expected = pytest.raises(RuntimeError, match='snapshot failed') if outcome == 'error' else nullcontext()
        with expected:
            with store.temporary_checkout(prefix='workorder-cleanup-test-') as temporary:
                directory = Path(temporary)
                for name in ('code', 'nested/ref'):
                    destination = directory / name
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    gate._ref_test_snapshot(root, destination, 'HEAD')
                    assert store.blob_bytes(destination, 'HEAD:app.py') == b'items = []\n'
                if outcome == 'error':
                    raise RuntimeError('snapshot failed')
                if outcome == 'timeout':
                    run = store.run_test([PYTHON, '-c', 'import time; time.sleep(60)'], destination, timeout=0.05)
                    assert run.returncode == 124
                if outcome == 'killed_batch':
                    batches[-1][1].kill()
                    batches[-1][1].wait(timeout=5)
        assert not directory.exists()
        assert source.poll() is None
        assert store.blob_bytes(root, 'HEAD:app.py') == b'items = []\n'
        temporary_batches = [process for cwd, process in batches if cwd.is_relative_to(directory)]
        assert len(temporary_batches) == 2
        assert all(process.poll() is not None and process.stdin.closed and process.stdout.closed
                   for process in temporary_batches)
        # Scoped cleanup removes closed entries; repeating it is harmless.
        store._READS.get().close(root=directory)
        assert source.poll() is None
    assert source.poll() is not None
