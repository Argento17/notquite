"""Round-21 lane G: retiring finished tasks, and acceptance items tied to tests."""
import os
import subprocess

import pytest

from test_workorder import cli, git, local_repo, ok
from test_v05 import commit_all
from test_v17r import check


CODE = 'def value():\n    return 1\n'
TASK = '''---
id: "TASK-1"
title: "Double"
owner: "alex"
status: "IN_PROGRESS"
ships: "none"
waits_on: []
test: "python -m pytest -q tests/test_double.py"
code_paths: ["calc.py"]
contract: "none"
---

## Scope

calc.double

## Acceptance

{acceptance}

## Progress
'''
DOUBLE = 'from calc import double\n\ndef test_double():\n    assert double(2) == 4\n'


def landed(root, acceptance='- double(2) is 4', test=DOUBLE, code='def double(x):\n    return 2 * x\n'):
    """TASK-1 lands on main with a new test; origin/main points at the landing."""
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    (root / 'tasks').mkdir()
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_value.py').write_text('from calc import value\n\ndef test_value():\n    assert value() == 1\n')
    (root / 'tasks/TASK-1.md').write_text(TASK.format(acceptance=acceptance))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-qc', 'feature')
    (root / 'calc.py').write_text(CODE + code)
    (root / 'tests/test_double.py').write_text(test)
    commit_all(root)


def finish(root):
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    git(root, 'switch', '-q', 'main')
    git(root, 'merge', '-q', '--ff-only', 'feature')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-qc', 'cleanup')


# 97: retiring a finished task

def test_retire_needs_a_finished_task_and_a_reason(local_repo):
    root = local_repo
    landed(root)
    result = cli(root, 'retire', 'TASK-1', '--reason', 'old')
    assert result.returncode == 1
    assert 'only a DONE or CLOSED task can be retired' in result.stderr
    finish(root)
    result = cli(root, 'retire', 'TASK-1', '--reason', '  ')
    assert result.returncode == 1 and 'needs a reason' in result.stderr
    output = ok(root, 'retire', 'TASK-1', '--reason', 'double() is replaced')
    assert 'TASK-1 RETIRED (was DONE)' in output
    assert 'workorder-allow:TASK-1' in output
    text = (root / 'tasks/TASK-1.md').read_text()
    assert 'status: "RETIRED"' in text and 'Retired (was DONE): double() is replaced' in text
    assert (root / '.workorder').is_dir() and any((root / '.workorder').rglob('*'))
    result = cli(root, 'retire', 'TASK-1', '--reason', 'again')
    assert result.returncode == 1 and 'TASK-1 is RETIRED' in result.stderr


def test_retirement_is_a_finding_a_label_or_approval_clears(local_repo):
    root = local_repo
    landed(root)
    finish(root)
    ok(root, 'retire', 'TASK-1', '--reason', 'double() is replaced')
    commit_all(root)
    git(root, 'add', 'tasks')
    assert cli(root, 'check', '--staged').returncode == 0
    git(root, 'rm', '-q', 'tests/test_double.py')
    commit_all(root)
    result = check(root, '--all-tests', '--require-task')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-1: status changed: DONE -> RETIRED' in result.stdout
    assert 'workorder-allow:TASK-1' in result.stdout
    assert 'regression:' not in result.stdout and 'no test passed' not in result.stdout
    assert '(0 claimed DONE or CLOSED, 1 retired)' in result.stdout
    # Removing the retired task's test file edits existing tests: approval alone is not enough.
    result = check(root, '--all-tests', '--require-task', '--approving-review')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'an approving review is not enough: it edits existing tests' in result.stdout
    assert 'accepted by an approving review: TASK-1 (status changed: DONE -> RETIRED)' in result.stdout
    for flags in (['--allow', 'TASK-1'], ['--allow', 'TASK-1', '--approving-review']):
        result = check(root, '--all-tests', '--require-task', *flags)
        assert result.returncode == 0, result.stdout + result.stderr


def test_without_retirement_removing_a_finished_tests_stays_refused(local_repo):
    root = local_repo
    landed(root)
    finish(root)
    git(root, 'rm', '-q', 'tests/test_double.py')
    commit_all(root)
    for flags in ([], ['--allow', 'TASK-1'], ['--approving-review']):
        result = check(root, '--all-tests', '--require-task', *flags)
        assert result.returncode == 1, result.stdout + result.stderr
        assert "regression: TASK-1's test fails on this code" in result.stdout


def test_retired_task_owns_no_tests_and_does_not_rerun(local_repo):
    root = local_repo
    landed(root)
    finish(root)
    ok(root, 'retire', 'TASK-1', '--reason', 'double() is replaced')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-qc', 'later')
    # The old behaviour changes; the retired task's test would now fail.
    (root / 'calc.py').write_text(CODE + 'def double(x):\n    return 3 * x\n')
    commit_all(root)
    result = check(root, '--all-tests')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'TASK-1' not in result.stdout.replace('no task changed', '')
    # Its old test file is an ordinary unowned test now: changes follow the repository rules.
    (root / 'tests/test_double.py').write_text(DOUBLE.replace('== 4', '== 6'))
    commit_all(root)
    result = check(root, '--all-tests')
    assert 'workorder-allow:TASK-1' not in result.stdout, result.stdout
    output = ok(root, 'show', '--task', 'TASK-1')
    assert output.startswith('TASK-1 RETIRED (finished, then retired)')
    assert 'Acceptance list' not in output
    output = ok(root, 'check', 'TASK-1')
    assert 'Result: retired; no completion claimed' in output
    assert ok(root, 'ready') .strip() == 'no open work'


@pytest.mark.parametrize('before', ['IN_PROGRESS', None])
def test_hand_written_retirement_of_unfinished_task_is_refused(local_repo, before):
    root = local_repo
    landed(root)
    path = root / 'tasks/TASK-1.md'
    if before is None:
        git(root, 'rm', '-q', '--cached', 'tasks/TASK-1.md')
        git(root, 'commit', '-qm', 'drop')
        git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('"IN_PROGRESS"', '"RETIRED"'))
    commit_all(root)
    for flags in ([], ['--allow', 'TASK-1'], ['--approving-review']):
        result = check(root, *flags)
        assert result.returncode == 1, result.stdout + result.stderr
        assert 'only a DONE or CLOSED task can be retired' in result.stdout


def test_deleting_a_retired_task_file_stays_refused(local_repo):
    root = local_repo
    landed(root)
    finish(root)
    ok(root, 'retire', 'TASK-1', '--reason', 'gone')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'rm', '-q', 'tasks/TASK-1.md')
    commit_all(root)
    for flags in ([], ['--approving-review']):
        result = check(root, *flags)
        assert result.returncode == 1, result.stdout + result.stderr
        assert 'task file removed since origin/main' in result.stdout


# 98: acceptance items tied to tests

JOIN = 'def join(a, b):\n    return a + b\n'
EMPTY = 'from paths import join\n\ndef test_empty():\n    assert join("", "b") == "b"\n'
TIED = ['join("a/", "/b") is "a/b" (test: tests/test_paths.py::test_trailing_slash)',
        'join("a", "b") is "a/b" (test: tests/test_paths.py::test_no_slash)',
        'the docs mention it']


def opened(root, acceptance=TIED, extra=()):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    (root / 'paths.py').write_text(JOIN)
    (root / 'tests/test_paths.py').write_text(EMPTY)
    commit_all(root)
    args = [arg for item in acceptance for arg in ('--acceptance', item)]
    ok(root, 'new', '--local', '--title', 'Trailing slash', '--owner', 'alex', '--test',
       'python -m pytest -q tests/test_paths.py', '--code-paths', 'paths.py', *args, *extra)
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('<!-- What is in scope, and what is explicitly out. -->', 'paths.join'))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-qc', 'fix')


def work(root, code, tests):
    (root / 'paths.py').write_text(code)
    (root / 'tests/test_paths.py').write_text(EMPTY + tests)
    commit_all(root)
    return cli(root, 'done', '--task', 'TASK-1')


RIGHT = 'def join(a, b):\n    return "/".join(p.strip("/") for p in (a, b) if p)\n'
WRONG = 'def join(a, b):\n    return a + b.lstrip("/")\n'
SLASH = '\ndef test_trailing_slash():\n    assert join("a/", "/b") == "a/b"\n'
NO_SLASH = '\ndef test_no_slash():\n    assert join("a", "b") == "{}"\n'


def test_new_writes_acceptance_items_and_checks_node_ids(local_repo):
    root = local_repo
    opened(root)
    text = (root / 'tasks/TASK-1.md').read_text()
    acceptance = text.split('## Acceptance', 1)[1].split('## Progress', 1)[0]
    assert all('- ' + item in acceptance for item in TIED)
    result = cli(root, 'new', '--local', '--title', 'x', '--owner', 'a', '--acceptance', 'bad (test: tests/test_paths.py)')
    assert result.returncode == 1 and 'is not a pytest node id' in result.stderr
    assert not (root / 'tasks/TASK-2.md').exists()
    output = ok(root, 'show', '--task', 'TASK-1')
    assert '  [test] - join("a/", "/b") is "a/b"' in output
    assert '  - the docs mention it' in output
    assert '(2 of 3 items tied to a test that workorder done checks; [test] marks them)' in output


def test_wrong_fix_with_matching_test_is_refused(local_repo):
    root = local_repo
    opened(root)
    result = work(root, WRONG, SLASH)
    assert result.returncode == 1, result.stdout + result.stderr
    assert ("acceptance item 'join(\"a\", \"b\") is \"a/b\"': its test tests/test_paths.py::test_no_slash "
            "does not exist") in result.stdout
    assert 'DONE was not written' in result.stdout
    result = work(root, WRONG, SLASH + NO_SLASH.format('ab'))
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'test_no_slash already passes on origin/main' in result.stdout
    assert 'status: "IN_PROGRESS"' in (root / 'tasks/TASK-1.md').read_text()
    result = work(root, RIGHT, SLASH + NO_SLASH.format('a/b'))
    assert result.returncode == 0, result.stdout + result.stderr
    assert ('Acceptance: 2 of 3 items tied to tests that pass now and failed before; '
            '1 item not tied to a test (a reviewer confirms it)') in result.stdout
    assert 'Acceptance is not checked' not in result.stdout


def test_tied_test_must_be_run_by_the_command_and_pass(local_repo):
    root = local_repo
    opened(root, acceptance=['slash (test: tests/test_other.py::test_trailing_slash)'])
    (root / 'tests/test_other.py').write_text('from paths import join\n' + SLASH)
    result = work(root, RIGHT, SLASH)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "test_other.py::test_trailing_slash is not run by the task's test command" in result.stdout
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('test_other.py', 'test_paths.py'))
    result = work(root, RIGHT, '\nimport pytest\n\n@pytest.mark.skip\n' + SLASH.lstrip('\n'))
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'test_paths.py::test_trailing_slash does not pass now' in result.stdout
    result = work(root, RIGHT, SLASH)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Acceptance: 1 of 1 item tied to tests that pass now and failed before' in result.stdout


def test_untied_acceptance_keeps_the_old_line(local_repo):
    root = local_repo
    opened(root, acceptance=['the docs mention it'])
    result = work(root, RIGHT, SLASH)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Acceptance is not checked by Workorder: confirm each item is met.' in result.stdout
    assert 'tied to tests' not in result.stdout


def test_refactor_kind_need_not_fail_before(local_repo):
    root = local_repo
    opened(root, acceptance=['empty first part (test: tests/test_paths.py::test_empty)'])
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('\ncontract:', '\nkind: "refactor"\ncontract:'))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (root / 'paths.py').write_text('def join(a, b):\n    return "".join((a, b))\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1', '--refactor')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Acceptance: 1 of 1 item tied to tests that pass now (the task is a refactor' in result.stdout


def test_done_refuses_a_retired_task(local_repo):
    root = local_repo
    landed(root)
    finish(root)
    ok(root, 'retire', 'TASK-1', '--reason', 'gone')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-1 is RETIRED; it no longer claims completion' in result.stdout
    assert 'status: "RETIRED"' in (root / 'tasks/TASK-1.md').read_text()
