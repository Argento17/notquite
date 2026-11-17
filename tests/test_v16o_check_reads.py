"""Gate phases share observations; Windows still reads bytes at each boundary."""
import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from workorder import gate, git_reads, store
from test_workorder import PYTHON, git, local_repo, task
from test_v05 import commit_all


def observation_platform(monkeypatch, name):
    # Patch only the observer's os module: changing the real os.name would
    # make pathlib try to construct WindowsPath objects on a POSIX host.
    monkeypatch.setattr(git_reads, 'os', SimpleNamespace(**{**vars(os), 'name': name}))


@pytest.fixture
def shared_test_repo(local_repo):
    """The same 40-task fixture as the reported shared-test review timeout."""
    root = local_repo
    (root / 'tests').mkdir()
    probe = root / 'tests/test_f7.py'
    probe.write_text('def test_value():\n    assert int(7) > 0\n')
    command = [PYTHON, '-m', 'pytest', '-q', 'tests']
    for n in range(1, 41):
        task(root, f'TASK-{n}', ships='none', test=store.join_command(command))
    commit_all(root)
    for n in range(1, 41):
        receipt = store.receipt(root, f'TASK-{n}', 'implemented')
        store.append(root, f'TASK-{n}', dict(kind='test', commit=receipt['commit'], code=receipt['code'],
                                           command=command, exit_code=0, valid=True))
        store.set_task_field(root, f'TASK-{n}', 'status', 'DONE')
    commit_all(root)
    probe.write_text('def test_value():\n    assert int(7) >= 0\n')
    return root


@pytest.mark.parametrize('platform', ['native', 'nt'])
@pytest.mark.usefixtures('observable_git_environment')
def test_many_task_check_has_bounded_observations(shared_test_repo, monkeypatch, platform):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '1')
    observation_platform(monkeypatch, os.name if platform == 'native' else platform)
    observations, reads = [], []
    inspect = git_reads.repository_state
    read = Path.read_bytes
    observing = False

    def counted_state(*args, **kwargs):
        nonlocal observing
        observations.append(args[0])
        observing = True
        try:
            return inspect(*args, **kwargs)
        finally:
            observing = False

    def counted_read(path):
        if observing:
            reads.append(path)
        return read(path)

    monkeypatch.setattr(git_reads, 'repository_state', counted_state)
    monkeypatch.setattr(Path, 'read_bytes', counted_read)
    problems = gate.check(shared_test_repo, base='HEAD')
    changed = [line for line in problems if 'lost or changed' in line]
    assert len(changed) == 1, problems
    assert all(f'TASK-{n}' in changed[0] for n in range(1, 41))
    # Validation is command-wide, not proportional to tasks or nested helpers.
    assert len(observations) <= 3, len(observations)
    assert len(reads) <= 1100, len(reads)


@pytest.mark.usefixtures('observable_git_environment')
def test_windows_shared_review_matches_uncached_results(shared_test_repo, monkeypatch):
    observation_platform(monkeypatch, 'nt')
    for allow in [(), ('TASK-7',)]:
        results = []
        for cache in ['1', '0']:
            monkeypatch.setenv('WORKORDER_GIT_CACHE', cache)
            notes, counts = [], {}
            problems = gate.check(shared_test_repo, base='HEAD', allow=allow, notes=notes, counts=counts)
            results.append((problems, notes, counts))
        assert results[0] == results[1]
        assert bool(results[0][0]) is (not allow)


@pytest.mark.parametrize('platform', ['native', 'nt'])
@pytest.mark.usefixtures('observable_git_environment')
def test_restored_mtime_edit_is_observed_between_scopes(local_repo, monkeypatch, platform):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '1')
    observation_platform(monkeypatch, os.name if platform == 'native' else platform)
    path = local_repo / 'app.py'
    path.write_bytes(b'items = []\n')
    before = path.stat()
    with store.read_session():
        with store.read_scope(local_repo):
            original = store._READS.get().states[(local_repo, False)]
        path.write_bytes(b'items = ()\n')
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
        assert path.stat().st_size == before.st_size
        assert path.stat().st_mtime_ns == before.st_mtime_ns
        with store.read_scope(local_repo):
            assert store._READS.get().states[(local_repo, False)] != original


@pytest.mark.parametrize('cache', ['0', '1'])
@pytest.mark.usefixtures('observable_git_environment')
def test_windows_check_refreshes_after_test_restores_mtime(local_repo, monkeypatch, cache):
    root = local_repo
    observation_platform(monkeypatch, 'nt')
    monkeypatch.setenv('WORKORDER_GIT_CACHE', cache)
    # Name the source checkout explicitly, including if prefetch uses a copy.
    # The mutation tampers with an already-read committed receipt, preserving
    # its size, file identity and mtime. The following task checks must refuse.
    target = root / '.workorder/receipts/TASK-1'
    code = (f'from pathlib import Path; import os, json; '
            f'p = next(p for p in Path({json.dumps(str(target))}).glob("*.json") '
            'if json.loads(p.read_text())["kind"] == "implemented"); '
            's = p.stat(); b = p.read_bytes(); '
            'p.write_bytes(b.replace(b"implemented", b"ImplementEd")); '
            'os.utime(p, ns=(s.st_atime_ns, s.st_mtime_ns)); '
            'assert p.stat().st_size == s.st_size; assert p.stat().st_mtime_ns == s.st_mtime_ns')
    command = [PYTHON, '-c', code]
    task(root, ships='none', test=store.join_command(command))
    commit_all(root)
    receipt = store.receipt(root, 'TASK-1', 'implemented')
    store.append(root, 'TASK-1', dict(kind='test', commit=receipt['commit'], code=receipt['code'],
                                   command=command, exit_code=0, valid=True))
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    commit_all(root)
    problems = gate.check(root, run_tests=True, all_tests=True, jobs=1)
    assert any('committed receipt file removed or changed' in line for line in problems), problems


@pytest.mark.usefixtures('observable_git_environment')
def test_scope_resumes_shared_validation_after_external_command(local_repo, monkeypatch):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '1')
    observation_platform(monkeypatch, 'nt')
    observations = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        observations.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    with store.read_session(), store.read_scope(local_repo):
        assert store.git(local_repo, 'config', '--get', 'user.name').stdout == 'solo\n'
        assert store.run_test([PYTHON, '-c', 'pass'], local_repo).returncode == 0
        observations.clear()
        for _ in range(3):
            assert store.git(local_repo, 'config', '--get', 'user.name').stdout == 'solo\n'
    assert len(observations) == 1, len(observations)


def mutation_hook(root, tmp_path, name, *, failing=False, global_config=False):
    marker = root / 'NEW'
    script = tmp_path / 'hook.py'
    script.write_text('from pathlib import Path\nimport sys\n'
                      + ('sys.stdin.read()\n' if name == 'reference-transaction' else '')
                      + f'Path({str(marker)!r}).write_text("changed")\n'
                      + ('raise SystemExit(1)\n' if failing else ''))
    hooks = tmp_path / 'hooks'
    hooks.mkdir()
    hook = hooks / name
    hook.write_text('#!/bin/sh\nexec ' + shlex.join([Path(PYTHON).as_posix(), script.as_posix()]) + '\n')
    hook.chmod(0o755)
    git(root, 'config', *(['--global'] if global_config else []), 'core.hooksPath', hooks.as_posix())
    return marker


@pytest.mark.parametrize('operation', ['status', 'diff', 'index_tree'])
@pytest.mark.usefixtures('observable_git_environment')
def test_index_refresh_hook_invalidates_enclosing_scope(local_repo, tmp_path, monkeypatch, operation):
    root = local_repo
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '1')
    observation_platform(monkeypatch, 'nt')
    path = root / 'app.py'
    if operation == 'index_tree':
        path.write_text('items = [4]\n')
        git(root, 'add', 'app.py')
    else:
        before = path.stat()
        os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns + 2_000_000_000))
    git(root, 'config', 'diff.autoRefreshIndex', 'true')
    marker = mutation_hook(root, tmp_path, 'post-index-change')
    query = ('ls-files', '--others', '--exclude-standard')
    with store.read_session(), store.read_scope(root):
        assert store.git(root, *query).stdout == ''
        if operation == 'index_tree':
            store.index_tree(root)
        else:
            store.git(root, operation, *(['--porcelain'] if operation == 'status' else ['--name-only', 'HEAD']))
        assert marker.exists(), 'the real Git command must execute its hook'
        assert store.git(root, *query).stdout == 'NEW\n'


@pytest.mark.parametrize('helper', ['checkout_bytes', 'checkout_contents'])
@pytest.mark.usefixtures('observable_git_environment')
def test_index_only_filter_invalidates_enclosing_scope(local_repo, tmp_path, monkeypatch, helper):
    root = local_repo
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '1')
    observation_platform(monkeypatch, 'nt')
    marker = root / 'NEW'
    script = tmp_path / 'filter.py'
    script.write_text('from pathlib import Path\nimport sys\n'
                      f'Path({str(marker)!r}).write_text("changed")\n'
                      'sys.stdout.buffer.write(sys.stdin.buffer.read())\n')
    git(root, 'config', 'filter.mutate.smudge', shlex.join([Path(PYTHON).as_posix(), script.as_posix()]))
    attributes = root / '.gitattributes'
    attributes.write_text('app.py filter=mutate\n')
    commit_all(root)
    git(root, 'config', 'filter.mutate.required', 'true')
    attributes.write_text('app.py -filter\n')
    store.index_tree(root)
    assert git_reads.repository_state(root) is not None
    query = ('ls-files', '--others', '--exclude-standard')
    with store.read_session(), store.read_scope(root):
        assert store.git(root, *query).stdout == ''
        if helper == 'checkout_bytes':
            assert store.checkout_bytes(root, 'app.py') == b'items = []\n'
        else:
            assert list(store.checkout_contents(root, [('app.py', '', '100644')])) == [
                ('app.py', '100644', b'items = []\n')]
        assert marker.exists(), 'the index-selected filter must actually run'
        assert store.git(root, *query).stdout == 'NEW\n'


@pytest.mark.parametrize('helper', ['_test_snapshot', '_ref_test_snapshot'])
@pytest.mark.usefixtures('observable_git_environment')
def test_failed_clone_hook_invalidates_enclosing_scope(local_repo, tmp_path, monkeypatch, helper):
    root = local_repo
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '1')
    observation_platform(monkeypatch, 'nt')
    marker = mutation_hook(root, tmp_path, 'reference-transaction', failing=True, global_config=True)
    query = ('ls-files', '--others', '--exclude-standard')
    with store.read_session(), store.read_scope(root):
        assert store.git(root, *query).stdout == ''
        with pytest.raises(store.Refused, match='could not create a temporary'):
            args = (root, tmp_path / 'copy', 'HEAD') if helper == '_ref_test_snapshot' else (root, tmp_path / 'copy')
            getattr(gate, helper)(*args)
        assert marker.exists(), 'the failed clone must execute its hook'
        assert store.git(root, *query).stdout == 'NEW\n'


@pytest.mark.usefixtures('observable_git_environment')
def test_diff_helper_probe_cannot_hide_hook_write_on_cached_query(local_repo, tmp_path, monkeypatch):
    root = local_repo
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '1')
    observation_platform(monkeypatch, 'nt')
    git(root, 'config', 'diff.unused.command', 'unused-command')
    marker = mutation_hook(root, tmp_path, 'post-index-change')
    code = (f'from pathlib import Path; import os; p = Path({json.dumps(str(root / "app.py"))}); '
            's = p.stat(); os.utime(p, ns=(s.st_atime_ns, s.st_mtime_ns + 2000000000))')
    query = ('ls-files', '--others', '--exclude-standard')
    with store.read_session(), store.read_scope(root):
        assert store.git(root, 'diff', 'HEAD').stdout == ''
        assert store.git(root, *query).stdout == ''
        assert not marker.exists()
        assert store.run_test([PYTHON, '-c', code], root).returncode == 0
        assert store.git(root, 'diff', 'HEAD').stdout == ''
        assert marker.exists()
        assert store.git(root, *query).stdout == 'NEW\n'


@pytest.mark.usefixtures('observable_git_environment')
def test_empty_hook_directory_falls_back_to_fresh_reads(local_repo):
    git(local_repo, 'config', 'core.hooksPath', '')
    assert git_reads.repository_state(local_repo) is None


@pytest.mark.usefixtures('observable_git_environment')
def test_windows_executable_index_hook_bypasses_query_cache(local_repo, monkeypatch):
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '1')
    monkeypatch.setattr(store, 'os', SimpleNamespace(**{**vars(os), 'name': 'nt'}))
    (local_repo / '.git/hooks/post-index-change.exe').write_bytes(b'executable hook')
    execute = store._git_execute
    calls = []

    def counted(root, *args, **kwargs):
        if args[:1] == ('diff',):
            calls.append(args)
            # Exercise Windows hook discovery without needing a native .exe.
            return subprocess.CompletedProcess(['git', *args], 0, '', '')
        return execute(root, *args, **kwargs)

    monkeypatch.setattr(store, '_git_execute', counted)
    with store.read_session(), store.read_scope(local_repo):
        for _ in range(2):
            assert store.git(local_repo, 'diff', '--name-only', 'HEAD').stdout == ''
    assert len(calls) == 2
