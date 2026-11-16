"""Staged claims must agree with their committed result; all history is local."""

import os
import subprocess

import pytest

from workorder import store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import evidence, fill_sections


def parallel_finished_tasks(root, remote_target):
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n')
    for n, name in enumerate(('greet', 'bye'), 1):
        fill_sections(task(root, f'TASK-{n}', ships='none', test=f'{PYTHON} test_{name}.py'))
    base = commit_all(root, 'Define both tasks')
    if remote_target:
        git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'switch', '-c', 'feat/bye')
    for n, name in ((2, 'bye'), (1, 'greet')):
        (root / f'{name}.py').write_text(f'def {name}():\n    return "{name}"\n')
        (root / f'test_{name}.py').write_text(f'from {name} import {name}\nassert {name}() == "{name}"\n')
        commit_all(root, f'Implement {name}')
        ok(root, 'done', '--task', f'TASK-{n}')
        commit_all(root, f'Finish {name}')
        if n == 2:
            git(root, 'update-ref', 'refs/remotes/origin/feat/bye', 'HEAD')
            git(root, 'switch', 'main')
    if remote_target:
        git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    ok(root, 'check')


def hook_commit(root):
    return subprocess.run(['git', '-c', 'core.hooksPath=.git/hooks', 'commit', '-qm', 'Land work'],
                          cwd=root, env={**os.environ, 'PYTHONPATH': str(PACKAGE)},
                          capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize('remote_target', [False, True], ids=['local-main', 'origin-main'])
@pytest.mark.parametrize('change', ['clean', 'staged-code', 'staged-doc', 'staged-own',
                                  'unstaged-own', 'untracked-code', 'no-squash-msg',
                                  'code-without-squash-msg'])
def test_squash_claim_hook_matches_committed_check(local_repo, remote_target, change):
    root = local_repo
    parallel_finished_tasks(root, remote_target)
    git(root, 'merge', '--squash', 'origin/feat/bye')
    if change in {'staged-code', 'staged-doc', 'untracked-code', 'code-without-squash-msg'}:
        path = 'extra.md' if change == 'staged-doc' else 'extra.py'
        (root / path).write_text('extra = 42\n')
        if change != 'untracked-code':
            git(root, 'add', path)
    elif change in {'staged-own', 'unstaged-own'}:
        # The declared test still passes; claim freshness must catch this edit.
        (root / 'bye.py').write_text('def bye():\n    return "bye"\n\nextra = 42\n')
        if change == 'staged-own':
            git(root, 'add', 'bye.py')
    if change in {'no-squash-msg', 'code-without-squash-msg'}:
        (root / '.git/SQUASH_MSG').unlink()

    staged = cli(root, 'check', '--staged')
    before = store.head(root)
    hooked = hook_commit(root)
    if hooked.returncode:
        assert store.head(root) == before
        # Bypass only the disposable repo's hook to inspect the refused candidate.
        git(root, 'commit', '-qm', 'Inspect refused commit')
    # Compare with exactly the index snapshot, without the deliberate worktree edits.
    if change == 'unstaged-own':
        git(root, 'restore', 'bye.py')
    elif change == 'untracked-code':
        (root / 'extra.py').unlink()
    committed = cli(root, 'check')
    # A local target advances with the commit; origin/main stays at its old tip.
    expected = int(change == 'staged-own' or
                   (remote_target and change in {'staged-code', 'code-without-squash-msg'}))
    assert committed.returncode == expected, committed.stdout + committed.stderr
    assert staged.returncode == expected, staged.stdout + staged.stderr
    assert (hooked.returncode != 0) == bool(expected), hooked.stdout + hooked.stderr
    if expected:
        assert 'TASK-2 was marked DONE' in committed.stdout
        assert 'TASK-2 is being marked DONE' in staged.stdout


@pytest.mark.parametrize('remote_target', [False, True], ids=['local-main', 'origin-main'])
def test_unrelated_ordinary_commit_beside_finished_task_still_passes(local_repo, remote_target):
    root = local_repo
    parallel_finished_tasks(root, remote_target)
    assert not (root / '.git/SQUASH_MSG').exists()
    (root / 'extra.py').write_text('extra = 42\n')
    git(root, 'add', 'extra.py')
    ok(root, 'check', '--staged')
    hooked = hook_commit(root)
    assert hooked.returncode == 0, hooked.stdout + hooked.stderr
    ok(root, 'check')


@pytest.mark.parametrize('change', ['clean', 'extra-code', 'own-code'])
def test_pending_merge_claim_hook_matches_committed_check(local_repo, change):
    root = local_repo
    ok(root, 'init')
    (root / '.gitignore').write_text('__pycache__/\n')
    fill_sections(task(root, ships='none', test=f'{PYTHON} test_own.py'))
    commit_all(root, 'Define task')
    git(root, 'switch', '-c', 'feature')
    (root / 'own.py').write_text('value = 1\n')
    (root / 'test_own.py').write_text('from own import value\nassert value == 1\n')
    commit_all(root, 'Implement task')
    evidence(root)
    commit_all(root, 'Record evidence before claiming DONE')
    git(root, 'switch', 'main')
    (root / 'other.py').write_text('other = 1\n')
    commit_all(root, 'Advance target')
    git(root, 'switch', 'feature')
    git(root, 'merge', '--no-commit', '--no-ff', 'main')
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    if change == 'extra-code':
        (root / 'extra.py').write_text('extra = 42\n')
    elif change == 'own-code':
        (root / 'own.py').write_text('value = 1\nextra = 42\n')
    git(root, 'add', '-A')
    staged = cli(root, 'check', '--staged')
    hooked = hook_commit(root)
    if hooked.returncode:
        git(root, 'commit', '-qm', 'Inspect refused merge')
    committed = cli(root, 'check')
    expected = int(change != 'clean')
    assert committed.returncode == expected, committed.stdout + committed.stderr
    assert staged.returncode == expected, staged.stdout + staged.stderr
    assert (hooked.returncode != 0) == bool(expected), hooked.stdout + hooked.stderr
