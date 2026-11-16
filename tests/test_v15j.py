"""Tenth-pass regressions using public commands and offline repositories."""

import pytest

from workorder import store
from test_workorder import cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections


A = 'def a():\n    return 1\n'
B = 'def b():\n    return 2\n'
TEST_A = 'from a import a\n\ndef test_a():\n    assert a() == 1\n'
TEST_B = 'from b import b\n\ndef test_b():\n    assert b() == 2\n'


def setup(root, declared=True):
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    for tid, name, code, test_text in [('TASK-1', 'a', A, TEST_A), ('TASK-2', 'b', B, TEST_B)]:
        (root / f'{name}.py').write_text(code)
        (root / f'tests/test_{name}.py').write_text(test_text)
        path = task(root, tid, ships='none', test=f'python -m pytest -q tests/test_{name}.py')
        fill_sections(path)
        if declared or tid == 'TASK-2':
            path.write_text(path.read_text().replace('contract: "none"',
                                                    f'contract: "none"\ncode_paths: ["{name}.py"]'))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')


def change_b(root):
    (root / 'b.py').write_text(B + '\ndef b2():\n    return 3\n')
    (root / 'tests/test_b.py').write_text(TEST_B + '\ndef test_b2():\n    from b import b2\n    assert b2() == 3\n')
    commit_all(root)


def check(root, *flags):
    return cli(root, 'check', '--base', 'origin/main', '--run-tests', *flags)


@pytest.mark.parametrize('declared', [False, True])
@pytest.mark.parametrize('verification_first', [False, True])
def test_verification_ignores_other_tasks_recorded_change(local_repo, declared, verification_first):
    root = local_repo
    setup(root, declared)
    if verification_first:
        ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
        commit_all(root)
    change_b(root)
    ok(root, 'done', '--task', 'TASK-2', '--needs-review')
    commit_all(root)
    if not verification_first:
        output = ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
        assert 'TASK-1 changed:' not in output, output
        assert output.count('Other:') == 1, output
        commit_all(root)
    refused = check(root)
    assert refused.returncode == 1 and 'workorder-allow:TASK-2' in refused.stdout, refused.stdout
    assert 'TASK-1: code changed' not in refused.stdout, refused.stdout
    accepted = check(root, '--allow', 'TASK-2')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert 'TASK-1 finished with --no-code-change' in accepted.stdout


@pytest.mark.parametrize('receipt_kind', ['implemented', 'test', 'legacy'])
def test_undeclared_verification_uses_other_tasks_new_receipts(local_repo, receipt_kind):
    root = local_repo
    setup(root, declared=False)
    change_b(root)
    if receipt_kind == 'legacy':
        # Existing releases can have implementation receipts without a path map.
        store.append(root, 'TASK-2', dict(kind='implemented', commit=store.head(root),
                                         code=store.code_digest(root, store.head(root))))
    elif receipt_kind == 'test':
        result = cli(root, 'receipt', 'test', '--task', 'TASK-2')
        # The passing test is recorded, but TASK-2 has no implementation yet.
        assert result.returncode == 1 and 'no implementation is recorded yet' in result.stdout
        assert store.entries(root, 'TASK-2')[-1]['valid'] is True
    else:
        ok(root, 'receipt', receipt_kind, '--task', 'TASK-2')
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    assert 'TASK-1 DONE:' in output
    commit_all(root)
    accepted = check(root, '--allow', 'TASK-2')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


@pytest.mark.parametrize('coverage', ['none', 'old', 'later_edit'])
def test_undeclared_verification_keeps_uncovered_changes(local_repo, coverage):
    root = local_repo
    setup(root, declared=False)
    if coverage == 'old':
        ok(root, 'receipt', 'implemented', '--task', 'TASK-2')
        commit_all(root)
        git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    change_b(root)
    if coverage == 'later_edit':
        ok(root, 'receipt', 'implemented', '--task', 'TASK-2')
        commit_all(root)
        (root / 'b.py').write_text(B + '\ndef b2():\n    return 4\n')
        commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1', '--no-code-change')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-1: code changed (' in result.stdout and 'b.py' in result.stdout
    assert store.entries(root, 'TASK-1') == []


@pytest.mark.parametrize('declared', [False, True])
@pytest.mark.parametrize('path', ['a.py', 'tests/test_a.py'])
def test_verification_own_changes_refuse_but_task_label_accepts_legacy_claim(local_repo, declared, path):
    root = local_repo
    setup(root, declared)
    (root / path).write_text(A.replace('return 1', 'return int("1")') if path == 'a.py'
                             else TEST_A + '    assert a() != 0\n')
    commit_all(root)
    # Even a receipt for another task must not subtract this task's declared files or tests.
    ok(root, 'receipt', 'implemented', '--task', 'TASK-2')
    commit_all(root)
    before = (root / 'tasks/TASK-1.md').read_bytes()
    refused = cli(root, 'done', '--task', 'TASK-1', '--no-code-change', '--needs-review')
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert f'TASK-1: code changed ({path})' in refused.stdout
    assert store.entries(root, 'TASK-1') == []
    assert (root / 'tasks/TASK-1.md').read_bytes() == before
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    ok(root, 'receipt', 'test', '--task', 'TASK-1')
    latest = store.entries(root, 'TASK-1')[-1]
    # Model a 0.14 test receipt, without no_code_change_base.
    store.append(root, 'TASK-1', {key: latest[key] for key in
                 ('kind', 'commit', 'code', 'command', 'exit_code', 'valid')} | {'no_code_change': True})
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    commit_all(root)
    for flags in ([], ['--approving-review'], ['--review', 'warn'], ['--allow', 'tests,TASK-2']):
        refused = check(root, *flags)
        assert refused.returncode == 1, refused.stdout + refused.stderr
        assert f'TASK-1: code changed ({path})' in refused.stdout
    accepted = check(root, '--allow', 'TASK-1')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert 'workorder-allow:TASK-1' in refused.stdout


@pytest.mark.parametrize('label_only', [False, True])
def test_done_refactor_leaves_other_tasks_review_pending(local_repo, label_only):
    root = local_repo
    setup(root)
    test_text = (TEST_B + '\ndef test_extra():\n    assert True\n' if label_only else
                 TEST_B.replace('b() == 2', 'b() == int("2")'))
    (root / 'tests/test_b.py').write_text(test_text)
    commit_all(root)
    before = (root / 'tasks/TASK-1.md').read_bytes()
    refused = cli(root, 'done', '--task', 'TASK-1', '--no-code-change')
    assert refused.returncode == 1, refused.stdout + refused.stderr
    assert refused.stdout.count('Other:') == 1
    assert store.entries(root, 'TASK-1') == []
    assert (root / 'tasks/TASK-1.md').read_bytes() == before
    ok(root, 'done', '--task', 'TASK-2', '--needs-review')
    commit_all(root)
    other_receipts = store.entries(root, 'TASK-2')
    # Grok's flow: TASK-2's test-only completion is committed before TASK-1 refactors.
    (root / 'a.py').write_text(A.replace('return 1', 'answer = 1\n    return answer'))
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1', '--refactor')
    assert 'TASK-1 DONE:' in output
    assert output.count('Other:') == 1 and '1 other task has findings (TASK-2)' in output
    assert 'Missing:' not in output and 'Next:' not in output
    assert store.entries(root, 'TASK-2') == other_receipts
    assert store.entries(root, 'TASK-1')[-1].get('refactor') is True
    assert not store.entries(root, 'TASK-1')[-1].get('pending_review')
    commit_all(root)
    refused = check(root)
    assert refused.returncode == 1 and 'workorder-allow:TASK-2' in refused.stdout
    approved = check(root, '--approving-review')
    assert approved.returncode == int(label_only), approved.stdout + approved.stderr
    accepted = check(root, '--allow', 'TASK-2')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


@pytest.mark.parametrize('needs_review', [False, True])
def test_done_still_names_and_refuses_other_tasks_failing_test(local_repo, needs_review):
    root = local_repo
    setup(root)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    commit_all(root)
    change_b(root)
    (root / 'a.py').write_text(A.replace('return 1', 'return 0'))
    commit_all(root)
    before = (root / 'tasks/TASK-2.md').read_bytes()
    result = cli(root, 'done', '--task', 'TASK-2', *(['--needs-review'] if needs_review else []))
    assert result.returncode == 1, result.stdout + result.stderr
    assert "regression: TASK-1's test fails on this code" in result.stdout
    assert result.stdout.count('Other:') == 1
    assert (root / 'tasks/TASK-2.md').read_bytes() == before
    assert store.entries(root, 'TASK-2') == []
