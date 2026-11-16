"""Fresh reads and command-local reuse must enforce the same evidence rules."""
import pytest

from test_workorder import local_repo
from test_v12 import (
    test_recorded_squash_paths_keep_deletions_modes_and_content as _squash_paths,
    test_parallel_squashes_survive_deleted_branches_in_fresh_clone as _parallel_squashes,
)


@pytest.mark.parametrize('change', ['delete', 'mode', 'content'])
def test_uncached_squash_claim_checks_recorded_paths(local_repo, tmp_path, monkeypatch, change):
    # This unmodelled environment also forces fresh reads in the pre-fix release.
    monkeypatch.setenv('GIT_CONFIG_COUNT', '0')
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '0')
    _squash_paths(local_repo, tmp_path, change)


@pytest.mark.parametrize('ships', ['none', 'repo'])
def test_uncached_parallel_squash_claim_checks_recorded_paths(local_repo, tmp_path, monkeypatch, ships):
    monkeypatch.setenv('GIT_CONFIG_COUNT', '0')
    monkeypatch.setenv('WORKORDER_GIT_CACHE', '0')
    _parallel_squashes(local_repo, tmp_path, ships)


@pytest.mark.parametrize('mode', ['explicit', 'unobservable'])
def test_explicit_fresh_mode_does_not_reuse_nested_reads(local_repo, monkeypatch, mode):
    from workorder import store
    from workorder import git_reads
    import subprocess

    if mode == 'explicit':
        monkeypatch.setenv('WORKORDER_GIT_CACHE', '0')
    else:
        monkeypatch.setattr(git_reads, 'repository_state', lambda *a, **kw: None)
    calls = []
    popen = subprocess.Popen

    def counted(args, *a, **kw):
        if args == ['git', 'rev-parse', 'HEAD']:
            calls.append(args)
        return popen(args, *a, **kw)

    monkeypatch.setattr(subprocess, 'Popen', counted)

    @store.cached_read
    def pair(root):
        return [store.git(root, 'rev-parse', 'HEAD').stdout for _ in range(2)]

    with store.read_session():
        first = pair(local_repo)
        assert pair(local_repo) == first
    assert len(calls) == 4


def test_mutating_a_read_result_does_not_change_later_reads(local_repo):
    from workorder import store
    from test_workorder import task
    task(local_repo)
    with store.read_session():
        original = store.record(local_repo, 'TASK-1')
        original['status'] = 'CLOSED'
        original['waits_on'].append('person:unrelated')
        again = store.record(local_repo, 'TASK-1')
        assert again['status'] == 'IN_PROGRESS'
        assert again['waits_on'] == []


def test_unchanged_state_does_not_reread_file_contents(local_repo, monkeypatch):
    """Freshness checks use POSIX change times even when mtime is restored."""
    import os
    from pathlib import Path
    from workorder import store
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    if os.name == 'nt':
        pytest.skip('Windows ctime is creation time; contents must be reread')
    reads = []
    read_bytes = Path.read_bytes

    def counted(path):
        if path == local_repo / 'app.py':
            reads.append(path)
        return read_bytes(path)

    monkeypatch.setattr(Path, 'read_bytes', counted)
    with store.read_session():
        assert store.git(local_repo, 'diff', '--name-only', 'HEAD').stdout == ''
        first = len(reads)
        for _ in range(4):
            assert store.git(local_repo, 'diff', '--name-only', 'HEAD').stdout == ''
        assert first and len(reads) == first
        path = local_repo / 'app.py'
        stamp = path.stat()
        path.write_text('items = [1]\n')
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert store.git(local_repo, 'diff', '--name-only', 'HEAD').stdout == 'app.py\n'


@pytest.mark.parametrize('helper', ['test_files', 'strict_test_files', 'refactor_test_files', 'selected_test_files'])
def test_compound_test_selection_checks_state_once(local_repo, monkeypatch, helper):
    from workorder import gate, git_reads, store
    from test_workorder import PYTHON, task
    from test_v05 import commit_all
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    (root / 'tests').mkdir()
    (root / 'tests/test_a.py').write_text('def test_a():\n    assert True\n')
    task(root, test=f'{PYTHON} -m pytest tests/test_a.py')
    base = commit_all(root)
    record = store.record(root, 'TASK-1')
    states = []
    inspect = git_reads.repository_state

    def counted(*args, **kwargs):
        states.append(args[0])
        return inspect(*args, **kwargs)

    monkeypatch.setattr(git_reads, 'repository_state', counted)
    with store.read_session():
        for _ in range(2):
            assert 'tests/test_a.py' in getattr(gate, helper)(root, record, base)
    assert len(states) == 2, len(states)


def test_cached_selection_observes_mutated_definition_and_return_value(local_repo):
    from workorder import gate, store
    from test_workorder import PYTHON, task
    from test_v05 import commit_all
    root = local_repo
    (root / 'tests').mkdir()
    for name in ('a', 'b'):
        (root / f'tests/test_{name}.py').write_text(f'def test_{name}():\n    assert True\n')
    task(root, test=f'{PYTHON} -m pytest tests/test_a.py')
    base = commit_all(root)
    record = store.record(root, 'TASK-1')
    with store.read_session():
        selected = gate.test_files(root, record, base)
        assert selected == {'tests/test_a.py'}
        selected.clear()
        assert gate.test_files(root, record, base) == {'tests/test_a.py'}
        record['test_paths'] = ['tests/test_b.py']
        assert gate.test_files(root, record, base) == {'tests/test_a.py', 'tests/test_b.py'}
        record['test_paths'].clear()
        assert gate.test_files(root, record, base) == {'tests/test_a.py'}


def test_unchanged_configuration_does_not_reread_dependencies(local_repo, monkeypatch):
    import os
    from pathlib import Path
    from workorder import store
    from test_workorder import git
    if os.name == 'nt':
        pytest.skip('Windows change times cannot identify unchanged file bytes')
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    external = local_repo.parent / 'config-include'
    external.write_text('[workorder]\nlabel = first\n')
    git(local_repo, 'config', 'include.path', str(external))
    reads = []
    read_bytes = Path.read_bytes

    def counted(path):
        if path == external:
            reads.append(path)
        return read_bytes(path)

    monkeypatch.setattr(Path, 'read_bytes', counted)
    with store.read_session():
        assert store.git(local_repo, 'config', '--get', 'workorder.label').stdout == 'first\n'
        first = len(reads)
        for _ in range(4):
            assert store.git(local_repo, 'config', '--get', 'workorder.label').stdout == 'first\n'
        assert first and len(reads) == first
        stamp = external.stat()
        external.write_text('[workorder]\nlabel = other\n')
        os.utime(external, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert store.git(local_repo, 'config', '--get', 'workorder.label').stdout == 'other\n'


@pytest.mark.parametrize('directory', ['.', '.git/objects'])
def test_unchanged_directories_are_not_relisted(local_repo, monkeypatch, directory):
    import os
    from workorder import store
    if os.name == 'nt':
        pytest.skip('Windows directory change times need a fresh listing')
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    target = str(local_repo / directory).removesuffix('/.')
    scans = []
    scandir = os.scandir

    def counted(path):
        if os.fspath(path) == target:
            scans.append(path)
        return scandir(path)

    monkeypatch.setattr(os, 'scandir', counted)
    with store.read_session():
        for _ in range(5):
            assert store.git(local_repo, 'diff', '--name-only', 'HEAD').stdout == ''
        assert len(scans) == 1, len(scans)
        stamp = local_repo.stat()
        (local_repo / 'new.py').write_text('new = True\n')
        os.utime(local_repo, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert store.git(local_repo, 'ls-files', '--others', '--exclude-standard').stdout == 'new.py\n'


@pytest.mark.parametrize('name', ['MERGE_HEAD', 'ORIG_HEAD', 'FETCH_HEAD', 'AUTO_MERGE',
                                'REBASE_HEAD', 'CHERRY_PICK_HEAD', 'REVERT_HEAD',
                                'BISECT_HEAD', 'MERGE_AUTOSTASH'])
def test_read_cache_observes_pseudoref_changes(local_repo, name):
    from workorder import store
    from test_workorder import git
    from test_v05 import commit_all
    root = local_repo
    before = git(root, 'rev-parse', 'HEAD')
    (root / 'app.py').write_text('items = [1]\n')
    after = commit_all(root)
    path = root / '.git' / name
    path.write_text(before + '\n')
    with store.read_session():
        assert store.git(root, 'rev-parse', '--verify', name).stdout.strip() == before
        path.write_text(after + '\n')
        assert store.git(root, 'rev-parse', '--verify', name).stdout.strip() == after


def test_unchanged_observed_files_do_not_rehash_repository(tmp_path, monkeypatch):
    import os
    from workorder import git_reads, store
    from test_workorder import git
    if os.name == 'nt':
        pytest.skip('Windows requires contents rather than POSIX change times')
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    # A commit can leave Git maintenance touching objects/ just after setup.
    # This budget needs stable inputs and its config query needs no commit.
    local_repo = tmp_path / 'local'
    local_repo.mkdir()
    git(local_repo, 'init', '-q', '-b', 'main')
    git(local_repo, 'config', 'user.name', 'solo')
    git(local_repo, 'config', 'user.email', 'solo@example.com')
    (local_repo / 'app.py').write_text('items = []\n')
    hashes = []
    sha256 = git_reads.hashlib.sha256

    def counted(*args, **kwargs):
        hashes.append(None)
        return sha256(*args, **kwargs)

    monkeypatch.setattr(git_reads.hashlib, 'sha256', counted)
    with store.read_session():
        before = store.git(local_repo, 'config', '--get', 'user.name').stdout
        first = len(hashes)
        for _ in range(4):
            assert store.git(local_repo, 'config', '--get', 'user.name').stdout == before
        assert first and len(hashes) == first


def test_external_command_keeps_queries_when_observed_state_is_unchanged(local_repo, monkeypatch):
    import subprocess
    from workorder import store
    from test_workorder import PYTHON
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    calls = []
    popen = subprocess.Popen
    query = ['git', 'rev-parse', 'HEAD']

    def counted(args, *a, **kw):
        if args == query:
            calls.append(args)
        return popen(args, *a, **kw)

    monkeypatch.setattr(subprocess, 'Popen', counted)
    with store.read_session():
        before = store.git(local_repo, 'rev-parse', 'HEAD').stdout
        assert store.run_test([PYTHON, '-c', 'pass'], local_repo).returncode == 0
        assert store.git(local_repo, 'rev-parse', 'HEAD').stdout == before
    assert len(calls) == 1


def test_external_command_rechecks_state_inside_compound_read(local_repo):
    from workorder import store
    from test_workorder import PYTHON

    @store.cached_read
    def changed_index(root):
        assert store.blob_bytes(root, ':app.py') == b'items = []\n'
        command = ('from pathlib import Path; import subprocess; '
                   'Path("app.py").write_text("items = [2]\\n"); '
                   'subprocess.run(["git", "add", "app.py"], check=True)')
        assert store.run_test([PYTHON, '-c', command], root).returncode == 0
        return store.blob_bytes(root, ':app.py')

    with store.read_session():
        assert changed_index(local_repo) == b'items = [2]\n'


def test_read_cache_observes_reflog_only_changes(local_repo):
    from workorder import store
    root = local_repo
    path = root / '.git/logs/refs/heads/main'
    query = ('log', '-g', '-1', '--format=%gs', 'main')
    with store.read_session():
        assert store.git(root, *query).stdout.strip() != 'new reflog message'
        line = path.read_text().splitlines()[-1].split('\t', 1)[0]
        with path.open('a') as stream:
            stream.write(line + '\tnew reflog message\n')
        assert store.git(root, *query).stdout.strip() == 'new reflog message'


def test_cached_task_observes_link_into_private_git_cache(local_repo):
    from workorder import store
    from test_workorder import task
    path = task(local_repo)
    target = local_repo / '.git/workorder-tests/task-source'
    target.parent.mkdir()
    target.write_text(path.read_text())
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError:
        pytest.skip('symlinks unavailable')
    with store.read_session():
        assert store.record(local_repo, 'TASK-1')['status'] == 'IN_PROGRESS'
        target.write_text(target.read_text().replace('IN_PROGRESS', 'DONE'))
        assert store.record(local_repo, 'TASK-1')['status'] == 'DONE'


@pytest.mark.parametrize('mode', ['explicit', 'unobservable'])
def test_fresh_blob_group_uses_one_new_batch_per_call(local_repo, monkeypatch, mode):
    import subprocess
    from workorder import git_reads, store
    from test_workorder import git
    root = local_repo
    (root / 'other.txt').write_text('other\n')
    git(root, 'add', 'other.txt')
    git(root, 'commit', '-qm', 'second blob')
    first, second = (git(root, 'rev-parse', 'HEAD:' + path) for path in ('app.py', 'other.txt'))
    replacement = git(root, 'hash-object', '-w', 'other.txt')
    if mode == 'explicit':
        monkeypatch.setenv('WORKORDER_GIT_CACHE', '0')
    else:
        monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
        monkeypatch.setattr(git_reads, 'repository_state', lambda *a, **kw: None)
    batches = []
    popen = subprocess.Popen

    def counted(args, *a, **kw):
        if args == ['git', 'cat-file', '--batch']:
            batches.append(args)
        return popen(args, *a, **kw)

    monkeypatch.setattr(subprocess, 'Popen', counted)
    with store.read_session():
        assert store.read_blobs(root, [first, second]) == {first: 'items = []\n', second: 'other\n'}
        git(root, 'replace', first, replacement)
        assert store.read_blobs(root, [first, second]) == {first: 'other\n', second: 'other\n'}
    assert len(batches) == 2


@pytest.mark.parametrize('mode', ['explicit', 'unobservable', 'no-session'])
@pytest.mark.parametrize('reader', ['record', 'fields_at', 'texts', 'paths', 'staged_paths'])
def test_fresh_metadata_reads_only_the_requested_task(local_repo, monkeypatch, mode, reader):
    from contextlib import nullcontext
    from workorder import gate, git_reads, store
    from test_workorder import git, task
    root = local_repo
    for tid in ('TASK-1', 'TASK-2'):
        task(root, tid)
        path = root / f'.workorder/receipts/{tid}/event.json'
        path.parent.mkdir(parents=True)
        path.write_text('{}\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'two tasks')
    if mode == 'explicit':
        monkeypatch.setenv('WORKORDER_GIT_CACHE', '0')
    else:
        monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
        if mode == 'unobservable':
            monkeypatch.setattr(git_reads, 'repository_state', lambda *a, **kw: None)
    reads = []
    original = git_reads.Batch.read

    def counted(batch, oid):
        reads.append(oid)
        return original(batch, oid)

    monkeypatch.setattr(git_reads.Batch, 'read', counted)
    with store.read_session() if mode != 'no-session' else nullcontext():
        if reader == 'record':
            assert store.record(root, 'TASK-1', staged=True)['id'] == 'TASK-1'
        elif reader == 'fields_at':
            assert gate.fields_at(root, 'TASK-1', 'HEAD')['id'] == 'TASK-1'
        elif reader == 'texts':
            assert store.receipt_texts(root, 'TASK-1', rev='HEAD') == {
                '.workorder/receipts/TASK-1/event.json': '{}\n'}
        else:
            kwargs = {'staged': True} if reader == 'staged_paths' else {'rev': 'HEAD'}
            assert store.receipt_paths(root, tid='TASK-1', **kwargs) == [
                '.workorder/receipts/TASK-1/event.json']
    assert len(reads) <= 2, reads


@pytest.mark.parametrize('fresh', [False, True])
def test_staged_task_does_not_follow_worktree_link(local_repo, tmp_path, monkeypatch, fresh):
    from workorder import store
    from test_workorder import git, task
    path = task(local_repo)
    git(local_repo, 'add', '-A')
    outside = tmp_path / 'outside-task'
    outside.write_text('not a task\n')
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError:
        pytest.skip('symlinks unavailable')
    if fresh:
        monkeypatch.setenv('WORKORDER_GIT_CACHE', '0')
    else:
        monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    with store.read_session():
        assert store.record(local_repo, 'TASK-1', staged=True)['status'] == 'IN_PROGRESS'


@pytest.mark.parametrize('rev', [None, ''])
@pytest.mark.parametrize('fresh', [False, True])
def test_fields_at_empty_revision_means_head(local_repo, monkeypatch, rev, fresh):
    from workorder import gate, store
    from test_workorder import git, task
    task(local_repo)
    git(local_repo, 'add', '-A')
    git(local_repo, 'commit', '-qm', 'task')
    if fresh:
        monkeypatch.setenv('WORKORDER_GIT_CACHE', '0')
    else:
        monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    with store.read_session():
        assert gate.fields_at(local_repo, 'TASK-1', rev)['id'] == 'TASK-1'


@pytest.mark.parametrize('reader', ['blob_bytes', 'read_blobs'])
def test_one_off_blob_read_starts_only_its_git_batch(local_repo, monkeypatch, reader):
    import subprocess
    from workorder import store
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    calls = []
    original = subprocess.Popen

    def counted(args, *a, **kw):
        if args[0] == 'git':
            calls.append(args)
        return original(args, *a, **kw)

    monkeypatch.setattr(subprocess, 'Popen', counted)
    assert store._READS.get() is None
    if reader == 'blob_bytes':
        assert store.blob_bytes(local_repo, 'HEAD:app.py') == b'items = []\n'
    else:
        assert store.read_blobs(local_repo, ['HEAD:app.py']) == {'HEAD:app.py': 'items = []\n'}
    assert calls == [['git', 'cat-file', '--batch']]
