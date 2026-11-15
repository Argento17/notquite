"""Round-18 regressions using disposable repositories and installed hooks."""
import os
from pathlib import Path
import subprocess

import pytest

from workorder import gate, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v14 import CODE, TEST, branch, claim


def hooked_commit(root):
    return subprocess.run(['git', 'commit', '-qm', 'Test change'], cwd=root,
        env=dict(os.environ, PYTHONPATH=str(PACKAGE),
                 PATH=str(Path(PYTHON).parent) + os.pathsep + os.environ['PATH']),
        capture_output=True, text=True)


def finished_pytest(root, count=1):
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'calc.py').write_text(CODE)
    (root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\npythonpath = ["."]\n')
    for n in range(1, count + 1):
        (root / f'tests/test_{n}.py').write_text(TEST)
        fill_sections(task(root, f'TASK-{n}', ships='none',
                           test=store.join_command([PYTHON, '-m', 'pytest', '-q', f'tests/test_{n}.py'])))
    commit_all(root)
    for n in range(1, count + 1):
        claim(root, f'TASK-{n}')
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    return base


def rename_test(root):
    git(root, 'mv', 'tests/test_1.py', 'tests/test_addition.py')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('tests/test_1.py', 'tests/test_addition.py'))
    git(root, 'add', '-A')


def stop(root):
    return subprocess.run([PYTHON, '-m', 'workorder', 'hook', 'stop'], cwd=root,
        env=dict(os.environ, PYTHONPATH=str(PACKAGE)), input='{}', capture_output=True, text=True)


def test_renamed_finished_test_commits_renews_and_needs_label(local_repo):
    root = local_repo
    base = finished_pytest(root)
    rename_test(root)
    result = hooked_commit(root)
    assert result.returncode == 0, result.stdout + result.stderr
    output = result.stdout + result.stderr
    assert 'test command changed and passes on this code' in output
    assert 'after committing run workorder renew --task TASK-1' in output
    assert 'workorder-allow:TASK-1' in output
    findings = [line for line in output.splitlines() if 'workorder-allow:TASK-1' in line]
    assert len(findings) == 1, output
    assert 'lost or changed' not in output and 'its test passes on' not in output, output
    assert [line for line in output.splitlines() if line.startswith('Note: TASK-1:')] == findings, output
    refused = cli(root, 'done', '--task', 'TASK-1')
    assert refused.returncode == 1 and 'test changed:' in refused.stdout
    result = cli(root, 'renew', '--task', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    git(root, 'add', '-A')
    result = hooked_commit(root)
    assert result.returncode == 0, result.stdout + result.stderr
    args = ['check', '--base', base, '--run-tests']
    result = cli(root, *args)
    assert result.returncode == 1 and 'workorder-allow:TASK-1' in result.stdout
    result = cli(root, *args, '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('hook', ['precommit', 'stop'])
@pytest.mark.parametrize('change', ['rename', 'edit'])
def test_finished_test_change_is_a_local_note(local_repo, hook, change):
    root = local_repo
    finished_pytest(root)
    if change == 'rename':
        rename_test(root)
    else:
        (root / 'tests/test_1.py').write_text(TEST + '# explanation\n')
        git(root, 'add', '-A')
    result = cli(root, 'check', '--staged') if hook == 'precommit' else stop(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'after committing run workorder renew --task TASK-1' in result.stdout
    assert 'workorder receipt test' not in result.stdout
    assert 'lost or changed' not in result.stdout and 'its test passes on' not in result.stdout, result.stdout


@pytest.mark.parametrize('hook', ['precommit', 'stop'])
@pytest.mark.parametrize('staged_passes', [False, True])
def test_changed_finished_test_uses_staged_code_and_refuses_failure(local_repo, hook, staged_passes):
    root = local_repo
    finished_pytest(root)
    rename_test(root)
    test = root / 'tests/test_addition.py'
    failing = TEST.replace('== 2', '== 99')
    test.write_text(TEST if staged_passes else failing)
    git(root, 'add', '-A')
    test.write_text(failing if staged_passes else TEST)
    result = cli(root, 'check', '--staged') if hook == 'precommit' else stop(root)
    assert result.returncode == (0 if staged_passes else 1 if hook == 'precommit' else 2), result.stdout + result.stderr
    assert ('passes on this code; after committing' in result.stdout) is staged_passes


@pytest.mark.parametrize('mode', ['plain', 'explicit', 'drop'])
def test_renew_refactor_after_later_commit(local_repo, mode):
    root = local_repo
    branch(root, test_text=TEST)
    (root / 'calc.py').write_text(CODE.replace('a / b', '(a) / b'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2', '--refactor')
    commit_all(root)
    (root / 'calc.py').write_text(CODE.replace('a / b', '(a) / (b)'))
    commit_all(root)
    # A separately recorded implementation must not discard the prior declaration.
    ok(root, 'receipt', 'implemented', '--task', 'TASK-2')
    flags = {'plain': [], 'explicit': ['--refactor'], 'drop': ['--no-refactor']}[mode]
    result = cli(root, 'renew', '--task', 'TASK-2', *flags)
    assert result.returncode == 0, result.stdout + result.stderr
    assert store.entries(root, 'TASK-2')[-1].get('refactor', False) is (mode != 'drop')
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    assert result.returncode == (1 if mode == 'drop' else 0), result.stdout + result.stderr


@pytest.mark.parametrize('explicit', [False, True])
@pytest.mark.parametrize('change', ['test', 'base-failure'])
def test_renew_rechecks_refactor_conditions(local_repo, explicit, change):
    root = local_repo
    branch(root, test_text=TEST)
    (root / 'calc.py').write_text(CODE.replace('a / b', '(a) / b'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2', '--refactor')
    commit_all(root)
    if change == 'test':
        (root / 'tests/test_div.py').write_text(TEST + '# edited\n')
        commit_all(root)
    else:
        git(root, 'checkout', '-q', 'main')
        (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
        commit_all(root)
        git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        git(root, 'checkout', '-q', 'feature')
        git(root, 'merge', '-s', 'ours', '--no-edit', 'main')
    result = cli(root, 'renew', '--task', 'TASK-2', *(['--refactor'] if explicit else []))
    assert result.returncode == 1, result.stdout + result.stderr
    assert ('its test files changed' if change == 'test' else 'needs its test to pass on') in result.stdout + result.stderr


@pytest.mark.parametrize('expression', ['x is x', 'x.value is x.value', 'x[0] is x[0]'])
def test_self_identity_asserts_nothing(local_repo, expression):
    base = store.head(local_repo)
    (local_repo / 'test_identity.py').write_text('def test_identity():\n    assert ' + expression + '\n')
    assert gate.assertion_problems(local_repo, base, {'id': 'TASK-1', 'test': 'pytest test_identity.py'})


def test_new_local_reason_is_in_words(local_repo):
    result = cli(local_repo, 'new', '--local', '--title', 'Something', '--owner', 'dev')
    assert result.returncode == 0
    assert 'local id:' in result.stdout and '--local' not in result.stdout


@pytest.mark.parametrize('mode', ['base', 'staged'])
def test_config_edit_has_no_per_task_duplicate_notes(local_repo, mode):
    root = local_repo
    base = finished_pytest(root, count=2)
    (root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\npythonpath = [".", "tests"]\n')
    git(root, 'add', '-A')
    result = cli(root, 'check', *(['--base', base, '--run-tests', '--all-tests'] if mode == 'base' else ['--staged']))
    assert result.returncode == (1 if mode == 'base' else 0)
    findings = [line for line in result.stdout.splitlines() if 'changed' in line and 'pyproject.toml' in line]
    assert len(findings) == 1, result.stdout


@pytest.mark.parametrize('review', [False, True])
def test_done_other_tasks_only_in_one_other_line(local_repo, review):
    root = local_repo
    finished_pytest(root, count=2)
    fill_sections(task(root, 'TASK-3', ships='none', test=store.join_command([PYTHON, 'test_new.py'])))
    (root / 'new.py').write_text('def value():\n    return 3\n')
    (root / 'test_new.py').write_text('from new import value\nassert value() == 3\n')
    (root / 'tests/test_1.py').write_text(TEST.replace('== 2', '== 2.0'))
    (root / 'tests/test_2.py').write_text(TEST.replace('== 2', '== 2.0'))
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-3', *(['--needs-review'] if review else []))
    other = [line for line in result.stdout.splitlines() if 'TASK-1' in line or 'TASK-2' in line]
    assert len(other) == 1 and 'Other:' in other[0], result.stdout


def test_stop_checks_index_only_test_edit(local_repo):
    root = local_repo
    finished_pytest(root)
    path = root / 'tests/test_1.py'
    path.write_text(TEST.replace('== 2', '== 99'))
    git(root, 'add', 'tests/test_1.py')
    path.write_text(TEST)
    result = stop(root)
    assert result.returncode == 2, result.stdout + result.stderr
    assert 'test fails' in result.stderr or 'test failed' in result.stderr or 'no test passed' in result.stderr


def test_passing_test_edit_cannot_hide_prior_stale_claim(local_repo):
    root = local_repo
    finished_pytest(root)
    store.set_task_field(root, 'TASK-1', 'status', 'IN_PROGRESS')
    commit_all(root)
    (root / 'calc.py').write_text(CODE + '# later code\n')
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    commit_all(root)
    store.set_task_field(root, 'TASK-1', 'next', 'Verify')
    (root / 'tests/test_1.py').write_text(TEST + '# passing test edit\n')
    git(root, 'add', '-A')
    result = cli(root, 'check', '--staged')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'was marked DONE' in result.stdout


def test_rename_next_step_commits_before_recording_receipts(local_repo):
    root = local_repo
    finished_pytest(root)
    rename_test(root)
    result = cli(root, 'check', 'TASK-1')
    assert result.returncode == 1
    steps = [line for line in result.stdout.splitlines() if 'Next:' in line]
    assert steps and all('commit' in line and 'then' in line for line in steps), result.stdout
    assert all('workorder renew --task TASK-1' in line for line in steps), result.stdout
