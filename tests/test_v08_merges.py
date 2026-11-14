"""Merge imports preserve existing claims; all histories are disposable and local."""

import os
import shlex
import subprocess

import pytest

from workorder import gate, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import PASS, evidence


def two_branch_merge(root, source='target', own_evidence=False):
    """Both branches edit app.py, in separate regions; main squashes the other task."""
    (root / 'app.py').write_text('own = 0\n' + '# unchanged\n' * 20 + 'other = 0\n')
    for tid in ('TASK-1', 'TASK-2'):
        path = task(root, tid, ships='none', test=PASS)
        if source == 'parent':
            path.write_text(path.read_text().replace('---\n\n',
                                                    'merge_ref: refs/heads/release\n---\n\n'))
    base = commit_all(root, 'Open both tasks')
    git(root, 'branch', 'release')
    git(root, 'checkout', '-qb', 'feature')
    app = root / 'app.py'
    app.write_text(app.read_text().replace('own = 0', 'own = 1'))
    commit_all(root, 'Implement own task')
    if own_evidence:
        evidence(root, 'TASK-1')
        commit_all(root, 'Record own implementation and test')
    git(root, 'checkout', '-qb', 'other', 'main')
    app.write_text(app.read_text().replace('other = 0', 'other = 1'))
    commit_all(root, 'Implement other task')
    evidence(root, 'TASK-2')
    store.set_task_field(root, 'TASK-2', 'status', 'DONE')
    commit_all(root, 'Finish other task')
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '-q', '--squash', 'other')
    commit_all(root, 'Squash other task into main')
    git(root, 'checkout', '-q', 'feature')
    git(root, 'merge', '-q', '--no-commit', '--no-ff', 'main')
    return base


@pytest.mark.parametrize('source', ['target', 'parent'])
@pytest.mark.parametrize('mode', ['working', 'staged', 'committed'])
def test_imported_done_is_not_a_new_claim(local_repo, source, mode):
    root = local_repo
    base = two_branch_merge(root, source)
    if source == 'target':
        # The target snapshot alone suffices, even without pending merge metadata.
        git(root, 'merge', '--quit')
    if mode == 'committed':
        commit_all(root, 'Bring main into feature')
    flags = ['--staged'] if mode == 'staged' else []
    result = cli(root, 'check', *flags)
    assert result.returncode == 0, result.stdout + result.stderr
    if mode != 'staged':
        ok(root, 'check', 'TASK-2')
        ok(root, 'check', '--base', base)


def test_pre_commit_allows_main_with_another_squashed_done_task(local_repo):
    root = local_repo
    two_branch_merge(root)
    # Run the installed hook's check entry point through an actual Git commit.
    hook = root / '.git/hooks/pre-commit'
    hook.write_text(f'#!/bin/sh\nexec {shlex.quote(PYTHON)} -m workorder check --staged\n')
    hook.chmod(0o755)
    result = subprocess.run(['git', '-c', 'core.hooksPath=.git/hooks', 'commit', '-qm', 'Merge main'],
                            cwd=root, env={**os.environ, 'PYTHONPATH': str(PACKAGE)},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert len(git(root, 'rev-list', '--parents', '-n', '1', 'HEAD').split()) == 3
    ok(root, 'check')


@pytest.mark.parametrize('change', ['task', 'receipt'])
@pytest.mark.parametrize('staged', [False, True])
def test_import_requires_both_task_and_receipts_to_match(local_repo, change, staged):
    root = local_repo
    two_branch_merge(root)
    if change == 'task':
        path = root / 'tasks/TASK-2.md'
        path.write_text(path.read_text() + '\nClaim updated on feature.\n')
    else:
        implemented = gate.newest_implementation(store.entries(root, 'TASK-2'))
        store.append(root, 'TASK-2', dict(kind='test', commit=implemented['commit'],
                     code=implemented['code'], command=[PYTHON, '-c', 'pass'], exit_code=0, valid=True))
    if staged:
        git(root, 'add', 'tasks', '.workorder')
    result = cli(root, 'check', *(['--staged'] if staged else []))
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-2 is being marked DONE' in result.stdout


def test_staged_import_compares_index_not_unstaged_metadata(local_repo):
    root = local_repo
    two_branch_merge(root)
    path = root / 'tasks/TASK-2.md'
    path.write_text(path.read_text() + '\nUnstaged task edit.\n')
    ok(root, 'check', '--staged')
    assert cli(root, 'check').returncode == 1


@pytest.mark.parametrize('committed', [False, True])
def test_reopening_imported_task_is_still_new_claim_when_target_advances(local_repo, committed):
    root = local_repo
    two_branch_merge(root)
    commit_all(root, 'Merge main')
    store.set_task_field(root, 'TASK-2', 'status', 'IN_PROGRESS')
    commit_all(root, 'Reopen imported task')
    git(root, 'checkout', '-q', 'main')
    (root / 'README.md').write_text('Unrelated main update.\n')
    commit_all(root, 'Advance target without changing its task record')
    git(root, 'checkout', '-q', 'feature')
    app = root / 'app.py'
    app.write_text(app.read_text().replace('other = 1', 'other = 2'))
    store.set_task_field(root, 'TASK-2', 'status', 'DONE')
    git(root, 'add', '-A')
    if committed:
        commit_all(root, 'Reclaim without testing')
    result = cli(root, 'check', *([] if committed else ['--staged']))
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'TASK-2' in result.stdout and 'implemented commit' in result.stdout


@pytest.mark.parametrize('mode', ['check', 'task', 'show'])
def test_stale_own_claim_after_merge_renews_on_branch_before_squash(local_repo, mode):
    root = local_repo
    two_branch_merge(root, own_evidence=True)
    commit_all(root, 'Merge main')
    app = root / 'app.py'
    app.write_text(app.read_text().replace('own = 1', 'own = 2'))
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    commit_all(root, 'Claim own task after changing the combined code')
    command = ['show', '--task', 'TASK-1'] if mode == 'show' else ['check', 'TASK-1'] if mode == 'task' else ['check']
    result = cli(root, *command)
    assert result.returncode == (0 if mode == 'show' else 1), result.stdout + result.stderr
    assert 'workorder renew --task TASK-1 on the branch before pushing' in result.stdout
    if mode != 'check':
        next_line = next(line for line in result.stdout.splitlines()
                         if line.startswith('next step:') or line.startswith('  Next:'))
        assert 'workorder renew --task TASK-1 on the branch before pushing' in next_line
    ok(root, 'renew', '--task', 'TASK-1')
    commit_all(root, 'Renew own evidence on the branch')
    ok(root, 'check')
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '-q', '--squash', 'feature')
    commit_all(root, 'Squash renewed feature')
    ok(root, 'check')
    implemented = gate.newest_implementation(store.entries(root, 'TASK-1'))
    assert gate.merge_match(root, implemented, store.head(root))['method'] == 'same code'
