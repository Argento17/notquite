"""Compound claim reads reuse work while observing each new repository state."""
import pytest

from workorder import gate, git_reads, store
from test_workorder import git, local_repo, task
from test_v05 import commit_all, finished


@pytest.mark.parametrize('staged', [False, True])
@pytest.mark.usefixtures('observable_git_environment')
def test_finished_code_change_checks_repository_once(local_repo, monkeypatch, staged):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    base = store.head(local_repo)
    finished(local_repo)
    record = store.record(local_repo, 'TASK-1')
    inspections = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        inspections.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    with store.read_session():
        for _ in range(2):
            assert gate.finished_code_change(local_repo, base, record, staged) is None
    assert len(inspections) == 2, len(inspections)


@pytest.mark.parametrize('staged', [False, True])
@pytest.mark.parametrize('fresh', [False, True])
def test_finished_code_change_observes_code_index_and_record(local_repo, monkeypatch, staged, fresh):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '0' if fresh else '1')
    root = local_repo
    base = store.head(root)
    finished(root)
    record = store.record(root, 'TASK-1')
    path = root / 'app.py'
    with store.read_session():
        assert gate.finished_code_change(root, base, record, staged) is None
        path.write_text('items = [1]\n')
        if staged:
            assert gate.finished_code_change(root, base, record, staged) is None
            git(root, 'add', 'app.py')
        assert 'app.py' in gate.finished_code_change(root, base, record, staged)
        record['status'] = 'IN_PROGRESS'
        assert gate.finished_code_change(root, base, record, staged) is None
        record['status'] = 'DONE'
        assert 'app.py' in gate.finished_code_change(root, base, record, staged)
        path.write_text('items = []\n')
        if staged:
            git(root, 'add', 'app.py')
        assert gate.finished_code_change(root, base, record, staged) is None


@pytest.mark.parametrize('helper', ['inherited_claim', 'completion_changed'])
@pytest.mark.parametrize('staged', [False, True])
@pytest.mark.usefixtures('observable_git_environment')
def test_claim_read_checks_repository_once(local_repo, monkeypatch, helper, staged):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    task(local_repo, status='DONE')
    base = commit_all(local_repo)
    record = store.record(local_repo, 'TASK-1')
    args = ((local_repo, record, staged) if helper == 'inherited_claim' else
            (local_repo, base, record, staged))
    inspections = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        inspections.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    with store.read_session():
        for _ in range(2):
            assert not getattr(gate, helper)(*args)
    assert len(inspections) == 2, len(inspections)


def test_inherited_claim_observes_merge_head_and_worktree_changes(local_repo):
    root = local_repo
    git(root, 'checkout', '-qb', 'donor')
    task(root, status='DONE')
    donor = commit_all(root)
    path = root / 'tasks/TASK-1.md'
    text = path.read_text()
    git(root, 'checkout', '-q', 'main')
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    record = store.record(root, 'TASK-1')
    merge_head = root / '.git/MERGE_HEAD'
    with store.read_session():
        assert not gate.inherited_claim(root, record)
        merge_head.write_text(donor + '\n')
        assert gate.inherited_claim(root, record)
        path.write_text(text.replace('status: "DONE"', 'status: "CLOSED"'))
        assert not gate.inherited_claim(root, record)
        path.write_text(text)
        assert gate.inherited_claim(root, record)
        merge_head.unlink()
        assert not gate.inherited_claim(root, record)


def test_completion_changed_observes_reopen_history_and_record_changes(local_repo):
    root = local_repo
    task(root, status='DONE')
    base = commit_all(root)
    record = store.record(root, 'TASK-1')
    path = root / 'tasks/TASK-1.md'
    text = path.read_text()
    with store.read_session():
        assert not gate.completion_changed(root, base, record)
        path.write_text(text.replace('status: "DONE"', 'status: "IN_PROGRESS"'))
        commit_all(root)
        path.write_text(text)
        commit_all(root)
        assert gate.completion_changed(root, base, record)
        record['status'] = 'IN_PROGRESS'
        assert not gate.completion_changed(root, base, record)
        record['status'] = 'DONE'
        assert gate.completion_changed(root, base, record)


@pytest.mark.parametrize('staged', [False, True])
@pytest.mark.usefixtures('observable_git_environment')
def test_changed_paths_checks_repository_once(local_repo, monkeypatch, staged):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    base = git(root, 'rev-parse', 'HEAD')
    (root / 'app.py').write_text('items = [1]\n')
    git(root, 'add', 'app.py')
    inspections = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        inspections.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    with store.read_session():
        for _ in range(2):
            assert gate.changed_paths(root, base, staged) == {'app.py'}
    assert len(inspections) == 2, len(inspections)


def test_changed_paths_observes_worktree_index_head_and_return_mutation(local_repo):
    root = local_repo
    path = root / 'app.py'
    original = path.read_text()
    path.write_text('items = [1]\n')
    git(root, 'add', 'app.py')
    with store.read_session():
        assert gate.changed_paths(root, 'HEAD') == {'app.py'}
        gate.changed_paths(root, 'HEAD', True).clear()
        assert gate.changed_paths(root, 'HEAD', True) == {'app.py'}
        path.write_text(original)
        assert gate.changed_paths(root, 'HEAD') == set()
        assert gate.changed_paths(root, 'HEAD', True) == {'app.py'}
        git(root, 'add', 'app.py')
        assert gate.changed_paths(root, 'HEAD', True) == set()
        path.write_text('items = [2]\n')
        git(root, 'add', 'app.py')
        assert gate.changed_paths(root, 'HEAD', True) == {'app.py'}
        commit_all(root)
        assert gate.changed_paths(root, 'HEAD', True) == set()
        assert gate.changed_paths(root, 'HEAD') == set()
