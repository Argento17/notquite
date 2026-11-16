"""Staged comparisons share a validated tree without rewriting the caller's index."""
from pathlib import Path
import subprocess

import pytest

from workorder import gate, store
from test_workorder import git, local_repo, task
from test_v05 import commit_all, evidence


@pytest.mark.parametrize('helper', ['index_code', 'stale_claim', 'changed_existing_test_paths',
                                   'receipt_covers_change'])
@pytest.mark.parametrize('alternate', [False, True])
@pytest.mark.parametrize('fresh', [False, True])
def test_staged_comparisons_preserve_caller_index(local_repo, monkeypatch, helper, alternate, fresh):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '0' if fresh else '1')
    root = local_repo
    task(root, status='IN_PROGRESS', ships='none')
    base = commit_all(root)
    evidence(root)
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    (root / 'app.py').write_text('items = [1]\n')
    git(root, 'add', '-A')
    original = root / '.git/index'
    index = root.parent / 'alternate-index' if alternate else original
    if alternate:
        index.write_bytes(original.read_bytes())
        monkeypatch.setenv('GIT_INDEX_FILE', str(index))
    tree = store.index_tree(root)
    expected_code = store.code_digest(root, tree)
    record = store.record(root, 'TASK-1', staged=True)
    implemented = gate.newest_implementation(store.entries(root, 'TASK-1', staged=True))
    before = index.read_bytes(), original.read_bytes()
    with store.read_session():
        if helper == 'index_code':
            assert store.index_code(root) == expected_code
        elif helper == 'stale_claim':
            assert 'NOT DONE' in gate.stale_claim(root, record, staged=True)
        elif helper == 'changed_existing_test_paths':
            expected = gate.changed_existing_test_paths(root, base, {'app.py'})
            assert gate.changed_existing_test_paths(root, base, {'app.py'}, staged=True) == expected
        else:
            assert not gate.receipt_covers_change(root, implemented, base, staged=True)
    assert (index.read_bytes(), original.read_bytes()) == before


def test_index_tree_reuses_unchanged_query_across_command_and_worker(local_repo, monkeypatch):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    expected = git(local_repo, 'write-tree')
    writes = []
    start = subprocess.Popen

    def counted(argv, *args, **kwargs):
        if Path(argv[0]).name == 'git' and list(argv[1:]) == ['write-tree']:
            writes.append(argv)
        return start(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', counted)
    with store.read_session():
        assert store.index_tree(local_repo) == expected
        with store.changing_repository():
            pass
        assert store.index_tree(local_repo) == expected
        seed = store._READS.get().fork()
    with store.read_session(seed=seed):
        assert store.index_tree(local_repo) == expected
    assert len(writes) == 1


@pytest.mark.parametrize('alternate', [False, True])
def test_shared_index_tree_observes_staging_and_environment(local_repo, monkeypatch, alternate):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    initial = git(root, 'write-tree')
    original = root / '.git/index'
    external = root.parent / 'alternate-index'
    external.write_bytes(original.read_bytes())
    with store.read_session():
        assert store.index_tree(root) == initial
        if alternate:
            monkeypatch.setenv('GIT_INDEX_FILE', str(external))
        (root / 'app.py').write_text('items = [1]\n')
        git(root, 'add', 'app.py')
        changed = store.index_tree(root)
        assert changed != initial
        with store.changing_repository():
            pass
        assert store.index_tree(root) == changed
        if alternate:
            monkeypatch.delenv('GIT_INDEX_FILE')
            assert store.index_tree(root) == initial


@pytest.mark.parametrize('alternate', [False, True])
@pytest.mark.parametrize('fresh', [False, True])
def test_absent_index_is_an_empty_tree_without_creating_caller_index(local_repo, monkeypatch, alternate, fresh):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '0' if fresh else '1')
    root = local_repo
    index = root.parent / 'missing-index' if alternate else root / '.git/index'
    if alternate:
        monkeypatch.setenv('GIT_INDEX_FILE', str(index))
    else:
        index.unlink()
    expected = store.git(root, 'mktree', data='').stdout.strip()
    with store.read_session():
        assert store.index_tree(root) == expected
        assert store.index_code(root) == store.code_digest(root, expected)
    assert not index.exists()


@pytest.mark.parametrize('layout', ['split', 'sparse'])
@pytest.mark.parametrize('fresh', [False, True])
def test_shared_tree_preserves_split_and_sparse_indexes(local_repo, monkeypatch, layout, fresh):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '0' if fresh else '1')
    root = local_repo
    for folder in ('kept', 'hidden'):
        (root / folder).mkdir()
        (root / folder / 'value.py').write_text('value = 1\n')
    commit_all(root)
    if layout == 'split':
        git(root, 'update-index', '--split-index')
    else:
        git(root, 'sparse-checkout', 'init', '--cone', '--sparse-index')
        git(root, 'sparse-checkout', 'set', 'kept')
    initial = git(root, 'rev-parse', 'HEAD^{tree}')
    index = root / '.git/index'
    before = index.read_bytes()
    with store.read_session():
        assert store.index_tree(root) == initial
        assert store.index_code(root) == store.code_digest(root, initial)
        assert index.read_bytes() == before
        (root / 'kept/value.py').write_text('value = 2\n')
        git(root, 'add', 'kept/value.py')
        before = index.read_bytes()
        changed = store.index_tree(root)
        assert changed != initial
        tree = store.code_tree(root, changed)
        assert tree['kept/value.py'].endswith(git(root, 'rev-parse', ':kept/value.py'))
        assert tree['hidden/value.py'] == store.code_tree(root, initial)['hidden/value.py']
        assert store.index_code(root) == store.code_digest(root, changed)
        assert index.read_bytes() == before
