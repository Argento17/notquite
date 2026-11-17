"""Sixth-pass usability regressions, with offline repositories and installed hooks."""
import os
import subprocess

import pytest

from workorder import store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v06b import fill_sections
from test_v14 import CODE, TEST
from test_v15d import finished_pytest, hooked_commit, stop


REPAIR = ('TASK-1: test command changed and passes on this code; after committing '
          'run workorder renew --task TASK-1; the pull request needs workorder-allow:TASK-1')


def commit_with_hook(root):
    git(root, 'add', '-A')
    result = hooked_commit(root)
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout + result.stderr


def finished_on_branch(root, status='DONE', refactor=False):
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    command = 'python -m pytest -q tests/test_fn.py'
    fill_sections(task(root, ships='none', test=command, kind='refactor' if refactor else None))
    if refactor:
        (root / 'fn.py').write_text(CODE)
        (root / 'tests/test_fn.py').write_text(TEST.replace('from calc', 'from fn'))
    commit_with_hook(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'work')
    (root / 'fn.py').write_text(CODE.replace('a / b', '(a + 0) / b') if refactor else CODE)
    (root / 'tests/test_fn.py').write_text(TEST.replace('from calc', 'from fn'))
    commit_with_hook(root)
    output = ok(root, 'done', '--task', 'TASK-1', *(['--refactor'] if refactor else []))
    assert 'TASK-1 DONE:' in output
    if status != 'DONE':
        store.set_task_field(root, 'TASK-1', 'status', status)
    commit_with_hook(root)


def rename_branch_test(root, passes=True, staged=True):
    git(root, 'mv', 'tests/test_fn.py', 'tests/test_fn2.py')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('tests/test_fn.py', 'tests/test_fn2.py'))
    if not passes:
        path = root / 'tests/test_fn2.py'
        path.write_text(path.read_text().replace('== 2', '== 99'))
    git(root, 'add', '-A')
    if not staged:
        git(root, 'reset', '-q', 'HEAD')


@pytest.mark.parametrize('status', ['DONE', 'CLOSED'])
def test_branch_finished_rename_commits_renews_and_needs_label(local_repo, status):
    root = local_repo
    finished_on_branch(root, status)
    rename_branch_test(root)
    result = hooked_commit(root)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert output.count(REPAIR) == 1, output
    assert 'test changed:' not in output, output
    refused = cli(root, 'done', '--task', 'TASK-1')
    assert refused.returncode == 1 and 'test changed:' in refused.stdout
    ok(root, 'renew', '--task', 'TASK-1')
    commit_with_hook(root)
    args = ['check', '--base', 'origin/main', '--run-tests']
    result = cli(root, *args)
    assert result.returncode == 1 and 'workorder-allow:TASK-1' in result.stdout
    ok(root, *args, '--allow', 'TASK-1')


@pytest.mark.parametrize('status', ['DONE', 'CLOSED'])
@pytest.mark.parametrize('staged', [False, True])
@pytest.mark.parametrize('passes', [False, True])
def test_branch_finished_rename_stop(local_repo, status, staged, passes):
    root = local_repo
    finished_on_branch(root, status)
    rename_branch_test(root, passes, staged)
    result = stop(root)
    output = result.stdout + result.stderr
    assert result.returncode == (0 if passes else 2), output
    if passes:
        assert output.count(REPAIR) == 1, output
        assert 'test changed:' not in output, output
    else:
        assert 'test failed' in output or 'no test passed' in output, output
        assert REPAIR not in output


@pytest.mark.parametrize('staged_passes', [False, True])
def test_branch_finished_rename_hook_uses_staged_code(local_repo, staged_passes):
    root = local_repo
    finished_on_branch(root)
    rename_branch_test(root, staged_passes)
    path = root / 'tests/test_fn2.py'
    path.write_text(TEST.replace('from calc', 'from fn').replace('== 2', '== 99')
                    if staged_passes else TEST.replace('from calc', 'from fn'))
    result = hooked_commit(root)
    output = result.stdout + result.stderr
    assert result.returncode == (0 if staged_passes else 1), output
    assert (REPAIR in output) is staged_passes, output


@pytest.mark.parametrize('hook', ['precommit', 'stop'])
def test_finished_command_without_test_receipts_still_refuses(local_repo, hook):
    root = local_repo
    ok(root, 'init')
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, '-c', 'pass'])))
    commit_with_hook(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'work')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('-c pass', '-c print(1)'))
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    git(root, 'add', '-A')
    result = hooked_commit(root) if hook == 'precommit' else stop(root)
    output = result.stdout + result.stderr
    assert result.returncode == (1 if hook == 'precommit' else 2), output
    assert 'test changed:' in output and REPAIR not in output


@pytest.mark.parametrize('branch_edit', [False, True])
@pytest.mark.parametrize('hook', ['precommit', 'stop'])
def test_pending_merge_notes_only_branch_test_edits(local_repo, branch_edit, hook):
    root = local_repo
    finished_on_branch(root, refactor=True)
    git(root, 'switch', 'main')
    path = root / 'tests/test_fn.py'
    path.write_text(path.read_text() + '\ndef test_more():\n    assert div(8, 2) == 4\n')
    commit_with_hook(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', 'work')
    git(root, 'merge', '--no-commit', '--no-ff', 'main')
    if branch_edit:
        path.write_text(path.read_text() + '    assert div(12, 3) == 4\n')
        git(root, 'add', '-A')
    result = cli(root, 'check', '--staged') if hook == 'precommit' else stop(root)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    if branch_edit:
        assert 'it is a declared refactor' in output
        assert 'renew --task TASK-1 --no-refactor' in output
        result = hooked_commit(root)
        assert result.returncode == 0, result.stdout + result.stderr
    else:
        assert 'after committing run workorder renew' not in output, output
        assert 'workorder-allow:TASK-1' not in output, output
        output = commit_with_hook(root)
        assert 'after committing run workorder renew' not in output
        output = ok(root, 'check', '--base', 'origin/main', '--run-tests')
        assert 'workorder-allow:TASK-1' not in output


@pytest.mark.parametrize('count', [1, 2])
def test_other_findings_grammar(local_repo, count):
    root = local_repo
    finished_pytest(root, count=count)
    fill_sections(task(root, 'TASK-3', ships='none', test=store.join_command([PYTHON, 'test_new.py'])))
    (root / 'new.py').write_text('def value():\n    return 3\n')
    (root / 'test_new.py').write_text('from new import value\nassert value() == 3\n')
    for n in range(1, count + 1):
        (root / f'tests/test_{n}.py').write_text(TEST.replace('== 2', '== 2.0'))
    commit_with_hook(root)
    result = cli(root, 'done', '--task', 'TASK-3')
    other = [line for line in result.stdout.splitlines() if 'Other:' in line]
    expected = ('1 other task has findings (TASK-1)' if count == 1
                else '2 other tasks have findings (TASK-1, TASK-2)')
    assert len(other) == 1 and expected in other[0], result.stdout


@pytest.mark.parametrize('count', [1, 2])
def test_all_tests_summary_when_no_task_changed(local_repo, count):
    root = local_repo
    finished_pytest(root, count=count)
    output = ok(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests')
    expected = ("no task changed in this change; ran 1 finished task's declared test" if count == 1
                else "no task changed in this change; ran 2 finished tasks' declared tests")
    assert output.splitlines()[-1].startswith(expected), output
    assert 'checked 0 tasks' not in output


@pytest.mark.skipif(os.name == 'nt', reason='builds an empty POSIX PATH for the shell hook')
def test_installed_hook_missing_workorder_explains_virtualenv(local_repo, tmp_path):
    ok(local_repo, 'init')
    empty_path = tmp_path / 'empty-path'
    empty_path.mkdir()
    result = subprocess.run(['/bin/sh', str(local_repo / '.git/hooks/pre-commit')], cwd=local_repo,
                            env=dict(os.environ, PATH=str(empty_path)), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'pipx install workorder' in result.stderr
    assert 'put the virtualenv that has workorder on PATH' in result.stderr


@pytest.mark.parametrize('target', [False, True])
def test_done_without_changes_explains_task_first(local_repo, target):
    root = local_repo
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, '-c', 'pass'])))
    (root / 'fn.py').write_text(CODE)
    commit_with_hook(root)
    if target:
        git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1
    missing = [line for line in result.stdout.splitlines() if 'Missing:' in line]
    assert len(missing) == 1, result.stdout
    assert 'nothing was implemented; land the task on main first, then do the work on a branch' in missing[0]


def test_named_task_run_tests_explains_refusal_without_running(local_repo):
    root = local_repo
    task(root, ships='none', test=store.join_command([PYTHON, '-c', 'print("TEST_WAS_RUN")']))
    result = cli(root, 'check', 'TASK-1', '--run-tests')
    assert result.returncode == 1
    assert not result.stdout, result.stdout
    assert result.stderr.splitlines() == [
        'workorder: --run-tests does not run with a task name; use workorder check --run-tests without a task']


@pytest.mark.parametrize('filename', ['AGENTS.md', 'CLAUDE.md'])
def test_init_instructions_distinguish_unfinished_work_and_agents(local_repo, filename):
    ok(local_repo, 'init')
    text = (local_repo / filename).read_text()
    assert 'Keep unfinished progress with `workorder note' in text
    assert '; done clears next.' in text
    assert 'Set `WORKORDER_AGENT` when sharing a Git identity' in text


def test_branch_finished_test_edit_keeps_commit_renew_flow(local_repo):
    root = local_repo
    finished_on_branch(root)
    path = root / 'tests/test_fn.py'
    path.write_text(path.read_text() + '    assert div(8, 2) == 4\n')
    output = commit_with_hook(root)
    assert 'test files changed and passes on this code' in output
    ok(root, 'renew', '--task', 'TASK-1')
    commit_with_hook(root)
    ok(root, 'check', '--base', 'origin/main', '--run-tests', '--allow', 'TASK-1')


@pytest.mark.parametrize('hook', ['precommit', 'stop'])
def test_edit_after_renewed_rename_has_one_repair_note(local_repo, hook):
    root = local_repo
    finished_on_branch(root)
    rename_branch_test(root)
    commit_with_hook(root)
    ok(root, 'renew', '--task', 'TASK-1')
    commit_with_hook(root)
    path = root / 'tests/test_fn2.py'
    path.write_text(path.read_text() + '    assert div(8, 2) == 4\n')
    git(root, 'add', '-A')
    result = hooked_commit(root) if hook == 'precommit' else stop(root)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert output.count('after committing run workorder renew --task TASK-1') == 1, output
    assert REPAIR in output and 'test changed:' not in output
