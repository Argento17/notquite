"""Clone-local test caching and repository import diagnostics, using real Git trees."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from workorder import bootstrap, gate, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, task
from test_v05 import commit_all
from test_v06b import fill_sections


@pytest.fixture(autouse=True)
def local_cache_environment(monkeypatch):
    monkeypatch.delenv('CI', raising=False)
    monkeypatch.delenv('GITHUB_ACTIONS', raising=False)


def counted_command(root):
    counter = root.parent / 'executions.txt'
    (root / 'probe.py').write_text(
        'from pathlib import Path\nimport sys\n'
        'counter = Path(sys.argv[1])\n'
        "counter.write_text(counter.read_text() + 'run\\n' if counter.exists() else 'run\\n')\n"
        "print('test output')\nprint('test warning', file=sys.stderr)\n"
        "raise SystemExit(1 if 'fail' in sys.argv else 0)\n")
    command = [PYTHON, str(root / 'probe.py'), str(counter)]
    task(root, ships='none', test=store.join_command(command))
    commit_all(root)
    return command, counter


def cached(command, root, timeout=None, **kwargs):
    # The pre-cache implementation is exercised too, so the red test shows
    # duplicate execution rather than merely failing on a missing API.
    if hasattr(store, 'cached_test'):
        return store.cached_test(command, root, timeout, **kwargs)
    return store.run_test(command, root, timeout)


def executions(counter):
    return counter.read_text().splitlines() if counter.exists() else []


def test_cache_persists_between_processes_without_tracking_files(local_repo):
    command, counter = counted_command(local_repo)
    program = (
        'import json,sys\nfrom pathlib import Path\nfrom workorder import store\n'
        "run = getattr(store, 'cached_test', store.run_test)(json.loads(sys.argv[2]), Path(sys.argv[1]))\n"
        "print(run.returncode, run.stdout.strip(), run.stderr.strip())\n")
    env = dict(os.environ, PYTHONPATH=str(PACKAGE))
    for _ in range(2):
        result = subprocess.run([PYTHON, '-c', program, str(local_repo), json.dumps(command)],
                                env=env, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        assert result.stdout == '0 test output test warning\n'
    assert len(executions(counter)) == 1
    assert git(local_repo, 'status', '--porcelain') == ''
    assert list((local_repo / '.git/workorder-tests').glob('*.json'))


@pytest.mark.parametrize('change', ['tracked', 'untracked', 'staged', 'command', 'timeout'])
def test_cache_key_tracks_actual_code_command_and_timeout(local_repo, change):
    command, counter = counted_command(local_repo)
    cached(command, local_repo)
    cached(command, local_repo)
    assert len(executions(counter)) == 1
    timeout = None
    if change in {'tracked', 'staged'}:
        (local_repo / 'app.py').write_text('items = [2]\n')
        if change == 'staged':
            git(local_repo, 'add', 'app.py')
    elif change == 'untracked':
        (local_repo / 'new_module.py').write_text('value = 2\n')
    elif change == 'command':
        command = command + ['changed-argument']
    else:
        timeout = 10
    cached(command, local_repo, timeout)
    again = cached(command, local_repo, timeout)
    assert again.returncode == 0 and again.cached
    assert len(executions(counter)) == 2


def test_metadata_commits_share_cache_but_clones_do_not(local_repo, tmp_path):
    command, counter = counted_command(local_repo)
    cached(command, local_repo)
    store.add_note(local_repo, 'TASK-1', 'Metadata only')
    commit_all(local_repo)
    cached(command, local_repo)
    assert len(executions(counter)) == 1
    clone = tmp_path / 'fresh-clone'
    git(tmp_path, 'clone', '-q', str(local_repo), str(clone))
    clone_command = gate._snapshot_command(command, local_repo, clone)
    cached(clone_command, clone, logical_command=command)
    assert len(executions(counter)) == 2


def test_snapshots_share_logical_command_and_fingerprint_actual_overlay(local_repo, tmp_path):
    command, counter = counted_command(local_repo)
    cached(command, local_repo)
    first = tmp_path / 'snapshot-one'
    gate._test_snapshot(local_repo, first)
    run = cached(gate._snapshot_command(command, local_repo, first), first,
                 cache_root=local_repo, logical_command=command)
    assert len(executions(counter)) == 1
    assert run.cached
    (first / 'app.py').write_text('items = [20]\n')
    cached(gate._snapshot_command(command, local_repo, first), first,
           cache_root=local_repo, logical_command=command)
    assert len(executions(counter)) == 2
    second = tmp_path / 'snapshot-two'
    gate._test_snapshot(local_repo, second)
    (second / 'app.py').write_text('items = [20]\n')
    cached(gate._snapshot_command(command, local_repo, second), second,
           cache_root=local_repo, logical_command=command)
    assert len(executions(counter)) == 2


@pytest.mark.parametrize('environment', ['CI', 'GITHUB_ACTIONS'])
def test_ci_runs_fresh_without_reading_or_writing_local_cache(local_repo, monkeypatch, environment):
    command, counter = counted_command(local_repo)
    cached(command, local_repo)
    cached(command, local_repo)
    assert len(executions(counter)) == 1
    monkeypatch.setenv(environment, 'true')
    cached(command, local_repo)
    cached(command, local_repo)
    assert len(executions(counter)) == 3
    changed = command + ['ci-only']
    cached(changed, local_repo)
    monkeypatch.delenv(environment)
    cached(changed, local_repo)
    assert len(executions(counter)) == 5


@pytest.mark.parametrize('change', ['code', 'head'])
def test_commands_changing_code_or_head_are_never_cached(local_repo, change):
    command, counter = counted_command(local_repo)
    cached(command, local_repo)
    cached(command, local_repo)
    assert len(executions(counter)) == 1
    probe = local_repo / 'probe.py'
    source = probe.read_text().replace("raise SystemExit(1 if 'fail' in sys.argv else 0)\n", '')
    if change == 'code':
        source += "Path('app.py').write_text('items = [20]\\n')\n"
    else:
        source += ('import subprocess\n'
                   "subprocess.run(['git', '-c', 'core.hooksPath=/dev/null', 'commit', '--allow-empty', '-qm', 'test moved HEAD'], check=True)\n")
    probe.write_text(source)
    commit_all(local_repo)
    original = (local_repo / 'app.py').read_bytes()
    for _ in range(2):
        cached(command, local_repo)
        if change == 'code':
            (local_repo / 'app.py').write_bytes(original)
    assert len(executions(counter)) == 3


def test_explicit_receipt_runs_refresh_cached_failures(local_repo):
    command, counter = counted_command(local_repo)
    failing = command + ['fail']
    first = cached(failing, local_repo)
    again = cached(failing, local_repo)
    assert first.returncode == again.returncode == 1
    assert len(executions(counter)) == 1
    receipt = store.receipt(local_repo, 'TASK-1', 'test', failing)
    assert receipt['exit_code'] == 1
    assert len(executions(counter)) == 2
    assert cached(failing, local_repo).cached
    assert len(executions(counter)) == 2


def test_explicit_receipt_seeds_cached_declared_checks(local_repo):
    command, counter = counted_command(local_repo)
    store.receipt(local_repo, 'TASK-1', 'test', command)
    run = cached(command, local_repo)
    assert run.returncode == 0
    assert len(executions(counter)) == 1


@pytest.mark.skipif(os.name == 'nt', reason='executes a POSIX pre-commit hook')
def test_done_and_hooks_share_one_run_but_explicit_check_runs_fresh(local_repo):
    root = local_repo
    bootstrap.initialize(root, claude=True)
    command, counter = counted_command(root)
    definition = fill_sections(root / 'tasks/TASK-1.md')
    definition.write_text(definition.read_text().replace('---\n\n',
                                                        'test_paths: ["probe.py"]\n---\n\n'))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    done = cli(root, 'done', '--no-code-change', '--task', 'TASK-1')
    assert done.returncode == 0, done.stdout + done.stderr
    assert len(executions(counter)) == 3
    git(root, 'add', 'tasks', '.workorder')
    env = dict(os.environ, PYTHONPATH=str(PACKAGE),
               PATH=str(Path(PYTHON).parent) + os.pathsep + os.environ['PATH'])
    hook = subprocess.run([str(root / '.git/hooks/pre-commit')], cwd=root, env=env,
                          capture_output=True, text=True)
    assert hook.returncode == 0, hook.stdout + hook.stderr
    commit_all(root, 'Record completion metadata')
    stop = subprocess.run([PYTHON, '-m', 'workorder', 'hook', 'stop'], cwd=root, env=env,
                          input='{}', capture_output=True, text=True)
    assert stop.returncode == 0, stop.stdout + stop.stderr
    checked = cli(root, 'check', '--base', base, '--run-tests', '--all-tests')
    assert checked.returncode == 0, checked.stdout + checked.stderr
    assert len(executions(counter)) == 5
    assert store.record(root, 'TASK-1')['status'] == 'DONE'
    assert store.entries(root, 'TASK-1')[-1]['command'] == command


def test_check_diagnoses_own_module_without_dependency_install_advice(local_repo):
    root = local_repo
    (root / 'sample.py').write_text('value = 1\n')
    command = [PYTHON, '-I', '-c', 'import sample']
    task(root, ships='none', test=store.join_command(command))
    base = commit_all(root)
    store.add_note(root, 'TASK-1', 'Check the module import')
    checked = cli(root, 'check', '--base', base, '--run-tests')
    output = checked.stdout + checked.stderr
    assert "cannot import this repository's own module 'sample'" in output
    assert 'working directory' in output and 'PYTHONPATH' in output
    assert 'install the test dependencies' not in output
    assert 'is not installed' not in output


@pytest.mark.parametrize('layout,module', [('flat', 'sample'), ('src', 'sample'),
                                           ('flat', 'sample.child'), ('namespace', 'sample.child')])
def test_receipt_diagnoses_repository_imports_without_install_advice(local_repo, layout, module):
    base = local_repo / 'src' if layout == 'src' else local_repo
    package = base / 'sample'
    package.mkdir(parents=True)
    if layout != 'namespace':
        (package / '__init__.py').write_text('')
    (package / 'child.py').write_text('value = 1\n')
    command = [PYTHON, '-I', '-c', f'import {module}']
    task(local_repo, ships='none', test=store.join_command(command))
    commit_all(local_repo)
    result = cli(local_repo, 'receipt', 'test', '--task', 'TASK-1')
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert "cannot import this repository's own module 'sample'" in output
    assert 'working directory' in output and 'PYTHONPATH' in output
    assert 'is not installed' not in output
