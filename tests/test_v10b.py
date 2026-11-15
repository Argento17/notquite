"""Round-12 second pass: hook index isolation, merge-base review, and done."""

import os
import json
import subprocess
import sys
from pathlib import Path

import pytest

from workorder import bootstrap, hooks, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, task
from test_v05 import commit_all
from test_v07b_setup import local_claim, refuse_network


def filled(root, command, tid='TASK-1'):
    path = task(root, tid, ships='none', test=command)
    path.write_text(path.read_text(encoding='utf-8')
                    + '\n## Scope\n\nDo the work.\n\n## Acceptance\n\n- The command passes.\n',
                    encoding='utf-8')
    return path


def real_git(root, *args, env=None):
    """Git with the installed hooks. Setup uses the helper that disables them."""
    merged = dict(os.environ if env is None else env)
    merged['PATH'] = str(Path(PYTHON).parent) + os.pathsep + merged.get('PATH', '')
    merged['PYTHONPATH'] = str(PACKAGE)
    return subprocess.run(['git', *args], cwd=root, env=merged, capture_output=True, text=True, timeout=60)


def with_origin(root):
    remote = root.parent / 'origin.git'
    git(root, 'init', '--bare', '-q', '--initial-branch=main', str(remote))
    git(root, 'remote', 'add', 'origin', str(remote))
    git(root, 'push', '-q', 'origin', 'HEAD:refs/heads/main')
    git(root, 'fetch', '-q', 'origin')
    return remote


def names_in_head(root):
    shown = real_git(root, 'show', '--name-only', '--pretty=format:', 'HEAD')
    assert shown.returncode == 0, shown.stderr
    return [line for line in shown.stdout.splitlines() if line.strip()]


@pytest.mark.skipif(os.name == 'nt', reason='executes a POSIX pre-commit hook')
@pytest.mark.parametrize('mode', ['all', 'paths', 'amend', 'worktree'])
def test_installed_hook_commits_the_staged_files(local_repo, mode):
    root = local_repo
    filled(root, f'{PYTHON} -c pass')
    (root / 'app.py').write_text('items = []\n', encoding='utf-8')
    commit_all(root, 'Task and code')
    with_origin(root)
    bootstrap.initialize(root)
    marker = f'marker-{mode}'
    (root / 'app.py').write_text(f'items = [{marker!r}]\n', encoding='utf-8')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text(encoding='utf-8') + f'\n<!-- {marker} -->\n', encoding='utf-8')
    if mode == 'worktree':
        linked = root.parent / 'linked'
        made = real_git(root, 'worktree', 'add', '-q', '-b', 'lane', str(linked), 'HEAD')
        assert made.returncode == 0, made.stderr
        (linked / 'app.py').write_text(f'items = [{marker!r}]\n', encoding='utf-8')
        linked_task = linked / 'tasks/TASK-1.md'
        linked_task.write_text(path.read_text(encoding='utf-8'), encoding='utf-8')
        result = real_git(linked, 'commit', '-aqm', 'Update from the linked worktree')
        show_root = linked
    elif mode == 'all':
        result = real_git(root, 'commit', '-aqm', 'Update everything')
        show_root = root
    elif mode == 'paths':
        result = real_git(root, 'commit', '-qm', 'Update named paths', 'app.py', 'tasks/TASK-1.md')
        show_root = root
    else:
        git(root, 'add', 'app.py', 'tasks/TASK-1.md')
        git(root, 'commit', '-qm', 'First amendment candidate')
        path.write_text(path.read_text(encoding='utf-8') + f'\n<!-- amended-{marker} -->\n', encoding='utf-8')
        result = real_git(root, 'commit', '-a', '--amend', '--no-edit')
        show_root = root
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert 'checked' in output
    committed = names_in_head(show_root)
    assert 'app.py' in committed, output
    assert 'tasks/TASK-1.md' in committed, output
    staged = real_git(show_root, 'diff', '--cached', '--name-only')
    assert staged.stdout.strip() == '', staged.stdout
    text = real_git(show_root, 'show', 'HEAD:tasks/TASK-1.md').stdout
    assert marker in text
    if mode == 'amend':
        assert f'amended-{marker}' in text


def test_test_and_requirement_changes_use_the_merge_base(local_repo):
    root = local_repo
    (root / 'tests').mkdir()
    (root / 'tests/test_keep.py').write_text('def test_keep():\n    assert True\n', encoding='utf-8')
    path = task(root, 'TASK-2', ships='none', test=f'{PYTHON} -m pytest -q tests/test_keep.py')
    path.write_text(path.read_text(encoding='utf-8')
                    + '\n## Scope\n\nKeep.\n\n## Acceptance\n\n- keep passes.\n', encoding='utf-8')
    commit_all(root, 'Fork point')
    git(root, 'checkout', '-q', '-b', 'feature')
    git(root, 'checkout', '-q', 'main')
    (root / 'tests/test_t1.py').write_text(
        'def test_t1():\n    assert True\n\n'
        'def test_t1_extra():\n    assert True\n\n'
        'def test_t1_more():\n    assert True\n', encoding='utf-8')
    path.write_text(path.read_text(encoding='utf-8').replace(
        '- keep passes.\n', '- keep passes.\n- Added only on main.\n'), encoding='utf-8')
    commit_all(root, 'Main gains a test file and an acceptance line')
    main = git(root, 'rev-parse', 'HEAD')
    git(root, 'update-ref', 'refs/remotes/origin/main', main)
    git(root, 'checkout', '-q', 'feature')
    (root / 'app.py').write_text('items = [1]\n', encoding='utf-8')
    short = git(root, 'rev-parse', '--short', git(root, 'merge-base', 'HEAD', 'origin/main'))
    clean = cli(root, 'check', '--review', 'refuse', '--base', 'origin/main')
    output = clean.stdout + clean.stderr
    assert clean.returncode == 0, output
    assert 'lost or changed' not in output
    assert 'test_t1.py' not in output
    assert 'Acceptance lines removed' not in output
    probe = root / 'tests/test_keep.py'
    probe.write_text('def test_keep():\n    assert  True\n', encoding='utf-8')
    changed = cli(root, 'check', '--review', 'refuse', '--base', 'origin/main')
    changed_output = changed.stdout + changed.stderr
    assert changed.returncode == 1, changed_output
    assert f'since origin/main (merge base {short})' in changed_output
    assert 'tests/test_keep.py' in changed_output
    assert 'tests/test_t1.py' not in changed_output


def test_needs_review_records_a_deliberate_test_change(local_repo, monkeypatch, capsys):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n', encoding='utf-8')
    (root / 'tests').mkdir()
    probe = root / 'tests/test_keep.py'
    probe.write_text('def test_keep():\n    assert True\n', encoding='utf-8')
    filled(root, f'{PYTHON} -m pytest -q tests/test_keep.py', tid='TASK-6')
    bootstrap.initialize(root)
    commit_all(root, 'Task')
    with_origin(root)
    git(root, 'checkout', '-q', '-b', 'feature')
    probe.write_text('def test_keep():\n    assert  True\n', encoding='utf-8')
    commit_all(root, 'Edit the test on purpose')
    refused = cli(root, 'done', '--task', 'TASK-6')
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert 'lost or changed' in refused.stdout
    assert store.record(root, 'TASK-6')['status'] == 'IN_PROGRESS'
    accepted = cli(root, 'done', '--task', 'TASK-6', '--needs-review')
    output = accepted.stdout + accepted.stderr
    assert accepted.returncode == 0, output
    assert store.record(root, 'TASK-6')['status'] == 'IN_PROGRESS'
    assert 'pending review: the pull request needs workorder-allow:TASK-6' in output
    store.set_task_field(root, 'TASK-6', 'status', 'DONE')  # Claim in the reviewed PR.
    git(root, 'add', 'tasks', '.workorder')
    hook = real_git(root, 'commit', '-qm', 'Record the review')
    hook_output = hook.stdout + hook.stderr
    assert hook.returncode == 0, hook_output
    assert 'lost or changed' in hook_output
    assert 'NOT DONE' not in hook_output
    local_claim(root, 'TASK-6')
    refuse_network(monkeypatch)
    monkeypatch.setattr(sys, 'stdin', __import__('io').StringIO('{}'))
    assert hooks.run(root, 'stop') == 0
    stopped = capsys.readouterr()
    assert 'lost or changed' in stopped.out
    assert 'NOT DONE' not in stopped.err
    blocked = cli(root, 'check', '--review', 'refuse', '--base', 'origin/main')
    assert blocked.returncode == 1
    assert 'lost or changed' in blocked.stdout
    reviewed = cli(root, 'check', '--review', 'refuse', '--base', 'origin/main', '--run-tests', '--all-tests',
                   '--require-task', '--allow', 'TASK-6')
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr


def test_done_refuses_until_code_changes(local_repo):
    root = local_repo
    filled(root, f'{PYTHON} -c pass')
    (root / 'app.py').write_text('items = []\n', encoding='utf-8')
    commit_all(root, 'Create the task and the code together')
    created = git(root, 'rev-parse', '--short', 'HEAD')
    refused = cli(root, 'done', '--task', 'TASK-1')
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert f'TASK-1: no code changed since {created}; nothing was implemented' in refused.stdout
    assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'
    accepted = cli(root, 'done', '--task', 'TASK-1', '--no-code-change')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert store.record(root, 'TASK-1')['status'] == 'DONE'
    store.set_task_field(root, 'TASK-1', 'status', 'IN_PROGRESS')
    commit_all(root, 'Reset the claim')
    with_origin(root)
    git(root, 'checkout', '-q', '-b', 'feature')
    same = cli(root, 'done', '--task', 'TASK-1')
    assert same.returncode == 1
    assert 'TASK-1: no code changed since origin/main; nothing was implemented' in same.stdout
    (root / 'app.py').write_text('items = [1]\n', encoding='utf-8')
    commit_all(root, 'Implement')
    finished = cli(root, 'done', '--task', 'TASK-1')
    assert finished.returncode == 1, finished.stdout + finished.stderr
    assert "its test passes on origin/main's code too" in finished.stdout


def test_require_task_needs_a_task_from_the_merge_base_or_allow(local_repo):
    root = local_repo
    (root / 'app.py').write_text('value = 1\n', encoding='utf-8')
    base = commit_all(root, 'Before the task')
    git(root, 'checkout', '-q', '-b', 'feature')
    filled(root, f'{PYTHON} -c pass', tid='TASK-9')
    (root / 'app.py').write_text('value = 2\n', encoding='utf-8')
    commit_all(root, 'New task and code')
    store.receipt(root, 'TASK-9', 'implemented')
    refused = cli(root, 'check', '--base', base, '--require-task')
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert 'TASK-9 is new in this pull request:' in refused.stdout
    assert 'no task records it' not in refused.stdout
    allowed = cli(root, 'check', '--base', base, '--require-task', '--allow', 'TASK-9')
    assert allowed.returncode == 0, allowed.stdout + allowed.stderr


@pytest.mark.parametrize('weaken', ['helper', 'fixture'])
def test_shared_helpers_and_fixture_data_are_test_files(local_repo, weaken):
    root = local_repo
    (root / 'calc.py').write_text('def add(a, b):\n    return a + b\n', encoding='utf-8')
    (root / 'testsupport').mkdir()
    (root / 'testsupport/helpers.py').write_text('def same(a, b):\n    return a == b\n', encoding='utf-8')
    (root / 'fixtures').mkdir()
    (root / 'fixtures/expected.json').write_text('{"sum": 4}\n', encoding='utf-8')
    (root / 'testdata').mkdir()
    (root / 'testdata/rows.csv').write_text('n\n1\n', encoding='utf-8')
    (root / 'test_data').mkdir()
    (root / 'test_data/note.txt').write_text('old\n', encoding='utf-8')
    (root / 'tests').mkdir()
    (root / 'tests/test_calc.py').write_text(
        'import json\nfrom pathlib import Path\nfrom calc import add\n'
        'from testsupport.helpers import same\n\n'
        'def test_add():\n'
        '    expected = json.loads(Path("fixtures/expected.json").read_text())["sum"]\n'
        '    assert same(add(2, 2), expected)\n', encoding='utf-8')
    (root / 'pytest.ini').write_text('[pytest]\npythonpath = .\n', encoding='utf-8')
    filled(root, f'{PYTHON} -m pytest -q tests/test_calc.py')
    base = commit_all(root, 'Working addition')
    (root / 'calc.py').write_text('def add(a, b):\n    return a + b + 1\n', encoding='utf-8')
    if weaken == 'helper':
        (root / 'testsupport/helpers.py').write_text('def same(a, b):\n    return True\n', encoding='utf-8')
    else:
        (root / 'fixtures/expected.json').write_text('{"sum": 5}\n', encoding='utf-8')
    (root / 'testdata/rows.csv').write_text('n\n2\n', encoding='utf-8')
    (root / 'test_data/note.txt').write_text('new\n', encoding='utf-8')
    commit_all(root, 'Break code and weaken the test')
    store.receipt(root, 'TASK-1', 'implemented')
    result = cli(root, 'check', '--review', 'refuse', '--base', base, '--run-tests', '--all-tests', '--require-task')
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    weakened = 'testsupport/helpers.py' if weaken == 'helper' else 'fixtures/expected.json'
    assert 'no task records it' not in output
    for path in (weakened, 'testdata/rows.csv', 'test_data/note.txt'):
        assert path in output, output


@pytest.mark.skipif(os.name == 'nt', reason='executes a POSIX pre-commit hook')
def test_force_keeps_and_calls_the_previous_hook(local_repo):
    root = local_repo
    hook = root / '.git/hooks/pre-commit'
    hook.parent.mkdir(parents=True, exist_ok=True)
    hook.write_text('#!/bin/sh\necho PREVIOUS-HOOK\nexit 0\n', encoding='utf-8')
    hook.chmod(0o755)
    result = cli(root, 'init', '--force')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert 'pre-commit.workorder-previous' in output
    assert 'calls it first' in output
    previous = root / '.git/hooks/pre-commit.workorder-previous'
    assert previous.read_text(encoding='utf-8') == '#!/bin/sh\necho PREVIOUS-HOOK\nexit 0\n'
    ran = real_git(root, 'commit', '--allow-empty', '-qm', 'Empty')
    assert ran.returncode == 0, ran.stdout + ran.stderr
    assert 'PREVIOUS-HOOK' in ran.stdout + ran.stderr
    assert 'checked' in (ran.stdout + ran.stderr).lower() or ran.returncode == 0


@pytest.mark.skipif(os.name == 'nt', reason='executes a POSIX pre-commit hook')
def test_hook_shows_test_output_only_when_the_test_fails(local_repo):
    root = local_repo
    filled(root, f'{PYTHON} -c "print(\'UNIQUE_PASS_OUTPUT\')"')
    commit_all(root, 'Passing command')
    with_origin(root)
    bootstrap.initialize(root)
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text(encoding='utf-8') + '\n<!-- pass -->\n', encoding='utf-8')
    passing = real_git(root, 'commit', '-aqm', 'Note a pass')
    passed = passing.stdout + passing.stderr
    assert passing.returncode == 0, passed
    assert 'UNIQUE_PASS_OUTPUT' not in passed
    assert 'ran 1 declared test' in passed
    path.write_text(path.read_text(encoding='utf-8') + '\n<!-- pass again -->\n', encoding='utf-8')
    cached = real_git(root, 'commit', '-aqm', 'Note a cached pass')
    cached_output = cached.stdout + cached.stderr
    assert cached.returncode == 0, cached_output
    assert '(cached)' in cached_output
    assert 'UNIQUE_PASS_OUTPUT' not in cached_output
    path.write_text(path.read_text(encoding='utf-8').replace(
        json.dumps(f'{PYTHON} -c "print(\'UNIQUE_PASS_OUTPUT\')"'),
        json.dumps(f'{PYTHON} -c "print(\'UNIQUE_FAIL_OUTPUT\'); raise SystemExit(1)"')), encoding='utf-8')
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    failing = real_git(root, 'commit', '-aqm', 'Note a failure')
    failed = failing.stdout + failing.stderr
    assert failing.returncode == 1, failed
    assert 'UNIQUE_FAIL_OUTPUT' in failed


def test_missing_dependencies_warn_and_the_check_names_pytest(local_repo, tmp_path):
    from test_v07b_setup import fake_commands, workflow_commands
    path = filled(local_repo, f'{PYTHON} runner.py')
    (local_repo / 'runner.py').write_text('raise SystemExit(0)\n', encoding='utf-8')
    bootstrap.initialize(local_repo)
    workflow = (local_repo / '.github/workflows/workorder.yml').read_text(encoding='utf-8')
    assert 'exit 1' not in workflow
    assert 'warning' in workflow.lower()
    assert 'pip install pytest' not in workflow
    environment, _log = fake_commands(tmp_path)
    for command in workflow_commands(local_repo) if os.name != 'nt' else []:
        completed = subprocess.run(['/bin/sh', '-eu', '-c', command], cwd=local_repo,
                                   env=environment, capture_output=True, text=True)
        assert completed.returncode == 0, completed.stderr
    base = commit_all(local_repo, 'Configured project')
    path.write_text(path.read_text(encoding='utf-8').replace(
        json.dumps(f'{PYTHON} runner.py'), json.dumps(f'{PYTHON} -S -m pytest -q')), encoding='utf-8')
    assert store.record(local_repo, 'TASK-1')['test'] == f'{PYTHON} -S -m pytest -q'
    checked = cli(local_repo, 'check', '--base', base, '--run-tests')
    output = checked.stdout + checked.stderr
    assert 'pytest is not installed' in output


@pytest.mark.parametrize('snapshot_kind', ['staged', 'ref'])
@pytest.mark.parametrize('timeout', [None, 10])
def test_copy_git_and_tests_do_not_inherit_repository_environment(local_repo, tmp_path, monkeypatch,
                                                                 snapshot_kind, timeout):
    from workorder import gate
    root = local_repo
    (root / 'app.py').write_text('items = [1]\n', encoding='utf-8')
    git(root, 'add', 'app.py')
    index = tmp_path / 'commit-index'
    index.write_bytes((root / '.git/index').read_bytes())
    environment = {
        'GIT_INDEX_FILE': str(index), 'GIT_DIR': str(root / '.git'),
        'GIT_WORK_TREE': str(root), 'GIT_PREFIX': 'subdir/',
        'GIT_COMMON_DIR': str(root / '.git'),
        'GIT_OBJECT_DIRECTORY': str(root / '.git/objects'),
        'GIT_ALTERNATE_OBJECT_DIRECTORIES': str(root / '.git/objects'),
    }
    for key, value in environment.items():
        monkeypatch.setenv(key, value)
    before = index.read_bytes()
    destination = tmp_path / 'copy'
    if snapshot_kind == 'staged':
        gate._test_snapshot(root, destination, staged=True)
        assert (destination / 'app.py').read_text() == 'items = [1]\n'
    else:
        gate._ref_test_snapshot(root, destination, store.head(root))
        assert (destination / 'app.py').read_text() == 'items = []\n'
    command = [PYTHON, '-c',
               'import os, pathlib, subprocess; '
               f'assert not (set({list(environment)!r}) & set(os.environ)); '
               'actual = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], text=True).strip(); '
               'assert pathlib.Path(actual).resolve() == pathlib.Path.cwd().resolve(); '
               'subprocess.run(["git", "read-tree", "HEAD"], check=True)']
    for _ in range(2):
        result = store.cached_test(command, destination, timeout=timeout, cache_root=root)
        assert result.returncode == 0, result.stdout + result.stderr
    assert index.read_bytes() == before
    # Reads on the user's repository must still see its alternate commit index.
    assert store.read_bytes(root, 'app.py', staged=True) == b'items = [1]\n'


@pytest.mark.parametrize('action', ['done', 'pre-commit', 'stop'])
def test_local_checks_ignore_tests_and_requirements_added_only_on_target(local_repo, monkeypatch, capsys, action):
    if action == 'pre-commit' and os.name == 'nt':
        pytest.skip('executes a POSIX pre-commit hook')
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n', encoding='utf-8')
    (root / 'tests').mkdir()
    (root / 'tests/test_keep.py').write_text('def test_keep():\n    assert True\n', encoding='utf-8')
    path = filled(root, f'{PYTHON} -m pytest -q tests')
    bootstrap.initialize(root)
    base = commit_all(root, 'Fork point')
    with_origin(root)
    (root / 'tests/test_main_only.py').write_text('def test_main():\n    assert True\n', encoding='utf-8')
    path.write_text(path.read_text() + '\n- Main-only acceptance.\n', encoding='utf-8')
    main = commit_all(root, 'Main advances')
    git(root, 'update-ref', 'refs/remotes/origin/main', main)
    git(root, 'checkout', '-qb', 'feature', base)
    (root / 'app.py').write_text('items = [1]\n', encoding='utf-8')
    commit_all(root, 'Implement')
    if action == 'done':
        result = cli(root, 'done', '--no-code-change', '--task', 'TASK-1')
        code, output = result.returncode, result.stdout + result.stderr
    else:
        store.receipt(root, 'TASK-1', 'implemented')
        store.receipt(root, 'TASK-1', 'test', [PYTHON, '-m', 'pytest', '-q', 'tests'])
        store.set_task_field(root, 'TASK-1', 'status', 'DONE')
        if action == 'pre-commit':
            git(root, 'add', 'tasks', '.workorder')
            result = real_git(root, 'commit', '-qm', 'Record completion')
            code, output = result.returncode, result.stdout + result.stderr
        else:
            local_claim(root, 'TASK-1')
            refuse_network(monkeypatch)
            monkeypatch.setattr(sys, 'stdin', __import__('io').StringIO('{}'))
            code = hooks.run(root, 'stop')
            captured = capsys.readouterr()
            output = captured.out + captured.err
    assert code == 0, output
    assert 'lost or changed' not in output
    assert 'Acceptance lines removed' not in output
    assert 'fewer tests pass' not in output
    assert store.record(root, 'TASK-1')['status'] == 'DONE'


@pytest.mark.parametrize('failure', ['test', 'dependency'])
def test_done_flags_cannot_accept_unsupported_completion(local_repo, failure):
    root = local_repo
    command = f'{PYTHON} -c "raise SystemExit(1)"' if failure == 'test' else f'{PYTHON} -c pass'
    path = filled(root, command)
    if failure == 'dependency':
        path.write_text(path.read_text().replace('waits_on: []', 'waits_on: ["person:alex"]'))
    commit_all(root, 'Verification task')
    result = cli(root, 'done', '--task', 'TASK-1', '--no-code-change', '--needs-review')
    assert result.returncode == 1, result.stdout + result.stderr
    assert ('a passing test' if failure == 'test' else 'person:alex') in result.stdout
    assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'


@pytest.mark.skipif(os.name == 'nt', reason='executes a POSIX pre-commit hook')
def test_previous_hook_failure_stops_commit_and_reinstall_keeps_backup(local_repo):
    root = local_repo
    hook = root / '.git/hooks/pre-commit'
    hook.write_text('#!/bin/sh\necho PREVIOUS-REFUSAL >&2\nexit 7\n', encoding='utf-8')
    hook.chmod(0o755)
    first = cli(root, 'init', '--force')
    assert first.returncode == 0, first.stderr
    previous = hook.with_name('pre-commit.workorder-previous')
    assert previous.exists()
    original = previous.read_bytes()
    assert cli(root, 'init', '--force').returncode == 0
    assert previous.read_bytes() == original
    before = git(root, 'rev-parse', 'HEAD')
    result = real_git(root, 'commit', '--allow-empty', '-qm', 'Must refuse')
    assert result.returncode != 0
    assert 'PREVIOUS-REFUSAL' in result.stdout + result.stderr
    assert 'checked' not in result.stdout + result.stderr
    assert git(root, 'rev-parse', 'HEAD') == before
    # A second different hook must not overwrite the already preserved original.
    hook.write_text('#!/bin/sh\necho SECOND\n', encoding='utf-8')
    refused = cli(root, 'init', '--force')
    assert refused.returncode == 1
    assert previous.read_bytes() == original
    assert 'SECOND' in hook.read_text()


def test_missing_executable_diagnostic_does_not_depend_on_os_error_text(local_repo, monkeypatch):
    from workorder import gate
    filled(local_repo, 'workorder-missing-test-tool')

    def missing(*args, **kwargs):
        raise FileNotFoundError(2, 'The system cannot find the file specified')

    monkeypatch.setattr(store, 'cached_test', missing)
    problem = gate.run_declared_test(local_repo, store.record(local_repo, 'TASK-1'))
    assert 'workorder-missing-test-tool is not installed in this environment' in problem
