"""Round-20 regressions, using disposable local repositories and real Git hooks."""

import os
import subprocess

import pytest

from workorder import gate, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v16 import HONEST, OLD_TEST, shared_file


def code_inside_tests(root, *, explicit=False, added=None):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    (root / 'pytest.ini').write_text('[pytest]\ntestpaths = tests\n')
    (root / 'tests/calc.py').write_text('def add(a, b):\n    return a + b\n')
    (root / 'tests/helpers.py').write_text('def pair():\n    return (2, 2)\n')
    old = 'from calc import add\n\ndef test_add():\n    assert add(2, 2) == 4\n'
    (root / 'tests/test_calc.py').write_text(old)
    path = task(root, ships='none', test=f'{PYTHON} -m pytest -q' + (' tests' if explicit else ''))
    fill_sections(path)
    path.write_text(path.read_text().replace('contract: "none"',
                                            'contract: "none"\ncode_paths: ["tests/calc.py"]'))
    base = commit_all(root)
    git(root, 'switch', '-c', 'feature')
    (root / 'tests/calc.py').write_text(
        'def add(a, b):\n    return sum((a, b))\n\ndef sub(a, b):\n    return a - b\n')
    (root / 'tests/helpers.py').write_text('def pair():\n    return (2, 2)\n\ndef sub_pair():\n    return (5, 3)\n')
    (root / 'tests/test_calc.py').write_text(old + (added if added is not None else
        '\nclass TestSub:\n    def test_sub(self):\n        from calc import sub\n'
        '        from helpers import sub_pair\n        assert sub(*sub_pair()) == 2\n'
        '\ndef test_add_pair():\n    from helpers import pair\n    assert add(*pair()) == 4\n'))
    commit_all(root)
    return base


@pytest.mark.parametrize('explicit', [False, True], ids=['testpaths', 'folder-command'])
def test_declared_code_inside_tests_reaches_done(local_repo, explicit):
    root = local_repo
    base = code_inside_tests(root, explicit=explicit)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert 'TASK-1 DONE:' in output
    assert 'workorder-allow:' not in output
    commit_all(root)
    ok(root, 'check', '--base', base, '--run-tests', '--all-tests', '--require-task')


@pytest.mark.parametrize('staged', [False, True])
def test_declared_code_is_not_test_material_but_its_helper_is(local_repo, staged):
    root = local_repo
    base = code_inside_tests(root)
    record = store.record(root, 'TASK-1')
    for material in (gate.test_files(root, record, base, staged),
                     gate.strict_test_files(root, record, base, staged),
                     gate.repository_test_files(root, base, {'TASK-1': record}, staged)):
        assert 'tests/calc.py' not in material
        assert {'tests/test_calc.py', 'tests/helpers.py'} <= material
    # The same path can still be test material for another task.
    other = {**record, 'code_paths': ['other.py']}
    assert 'tests/calc.py' in gate.strict_test_files(root, other, base, staged)


def test_declared_code_inside_tests_is_stubbed(local_repo):
    code_inside_tests(local_repo, added='\ndef test_sub_callable():\n    from calc import sub\n'
                      '    assert callable(sub)\n')
    result = cli(local_repo, 'done', '--task', 'TASK-1')
    assert result.returncode == 1
    assert 'its new or changed tests still pass with the changed functions stubbed out' in result.stdout
    assert store.entries(local_repo, 'TASK-1') == []


def real_merge(root):
    return subprocess.run(['git', 'merge', '--no-ff', 'main', '-m', 'Merge main'], cwd=root,
                          env=dict(os.environ, PYTHONPATH=str(PACKAGE)),
                          capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize('broken', [False, True], ids=['renew-note', 'failing-test'])
def test_plain_merge_checks_finished_task(local_repo, broken):
    root = local_repo
    shared_file(root, HONEST, old_test=OLD_TEST + '\ndef test_unrelated():\n    from calc import unrelated\n'
                '    assert unrelated() > 0\n',
                other_code='\n\ndef unrelated():\n    return 41\n')
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    before = store.head(root)
    git(root, 'switch', 'main')
    path = root / 'calc.py'
    path.write_text(path.read_text().replace('return 41', 'return -1' if broken else 'return 42'))
    commit_all(root)
    git(root, 'switch', 'feature')
    result = real_merge(root)
    output = result.stdout + result.stderr
    if broken:
        assert result.returncode != 0, output
        assert store.head(root) == before
        assert 'test_unrelated' in output and 'failed' in output
    else:
        assert result.returncode == 0, output
        assert 'Note: TASK-1: its code changed after it was finished (calc.py)' in output
        assert 'workorder renew --task TASK-1' in output
        assert len(git(root, 'rev-list', '--parents', '-n', '1', 'HEAD').split()) == 3


def test_init_check_reports_missing_merge_hook_without_writing(local_repo):
    ok(local_repo, 'init')
    hook = local_repo / '.git/hooks/pre-merge-commit'
    hook.unlink(missing_ok=True)
    result = cli(local_repo, 'init', '--check')
    assert result.returncode == 1, result.stdout + result.stderr
    assert '.git/hooks/pre-merge-commit: missing or not executable' in result.stdout
    assert not hook.exists()
    ok(local_repo, 'init')
    ok(local_repo, 'init', '--check')


@pytest.mark.parametrize('exit_code', [0, 7])
def test_init_preserves_and_chains_merge_hook(local_repo, exit_code):
    root = local_repo
    hook = root / '.git/hooks/pre-merge-commit'
    original = f'#!/bin/sh\necho PREVIOUS-MERGE\nexit {exit_code}\n'
    hook.write_text(original)
    hook.chmod(0o755)
    refused = cli(root, 'init')
    assert refused.returncode == 1 and '--force' in refused.stdout + refused.stderr
    assert hook.read_text() == original
    assert not (root / '.git/hooks/pre-commit').exists()
    output = ok(root, 'init', '--force')
    assert 'pre-merge-commit.workorder-previous' in output
    previous = hook.with_name('pre-merge-commit.workorder-previous')
    assert previous.read_text() == original
    ok(root, 'init', '--force')
    commit_all(root)
    git(root, 'switch', '-c', 'feature')
    (root / 'branch.md').write_text('Feature\n')
    commit_all(root)
    git(root, 'switch', 'main')
    (root / 'main.md').write_text('Main\n')
    commit_all(root)
    git(root, 'switch', 'feature')
    before = store.head(root)
    result = real_merge(root)
    assert 'PREVIOUS-MERGE' in result.stdout + result.stderr
    assert (result.returncode == 0) == (exit_code == 0)
    if exit_code:
        assert store.head(root) == before
    hook.write_text('#!/bin/sh\necho REPLACEMENT\n')
    assert cli(root, 'init', '--force').returncode == 1
    assert previous.read_text() == original
    assert 'REPLACEMENT' in hook.read_text()


@pytest.mark.parametrize('flags', [[], ['--light'], ['--check']])
def test_init_existing_workflow_points_to_update(local_repo, flags):
    ok(local_repo, 'init')
    workflow = local_repo / '.github/workflows/workorder.yml'
    before = workflow.read_bytes()
    output = ok(local_repo, 'init', *flags)
    line = next(line for line in output.splitlines() if 'already present; skipped' in line)
    assert 'workorder init --update-workflow' in line
    assert workflow.read_bytes() == before


def test_show_unimplemented_task_says_to_work_on_branch(local_repo):
    fill_sections(task(local_repo, ships='none', test=f'{PYTHON} test_calc.py'))
    commit_all(local_repo)
    output = ok(local_repo, 'show', '--task', 'TASK-1')
    step = next(line for line in output.splitlines() if line.startswith('next step:'))
    assert 'on a branch' in step
    assert 'Acceptance' in step and 'workorder done --task TASK-1' in step


def test_agent_block_explains_build_and_cache_gitignore(local_repo):
    ok(local_repo, 'init')
    for name in ('AGENTS.md', 'CLAUDE.md'):
        text = (local_repo / name).read_text()
        assert any(all(word in line for word in ('.gitignore', '__pycache__/', '*.egg-info/'))
                   for line in text.splitlines())


def test_import_advice_requires_configuration_on_base(local_repo):
    from workorder.python_imports import import_advice
    (local_repo / 'src').mkdir()
    (local_repo / 'src/calc.py').write_text('value = 1\n')
    advice = import_advice('calc', local_repo, local_repo)
    assert 'pythonpath = ["src"]' in advice
    assert 'base branch' in advice and 'branch-only' in advice
