"""Narrow item 44: only another task's own, current pending declaration counts."""

import pytest

from workorder import store
from test_workorder import cli, git, local_repo, ok
from test_v05 import commit_all
from test_v13 import finished_multiplication
from test_v15j import A, B, TEST_B, setup


def assert_other_refusal(root, tid, other, *flags):
    task = root / f'tasks/{tid}.md'
    before = task.read_bytes()
    receipts = store.entries(root, tid)
    result = cli(root, 'done', '--task', tid, *flags)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count('Other:') == 1, result.stdout
    assert f'1 other task has findings ({other})' in result.stdout
    assert task.read_bytes() == before
    assert store.entries(root, tid) == receipts


def test_case_11_keeps_other_tasks_weakened_test_blocking(local_repo):
    root = local_repo
    finished_multiplication(root)
    # Current tests pass despite broken multiplication; the base test catches it.
    (root / 'test_mul.py').write_text('from calc import mul\nassert mul(3, 4) == 7\n')
    commit_all(root)
    other_task = (root / 'tasks/TASK-1.md').read_bytes()
    other_receipts = store.entries(root, 'TASK-1')
    assert_other_refusal(root, 'TASK-2', 'TASK-1')
    output = ok(root, 'done', '--task', 'TASK-2', '--needs-review')
    assert output.count('Other:') == 1
    assert store.entries(root, 'TASK-2')[-1]['pending_review'] is True
    assert (root / 'tasks/TASK-1.md').read_bytes() == other_task
    assert store.entries(root, 'TASK-1') == other_receipts


@pytest.mark.parametrize('coverage', ['base_receipt', 'later_test_edit', 'new_finding'])
def test_other_pending_completion_only_covers_its_declared_findings(local_repo, coverage):
    root = local_repo
    setup(root)
    # TASK-2 declares a weakened, still meaningful test while its code is sound.
    weakened = TEST_B.replace('assert b() == 2', 'assert isinstance(b(), int)')
    (root / 'tests/test_b.py').write_text(weakened)
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2', '--needs-review')
    commit_all(root)
    if coverage == 'base_receipt':
        # The target carries the old declaration, but restored the original test.
        (root / 'tests/test_b.py').write_text(TEST_B)
        commit_all(root)
        git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        (root / 'tests/test_b.py').write_text(weakened)
    elif coverage == 'later_test_edit':
        (root / 'tests/test_b.py').write_text(weakened.replace('int)', '(int, float))'))
    else:
        # Same current test, but a new old-test failure that TASK-2 never declared.
        (root / 'b.py').write_text(B.replace('return 2', 'return 3'))
    (root / 'a.py').write_text(A.replace('return 1', 'value = 1\n    return value'))
    commit_all(root)
    other_receipts = store.entries(root, 'TASK-2')
    assert_other_refusal(root, 'TASK-1', 'TASK-2', '--refactor')
    assert store.entries(root, 'TASK-2') == other_receipts


def test_recorded_old_test_failure_can_stay_pending_for_its_own_task(local_repo):
    root = local_repo
    setup(root)
    (root / 'b.py').write_text(B.replace('return 2', 'return 3'))
    (root / 'tests/test_b.py').write_text(TEST_B.replace('b() == 2', 'b() == 3'))
    (root / 'a.py').write_text(A.replace('return 1', 'value = 1\n    return value'))
    commit_all(root)
    assert_other_refusal(root, 'TASK-1', 'TASK-2', '--refactor')
    ok(root, 'done', '--task', 'TASK-2', '--needs-review')
    commit_all(root)
    other_receipts = store.entries(root, 'TASK-2')
    assert any('its tests from origin/main fail' in item['message']
               for item in other_receipts[-1]['pending_findings'])
    output = ok(root, 'done', '--task', 'TASK-1', '--refactor')
    assert output.count('Other:') == 1
    assert store.entries(root, 'TASK-2') == other_receipts
    assert not store.entries(root, 'TASK-1')[-1].get('pending_review')
