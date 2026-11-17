"""Seventh-pass regressions using real commands and offline repositories."""
import pytest

from workorder import store
from test_workorder import cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v14 import check
from test_v15d import hooked_commit, stop


ADD = 'def add(a, b):\n    return a + b\n'
SUB = '\n\ndef sub(a, b):\n    return a - b\n'


def define(root, tid, function, scope):
    path = task(root, tid, ships='none', test=f'python -m pytest -q tests/test_{function}.py')
    fill_sections(path)
    path.write_text(path.read_text().replace('contract: "none"', f'contract: "none"\ncode_paths: ["{scope}"]'))


def two_tasks(root, second_module=False, broken=False):
    """Land a real DONE claim, then implement a second task within its code paths."""
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    if second_module:
        (root / 'calc').mkdir()
    source = 'calc/add.py' if second_module else 'calc.py'
    scope = 'calc' if second_module else source
    define(root, 'TASK-1', 'add', scope)
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'add')
    (root / source).write_text(ADD)
    module = 'calc.add' if second_module else 'calc'
    (root / 'tests/test_add.py').write_text(
        f'from {module} import add\n\ndef test_add():\n    assert add(2, 2) == 4\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    git(root, 'switch', 'main')
    git(root, 'merge', '--ff-only', 'add')
    define(root, 'TASK-2', 'sub', scope)
    commit_all(root)
    # Model the fetched main ref without a network remote.
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 't2')
    if broken:
        (root / source).write_text(ADD.replace('a + b', 'a - b'))
    if second_module:
        (root / 'calc/sub.py').write_text(SUB.lstrip())
    else:
        with (root / source).open('a') as stream:
            stream.write(SUB)
    module = 'calc.sub' if second_module else 'calc'
    (root / 'tests/test_sub.py').write_text(
        f'from {module} import sub\n\ndef test_sub():\n    assert sub(5, 2) == 3\n')
    commit_all(root)


@pytest.mark.parametrize('second_module', [False, True], ids=['same-file', 'second-module'])
@pytest.mark.parametrize('action', ['done', 'check', 'precommit', 'stop'])
def test_old_claim_does_not_owe_proof_of_second_task(local_repo, second_module, action):
    root = local_repo
    two_tasks(root, second_module)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-2')
    else:
        ok(root, 'done', '--task', 'TASK-2')
        git(root, 'add', '-A')
        if action == 'precommit':
            result = hooked_commit(root)
        elif action == 'stop':
            result = stop(root)
        else:
            commit_all(root)
            result = check(root)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert 'Other:' not in output, output
    assert 'workorder-allow:TASK-1' not in output, output
    assert 'TASK-1: its test still passes' not in output, output
    assert 'TASK-1: its test passes on' not in output, output
    if action == 'done':
        assert store.record(root, 'TASK-2')['status'] == 'DONE'


@pytest.mark.parametrize('second_module', [False, True], ids=['same-file', 'second-module'])
@pytest.mark.parametrize('action', ['done', 'check', 'precommit', 'stop'])
def test_old_claim_still_refuses_broken_add(local_repo, second_module, action):
    root = local_repo
    two_tasks(root, second_module, broken=True)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-2')
    elif action == 'check':
        result = check(root)
    elif action == 'precommit':
        store.add_note(root, 'TASK-2', 'Subtraction implemented')
        git(root, 'add', '-A')
        result = hooked_commit(root)
    else:
        # Make TASK-2 relevant to Stop without claiming the broken change is done.
        store.add_note(root, 'TASK-2', 'Subtraction implemented')
        result = stop(root)
    output = result.stdout + result.stderr
    assert result.returncode == (2 if action == 'stop' else 1), output
    assert "TASK-1's test fails on this code" in output, output
    assert 'test_add' in output, output


@pytest.mark.parametrize('action', ['check', 'precommit', 'stop'])
def test_renewed_landed_claim_checks_regression_without_new_implementation_proof(local_repo, action):
    root = local_repo
    two_tasks(root)
    # The landed add task needs passing regressions; sub needs its own task evidence.
    ok(root, 'renew', '--task', 'TASK-1')
    git(root, 'add', '-A')
    if action == 'check':
        result = check(root)
    elif action == 'precommit':
        result = hooked_commit(root)
    else:
        result = stop(root)
    output = result.stdout + result.stderr
    assert result.returncode == (1 if action == 'check' else 0), output
    assert 'TASK-1: its test still passes with the changed functions stubbed out' not in output
    assert "TASK-1: its test passes on" not in output
    if action == 'check':
        assert 'no task records it' in output


@pytest.mark.parametrize('staged_passes', [False, True])
def test_stop_uses_staged_code_for_an_old_claim(local_repo, staged_passes):
    root = local_repo
    two_tasks(root)
    source = root / 'calc.py'
    good = ADD + SUB + '# staged edit\n'
    bad = good.replace('a + b', 'a - b')
    source.write_text(good if staged_passes else bad)
    git(root, 'add', 'calc.py')
    source.write_text(bad if staged_passes else good)
    result = stop(root)
    output = result.stdout + result.stderr
    assert result.returncode == (0 if staged_passes else 2), output
    if not staged_passes:
        assert "TASK-1's test fails on this code" in output and 'test_add' in output, output


@pytest.mark.parametrize('test_state, target', [('missing', False), ('missing', True),
                                              ('failing', True), ('passing', True)])
def test_unfinished_hook_hides_test_output(local_repo, test_state, target):
    root = local_repo
    ok(root, 'init')
    commit_all(root)
    if target:
        remote = root.parent / 'remote.git'
        git(root, 'init', '--bare', '-q', '--initial-branch=main', str(remote))
        git(root, 'remote', 'add', 'origin', str(remote))
        git(root, 'push', '-q', 'origin', 'main')  # local filesystem transport only
    ok(root, 'new', '--title', 'Multiply', '--owner', 'dev', '--test', 'python3 test_mul.py')
    if test_state != 'missing':
        (root / 'test_mul.py').write_text(
            'print("UNFINISHED_TEST_OUTPUT")\n' + ('assert False\n' if test_state == 'failing' else ''))
    git(root, 'add', '-A')
    result = hooked_commit(root)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert 'Failure:' not in output and 'Traceback' not in output, output
    assert "can't open file" not in output and 'UNFINISHED_TEST_OUTPUT' not in output, output
    if test_state == 'missing':
        notes = [line for line in output.splitlines() if line.startswith('Note:')]
        assert notes == ['Note: TASK-1: its test file is not there yet; an unfinished task may land'], output
    elif test_state == 'failing':
        assert 'task is not claimed finished' in output, output


def test_unfinished_inline_python_is_not_a_missing_file(local_repo):
    root = local_repo
    ok(root, 'init')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    fill_sections(task(root, ships='none', test='python -c "assert 1.0 == 2"'))
    git(root, 'add', '-A')
    result = hooked_commit(root)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert 'test file is not there' not in output, output
    assert 'task is not claimed finished' in output, output
    assert 'Failure:' not in output and 'Traceback' not in output, output


def test_bare_base_summary_when_no_task_changed(local_repo):
    git(local_repo, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    output = ok(local_repo, 'check', '--base', 'origin/main')
    assert output.splitlines()[-2:] == ['no task changed in this change', 'check: OK'], output  # 1.0 P2: verdict last
