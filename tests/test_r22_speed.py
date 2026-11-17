"""Round 22 speed: real projects are too large for the read cache to observe, so reads whose answer
cannot change are shared anyway, and the pre-commit hook returns at once when nothing it checks is
staged. Verifiers measured done at 7-13 s and the hook at 4-16 s for a 0.05 s test."""

from pathlib import Path

import pytest

from workorder import gate, git_reads, source, store
from test_workorder import cli, git, local_repo, ok
from test_v05 import commit_all
from test_v16b_project import project


@pytest.fixture
def unobservable(monkeypatch):
    """A large work tree: the read cache cannot observe the repository state (rich, click, pydantic)."""
    monkeypatch.setattr(git_reads, 'repository_state', lambda *args, **kwargs: None)


def counted(monkeypatch, commands):
    original = store._git_execute

    def run(root, *args, **kwargs):
        commands.append(args)
        return original(root, *args, **kwargs)
    monkeypatch.setattr(store, '_git_execute', run)


def test_object_reads_share_one_git_process_when_the_state_cannot_be_observed(local_repo, unobservable,
                                                                              monkeypatch):
    root = local_repo
    head = store.head(root)
    started = []

    class Counted(store.Batch):
        def __init__(self, root):
            started.append(root)
            super().__init__(root)
    monkeypatch.setattr(store, 'Batch', Counted)
    with store.read_session():
        for _ in range(3):
            assert store.blob_bytes(root, f'{head}:app.py') == b'items = []\n'
            assert store.read_bytes(root, 'app.py', staged=True) == b'items = []\n'
        assert len(started) == 1, started  # was one cat-file process per read
        # A staged read follows the index: a new stage is read, never a remembered one.
        (root / 'app.py').write_bytes(b'items = [1]\n')
        git(root, 'add', 'app.py')
        assert store.read_bytes(root, 'app.py', staged=True) == b'items = [1]\n'
        assert store.blob_bytes(root, f'{head}:app.py') == b'items = []\n'


def test_listings_named_by_object_ids_run_once_and_follow_the_index(local_repo, unobservable, monkeypatch):
    root = local_repo
    head = store.head(root)
    commands = []
    counted(monkeypatch, commands)
    with store.read_session():
        for _ in range(3):
            assert gate.repository_files(root, head) == {'app.py'}
            assert gate.repository_files(root) == {'app.py'}
        assert commands.count(('ls-tree', '-r', '--name-only', '-z', head)) == 1, commands
        assert commands.count(('ls-files', '-z')) == 1, commands
        (root / 'new.py').write_text('x = 1\n')
        git(root, 'add', 'new.py')
        assert gate.repository_files(root) == {'app.py', 'new.py'}  # the index changed: listed again
        assert gate.raw_changed_paths(root, head, True) == {'new.py'}
        assert gate.repository_files(root, 'HEAD') == {'app.py'}  # a name, not an ID: always asked


def test_work_tree_changes_are_listed_once_per_outer_read(local_repo, unobservable, monkeypatch):
    root = local_repo
    head = store.head(root)
    commands = []
    counted(monkeypatch, commands)
    diff = ('diff', '--name-only', '-z', '--no-renames', head)
    with store.read_session():
        with store.read_scope(root):
            for _ in range(3):
                assert gate.raw_changed_paths(root, head) == set()
        assert commands.count(diff) == 1, commands  # was three
        (root / 'app.py').write_text('items = [2]\n')
        with store.read_scope(root):
            assert gate.raw_changed_paths(root, head) == {'app.py'}  # a new outer read looks again
        assert commands.count(diff) == 2, commands


def test_work_tree_changes_are_read_each_time_when_an_index_hook_can_run(local_repo, unobservable, monkeypatch):
    root = local_repo
    head = store.head(root)
    hook = Path(store.git(root, 'rev-parse', '--git-path', 'hooks/post-index-change').stdout.strip())
    hook = hook if hook.is_absolute() else root / hook
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text('#!/bin/sh\nexit 0\n')
    commands = []
    counted(monkeypatch, commands)
    with store.read_session(), store.read_scope(root):
        for _ in range(2):
            gate.raw_changed_paths(root, head)
    assert commands.count(('diff', '--name-only', '-z', '--no-renames', head)) == 2, commands


def test_each_python_source_is_normalized_once(monkeypatch):
    parsed = []
    original = source.python_code

    def code(content):
        parsed.append(content)
        return original(content)
    monkeypatch.setattr(source, 'python_code', code)
    before, after = b'def f():\n    return 1\n', b'def f():\n    """Doc."""\n    return 1  # note\n'
    for _ in range(3):
        assert source.same_python(before, after)
    assert not source.same_python(before, b'def f():\n    return 2\n')
    assert len(parsed) == 3, parsed  # was eight: both sides parsed on every comparison


def finished_branch(root):
    """A finished task committed on a feature branch whose code changed since origin/main."""
    project(root, with_task=True, broken_head=False)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)


def test_hook_returns_at_once_when_only_documentation_is_staged(local_repo):
    root = local_repo
    finished_branch(root)
    for staged in ([], ['README.md'], ['docs/guide.rst']):
        for path in staged:
            (root / path).parent.mkdir(exist_ok=True)
            (root / path).write_text('Words.\n')
            git(root, 'add', path)
        result = cli(root, 'check', '--staged')
        # Was: check: OK and a run of TASK-1's test ("ran 1 task's declared test") for every commit.
        assert (result.returncode, result.stdout) == (0, ''), result.stdout + result.stderr
        assert 'Time:' not in result.stderr
        git(root, 'reset', '-q', '--hard')
        git(root, 'clean', '-fdq')


def test_hook_still_checks_documentation_a_task_names_and_any_code(local_repo):
    root = local_repo
    finished_branch(root)
    task_file = root / 'tasks/TASK-1.md'
    task_file.write_text(task_file.read_text().replace('## Scope', '## Scope\n\nSee notes.md.', 1))
    commit_all(root)
    (root / 'notes.md').write_text('Words.\n')
    git(root, 'add', 'notes.md')
    named = cli(root, 'check', '--staged')
    assert named.returncode == 0 and 'checked 0 tasks' in named.stdout, named.stdout + named.stderr
    git(root, 'reset', '-q', '--hard')
    git(root, 'clean', '-fdq')
    (root / 'app.py').write_text('value = 1\nfeature = False\n')
    git(root, 'add', 'app.py')
    code = cli(root, 'check', '--staged')
    assert 'TASK-1: its code changed after it was finished (app.py)' in code.stdout, code.stdout + code.stderr


def test_a_claim_on_the_implemented_code_needs_no_replay_comparison(local_repo, monkeypatch):
    root = local_repo
    project(root, with_task=True, broken_head=False)
    ok(root, 'done', '--task', 'TASK-1')  # DONE in the work tree, on the code its receipt names
    replays = []
    original = gate.recorded_branch_code

    def replay(*args, **kwargs):
        replays.append(args)
        return original(*args, **kwargs)
    monkeypatch.setattr(gate, 'recorded_branch_code', replay)
    with store.read_session():
        lines, refused = gate.explain(root, 'TASK-1')
    assert not refused, lines
    assert replays == []  # done's two explains each compared every source file (2.6 s on rich)
