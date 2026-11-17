"""Round-20 local review handoff and completion-label advice."""
import os
import subprocess

import pytest

from test_workorder import cli, git, local_repo, ok
from test_v05 import commit_all
from test_v09_setup import hook_env
from test_v14 import claim
from test_v15c_imports import setup_task
from test_v15d_imports import remapped_editable
from test_v17r import CODE, branch, check, setup


@pytest.mark.parametrize('pending', [False, True])
def test_staged_unavailable_comparison_needs_pending_review(local_repo, tmp_path, pending):
    root = local_repo
    python, _ = remapped_editable(root, tmp_path)
    setup_task(root, python, '')
    (root / 'pkg/unipkg/__init__.py').write_text('def value():\n    return 2\n')
    (root / 'tests/test_value.py').write_text(
        'from unipkg import value\ndef test_value():\n    assert value() == 2\n')
    commit_all(root)
    if pending:
        output = ok(root, 'done', '--task', 'TASK-1', '--needs-review')
        assert 'pending review: the pull request needs workorder-allow:TASK-1' in output
    else:
        claim(root, 'TASK-1')
    git(root, 'add', 'tasks', '.workorder')

    result = cli(root, 'check', '--staged')
    assert result.returncode == (0 if pending else 1), result.stdout + result.stderr
    if not pending:
        assert 'could not compare' in result.stdout
        return
    assert 'Note: TASK-1: could not compare' in result.stdout
    assert 'Note: TASK-1: pending review' in result.stdout
    for flags in ([], ['--run-tests'], ['--run-tests', '--all-tests']):
        result = cli(root, 'check', '--staged', *flags)
        assert result.returncode == 0, result.stdout + result.stderr
    for flags in ([], ['--review', 'warn'], ['--allow', 'tests']):
        result = check(root, '--all-tests', '--require-task', *flags)
        assert result.returncode == 1, result.stdout + result.stderr
        assert 'workorder-allow:TASK-1' in result.stdout
    for flags in (['--approving-review'], ['--approving-review', '--review', 'warn']):
        result = check(root, '--all-tests', '--require-task', *flags)  # 0.18: approval clears it
        assert result.returncode == 0, result.stdout + result.stderr
    result = check(root, '--all-tests', '--require-task', '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr

    # Exercise the installed pre-commit entry point, not only its CLI command.
    if os.name != 'nt':
        ok(root, 'init')
        result = subprocess.run(['sh', str(root / '.git/hooks/pre-commit')],
                                cwd=root, env=hook_env(), capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Note: TASK-1: could not compare' in result.stdout
        assert 'check: OK' in result.stdout


@pytest.mark.parametrize('completion,kind', [('refactor', 'refactor'),
                                           ('no-code-change', 'verification')])
@pytest.mark.parametrize('declaration', ['missing', 'branch-only', 'wrong', 'matching'])
def test_done_and_staged_exemptions_explain_label_once(local_repo, completion, kind, declaration):
    root = local_repo
    base_kind = kind if declaration == 'matching' else (
        'verification' if kind == 'refactor' else 'refactor') if declaration == 'wrong' else None
    setup(root, kind=base_kind)
    branch(root)
    if declaration == 'branch-only':
        path = root / 'tasks/TASK-1.md'
        path.write_text(path.read_text().replace('\ncontract:', f'\nkind: {kind}\ncontract:'))
    if completion == 'refactor':
        (root / 'calc.py').write_text(CODE.replace('return 1', 'return 1 + 0'))
    if completion == 'refactor' or declaration == 'branch-only':
        commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1', '--' + completion)
    git(root, 'add', 'tasks', '.workorder')
    staged = ok(root, 'check', '--staged')
    label_notes = [[line for line in text.splitlines()
                    if line.startswith('Note:') and 'workorder-allow:TASK-1' in line]
                   for text in (output, staged)]
    assert [len(notes) for notes in label_notes] == [0 if declaration == 'matching' else 1] * 2, (output, staged)
    for notes in label_notes:
        if notes:
            assert 'the pull request needs workorder-allow:TASK-1 or an approving review' in notes[0]
    result = check(root, '--approving-review', '--review', 'warn')
    assert result.returncode == 0, result.stdout + result.stderr  # 0.18: approval clears it
    assert check(root, '--allow', 'TASK-1').returncode == 0


def test_pending_review_does_not_hide_staged_current_test_failure(local_repo):
    root = local_repo
    setup(root)
    branch(root)
    (root / 'calc.py').write_text(CODE.replace('return 1', 'return 1 + 0'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    (root / 'calc.py').write_text(CODE.replace('return 1', 'return 2'))
    git(root, 'add', 'calc.py', 'tasks', '.workorder')
    result = cli(root, 'check', '--staged')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'test fails on this code' in result.stdout or 'test failed' in result.stdout
