"""Command-local Git reuse must not hide changes to the evidence it reads."""
import os
import subprocess

import pytest

from workorder import cli, store
from test_workorder import PYTHON, git, local_repo, task
from test_v05 import commit_all
from test_v06b import fill_sections


@pytest.fixture
def git_spawns(monkeypatch):
    calls = []
    popen = subprocess.Popen

    def counted(args, *a, **kw):
        if isinstance(args, (list, tuple)) and args[0] == 'git':
            calls.append(tuple(args[1:]))
        return popen(args, *a, **kw)

    monkeypatch.setattr(subprocess, 'Popen', counted)
    return calls


def test_done_git_subprocess_budget(local_repo, monkeypatch, git_spawns):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n')
    (root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    fill_sections(task(root, ships='none', test=f'{PYTHON} -m pytest -q tests/test_mul.py'))
    commit_all(root)
    git(root, 'switch', '-qc', 'feature')
    (root / 'app.py').write_text('def mul(a, b):\n    return a * b\n')
    (root / 'tests').mkdir()
    (root / 'tests/test_mul.py').write_text('from app import mul\n\ndef test_mul():\n    assert mul(3, 4) == 12\n')
    commit_all(root)
    monkeypatch.chdir(root)
    git_spawns.clear()
    assert cli.main(['done', '--task', 'TASK-1']) == 0
    assert len(git_spawns) <= 180, len(git_spawns)


def test_workorder_write_refreshes_worktree_query(local_repo, git_spawns):
    root = local_repo
    task(root)
    commit_all(root)
    query = ('diff', '--name-only', 'HEAD')
    with store.read_session():
        assert store.git(root, *query).stdout == ''
        assert store.git(root, *query).stdout == ''
        assert git_spawns.count(query) == 1
        store.add_note(root, 'TASK-1', 'Changed by Workorder')
        assert store.git(root, *query).stdout == 'tasks/TASK-1.md\n'
        assert store.git(root, *query).stdout == 'tasks/TASK-1.md\n'
        assert git_spawns.count(query) == 2


@pytest.mark.parametrize('timeout', [None, 10])
def test_test_command_refreshes_worktree_query(local_repo, timeout):
    root = local_repo
    with store.read_session():
        assert store.git(root, 'diff', '--name-only', 'HEAD').stdout == ''
        run = store.run_test([PYTHON, '-c', 'from pathlib import Path; Path("app.py").write_text("changed\\n")'],
                             root, timeout)
        assert run.returncode == 0, run.stderr
        assert store.git(root, 'diff', '--name-only', 'HEAD').stdout == 'app.py\n'


@pytest.mark.parametrize('state', ['worktree', 'untracked', 'index', 'head', 'ref', 'config'])
def test_read_session_observes_external_changes(local_repo, state):
    root = local_repo
    queries = {
        'worktree': ('diff', '--name-only', 'HEAD'),
        'untracked': ('ls-files', '--others', '--exclude-standard'),
        'index': ('show', ':app.py'),
        'head': ('rev-parse', 'HEAD'),
        'ref': ('rev-parse', 'main'),
        'config': ('config', '--get', 'user.name'),
    }
    query = queries[state]
    with store.read_session():
        before = store.git(root, *query).stdout
        if state in {'head', 'ref'}:
            git(root, 'switch', '-qc', 'feature')
        if state == 'config':
            git(root, 'config', 'user.name', 'changed')
        elif state == 'untracked':
            (root / 'new.py').write_text('new\n')
        else:
            path = root / 'app.py'
            stamp = path.stat()
            path.write_text('items = [1]\n')  # same size and restored mtime
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
            if state in {'index', 'head', 'ref'}:
                git(root, 'add', 'app.py')
            if state in {'head', 'ref'}:
                git(root, 'commit', '-qm', 'Changed')
            if state == 'ref':
                git(root, 'branch', '-f', 'main', 'HEAD')
        assert store.git(root, *query).stdout != before


def test_batch_process_reused_and_closed(local_repo, git_spawns):
    root = local_repo
    first = git(root, 'rev-parse', 'HEAD:app.py')
    (root / 'second.txt').write_bytes(b'\xef\xbb\xbfsecond\r\n\xff\n')
    commit_all(root)
    second = git(root, 'rev-parse', 'HEAD:second.txt')
    with store.read_session():
        assert store.read_blobs(root, [first])[first] == 'items = []\n'
        store.invalidate_reads()
        assert store.read_blobs(root, [second])[second] == 'second\n\udcff\n'
        assert store.read_blobs(root, [first])[first] == 'items = []\n'
        assert git_spawns.count(('cat-file', '--batch')) == 1
    with store.read_session():
        assert store.read_blobs(root, [first])[first] == 'items = []\n'
    assert git_spawns.count(('cat-file', '--batch')) == 2


@pytest.mark.parametrize('state', ['index', 'head', 'replace'])
def test_batch_observes_changed_git_state(local_repo, state):
    root = local_repo
    original = git(root, 'rev-parse', 'HEAD:app.py')
    spec = ':app.py' if state == 'index' else 'HEAD:app.py' if state == 'head' else original
    with store.read_session():
        assert store.blob_bytes(root, spec) == b'items = []\n'
        (root / 'app.py').write_text('items = [2]\n')
        git(root, 'add', 'app.py')
        if state == 'head':
            git(root, 'commit', '-qm', 'Changed')
        if state == 'replace':
            replacement = git(root, 'rev-parse', ':app.py')
            git(root, 'replace', original, replacement)
        assert store.blob_bytes(root, spec) == b'items = [2]\n'


def test_batch_preserves_binary_and_newline_paths(local_repo):
    root = local_repo
    name = 'odd\nname' if os.name != 'nt' else 'odd name'
    raw = b'\x00\xff\xef\xbb\xbf\r\n'
    (root / name).write_bytes(raw)
    commit_all(root)
    with store.read_session():
        assert store.read_bytes(root, name, staged=True) == raw
        assert store.blob_bytes(root, 'HEAD:missing') is None
        assert store.blob_bytes(root, 'HEAD:app.py') == b'items = []\n'


@pytest.mark.parametrize('setting', ['include.path', 'core.excludesFile'])
def test_external_git_config_inputs_are_not_stale(local_repo, setting):
    root = local_repo
    external = root.parent / 'git-settings'
    if setting == 'include.path':
        external.write_text('[user]\nname = first\n')
        query = ('config', '--get', 'user.name')
        updated = '[user]\nname = second\n'
    else:
        (root / 'hidden').write_text('value')
        external.write_text('hidden\n')
        query = ('ls-files', '--others', '--exclude-standard')
        updated = ''
    git(root, 'config', setting, str(external))
    with store.read_session():
        before = store.git(root, *query).stdout
        external.write_text(updated)
        assert store.git(root, *query).stdout != before


def test_missing_blob_with_spaces_does_not_break_batch(local_repo):
    with store.read_session():
        assert store.blob_bytes(local_repo, 'HEAD:not here') is None
        assert store.blob_bytes(local_repo, 'HEAD:app.py') == b'items = []\n'


def test_submodule_head_change_invalidates_parent_query(local_repo):
    root = local_repo
    sub = root / 'sub'
    sub.mkdir()
    git(sub, 'init', '-q', '-b', 'main')
    git(sub, 'config', 'user.name', 'test')
    git(sub, 'config', 'user.email', 'test@example.invalid')
    (sub / 'data').write_text('same bytes')
    commit_all(sub)
    commit_all(root)
    with store.read_session():
        assert store.git(root, 'diff', '--name-only', 'HEAD').stdout == ''
        git(sub, 'commit', '--allow-empty', '-qm', 'Move gitlink')
        assert store.git(root, 'diff', '--name-only', 'HEAD').stdout == 'sub\n'


def test_isolated_query_observes_injected_config(local_repo, monkeypatch):
    root = local_repo
    external = root.parent / 'extra-config'
    external.write_text('[user]\nname = first\n')
    monkeypatch.setenv('GIT_CONFIG_COUNT', '1')
    monkeypatch.setenv('GIT_CONFIG_KEY_0', 'include.path')
    monkeypatch.setenv('GIT_CONFIG_VALUE_0', str(external))
    with store.read_session():
        assert store._git_run(root, 'config', '--get', 'user.name', isolated=True).stdout == 'first\n'
        external.write_text('[user]\nname = second\n')
        assert store._git_run(root, 'config', '--get', 'user.name', isolated=True).stdout == 'second\n'


def test_deleted_object_cannot_be_returned_from_cache(local_repo):
    root = local_repo
    oid = git(root, 'rev-parse', 'HEAD:app.py')
    with store.read_session():
        assert store.blob_bytes(root, oid) == b'items = []\n'
        (root / '.git/objects' / oid[:2] / oid[2:]).unlink()
        assert store.blob_bytes(root, oid) is None


def test_head_and_blob_share_batch(local_repo, git_spawns):
    expected = git(local_repo, 'rev-parse', 'HEAD')
    git_spawns.clear()
    with store.read_session():
        assert store.head(local_repo) == expected
        assert store.blob_bytes(local_repo, 'HEAD:app.py') == b'items = []\n'
    assert len(git_spawns) == 1, git_spawns


def test_dead_batch_is_reaped_and_can_be_retried(local_repo):
    root = local_repo
    with store.read_session():
        assert store.blob_bytes(root, 'HEAD:app.py') == b'items = []\n'
        process = next(iter(store._READS.get().batches.values())).process
        process.kill()
        process.wait(timeout=5)
        with pytest.raises(store.Refused):
            store.blob_bytes(root, 'HEAD:missing')
        assert store.blob_bytes(root, 'HEAD:./app.py') == b'items = []\n'
    assert process.poll() is not None
