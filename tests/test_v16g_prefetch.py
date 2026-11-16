"""Parallel test workers own bounded Git reads without sharing parent state."""
import subprocess
from pathlib import Path
from threading import Barrier

import pytest

from workorder import gate, store
from test_workorder import PYTHON, git, local_repo


@pytest.mark.parametrize('mode', ['current', 'snapshot', 'staged'])
def test_parallel_tests_own_distinct_read_sessions(local_repo, monkeypatch, mode):
    root = local_repo
    records = [{'id': f'TASK-{i}', 'test': store.join_command([PYTHON, '-c', f'print({i})'])}
               for i in (1, 2)]
    observed = []
    barrier = Barrier(2)
    actual = store.cached_test
    with store.read_session():
        parent = store._READS.get()
        parent['parent-marker'] = 'unchanged'

        def run(*args, **kwargs):
            worker = store._READS.get()
            barrier.wait(timeout=15)
            result = actual(*args, **kwargs)
            observed.append(worker)
            assert parent['parent-marker'] == 'unchanged'
            return result

        monkeypatch.setattr(store, 'cached_test', run)
        results = {}
        gate.prefetch_tests(root, records, results, jobs=2, snapshot=mode == 'snapshot', staged=mode == 'staged')
        assert store._READS.get() is parent
    assert sorted((code, output.strip()) for code, output in results.values()) == [(0, '1'), (0, '2')]
    assert len(observed) == 2
    assert all(worker is not None and worker is not parent for worker in observed)
    assert observed[0] is not observed[1]
    assert all(batch.process.poll() is not None for worker in observed for batch in worker.batches.values())


def test_parallel_test_reuses_head_reads_within_its_worker(local_repo, monkeypatch):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)  # measure the reuse contract
    commands = []
    actual = subprocess.Popen

    def start(argv, *args, **kwargs):
        if argv and argv[0] == 'git':
            commands.append(tuple(argv[1:]))
        return actual(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', start)
    record = {'id': 'TASK-1', 'test': store.join_command([PYTHON, '-c', 'print("checked")'])}
    results = {}
    gate.prefetch_tests(local_repo, [record], results, jobs=1)
    assert list(results.values()) == [(0, 'checked\n')]
    assert commands.count(('rev-parse', '--verify', 'HEAD^{commit}')) <= 1


def test_snapshot_worker_closes_git_before_deleting_its_checkout(local_repo, monkeypatch):
    batches = []
    alive_at_cleanup = []
    actual_process = subprocess.Popen
    actual_directory = gate.tempfile.TemporaryDirectory

    def start(argv, *args, **kwargs):
        process = actual_process(argv, *args, **kwargs)
        if argv[:3] == ['git', 'cat-file', '--batch']:
            batches.append((Path(kwargs['cwd']), process))
        return process

    class Directory(actual_directory):
        def __exit__(self, *args):
            if Path(self.name).name.startswith('workorder-parallel-'):
                alive_at_cleanup.extend(process.poll() is None for path, process in batches
                                        if path.is_relative_to(self.name))
            return super().__exit__(*args)

    monkeypatch.setattr(subprocess, 'Popen', start)
    monkeypatch.setattr(gate.tempfile, 'TemporaryDirectory', Directory)
    record = {'id': 'TASK-1', 'test': store.join_command([PYTHON, '-c', 'print("checked")'])}
    results = {}
    gate.prefetch_tests(local_repo, [record], results, jobs=1, snapshot=True)
    assert list(results.values()) == [(0, 'checked\n')]
    assert alive_at_cleanup
    assert not any(alive_at_cleanup)


def test_worker_reuses_validated_parent_git_queries(local_repo, monkeypatch):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    commands = []
    actual = subprocess.Popen

    def start(argv, *args, **kwargs):
        if argv and argv[0] == 'git':
            commands.append(tuple(argv[1:]))
        return actual(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', start)
    record = {'id': 'TASK-1', 'test': store.join_command([PYTHON, '-c', 'print("checked")'])}
    with store.read_session():
        store.git(local_repo, 'rev-parse', '--verify', 'HEAD^{commit}')
        commands.clear()
        results = {}
        gate.prefetch_tests(local_repo, [record], results, jobs=1)
    assert list(results.values()) == [(0, 'checked\n')]
    assert ('rev-parse', '--verify', 'HEAD^{commit}') not in commands
    assert ('config', '--null', '--list', '--show-origin', '--includes') not in commands


@pytest.mark.parametrize('change', ['ref', 'worktree', 'config', 'environment'])
def test_worker_validates_changes_after_parent_seed(local_repo, monkeypatch, change):
    root = local_repo
    before = git(root, 'rev-parse', 'HEAD')
    after = git(root, 'commit-tree', 'HEAD^{tree}', '-p', 'HEAD', '-m', 'new head')
    git(root, 'config', 'workorder.probe', 'old')
    query = (('rev-parse', 'HEAD') if change == 'ref' else
             ('diff', '--name-only', 'HEAD') if change == 'worktree' else
             ('config', '--get', 'workorder.probe'))
    expected = after if change == 'ref' else 'app.py' if change == 'worktree' else 'new'
    actual = store.cached_test
    observed = []

    def run(*args, **kwargs):
        if change == 'ref':
            git(root, 'update-ref', 'HEAD', after)
        elif change == 'worktree':
            (root / 'app.py').write_text('items = [1]\n')
        elif change == 'config':
            git(root, 'config', 'workorder.probe', 'new')
        else:
            monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
            monkeypatch.setenv('GIT_CONFIG_KEY_0', 'workorder.probe')
            monkeypatch.setenv('GIT_CONFIG_VALUE_0', 'new')
        observed.append(store.git(root, *query).stdout.strip())
        return actual(*args, **kwargs)

    monkeypatch.setattr(store, 'cached_test', run)
    record = {'id': 'TASK-1', 'test': store.join_command([PYTHON, '-c', 'print("checked")'])}
    with store.read_session():
        assert store.git(root, *query).stdout.strip() == (before if change == 'ref' else '' if change == 'worktree' else 'old')
        gate.prefetch_tests(root, [record], {}, jobs=1)
    assert observed == [expected]


def test_worker_observation_seed_is_independent_and_excludes_derived_and_processes(local_repo, monkeypatch):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    actual = store.cached_test
    observed = []
    record = {'id': 'TASK-1', 'test': store.join_command([PYTHON, '-c', 'print("checked")'])}
    with store.read_session():
        parent = store._READS.get()
        store.blob_bytes(local_repo, 'HEAD:app.py')
        parent['derived-marker'] = 'parent only'
        parent.configurations['marker'] = {'values': ['parent']}

        def run(*args, **kwargs):
            worker = store._READS.get()
            assert worker is not parent
            assert 'derived-marker' not in worker
            assert not worker.batches
            assert worker.configurations['marker'] == {'values': ['parent']}
            worker.configurations['marker']['values'].append('worker')
            assert parent.configurations['marker'] == {'values': ['parent']}
            assert worker.states is not parent.states
            assert worker.queries is not parent.queries
            assert worker.contents is not parent.contents
            assert worker.snapshots is not parent.snapshots
            observed.append(worker)
            return actual(*args, **kwargs)

        monkeypatch.setattr(store, 'cached_test', run)
        gate.prefetch_tests(local_repo, [record], {}, jobs=1)
        assert all(batch.process.poll() is None for batch in parent.batches.values())
    assert len(observed) == 1
