"""Second-pass 0.11 regressions; all repositories and remotes are local."""

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from workorder import bootstrap, gate, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections


@pytest.mark.parametrize('action', ['done', 'receipt', 'check'])
def test_explicit_evidence_runs_fresh_after_environment_change(local_repo, monkeypatch, action):
    root = local_repo
    command = store.join_command([PYTHON, '-c', 'import os; assert not os.environ.get("WO_FAIL_NOW")'])
    fill_sections(task(root, ships='none', test=command))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    monkeypatch.setenv('WO_FAIL_NOW', '1')
    arguments = {'done': ['done', '--task', 'TASK-1', '--no-code-change'],
                 'receipt': ['receipt', 'test', '--task', 'TASK-1'],
                 'check': ['check', '--run-tests', '--all-tests']}[action]
    result = cli(root, *arguments)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'AssertionError' in result.stdout + result.stderr
    if action == 'receipt':
        assert store.entries(root, 'TASK-1')[-1]['exit_code'] == 1
    elif action == 'done':
        assert len(store.entries(root, 'TASK-1')) == 2


def test_implicit_check_reuse_names_original_time(local_repo):
    root = local_repo
    command = store.join_command([PYTHON, '-c', 'print("fresh evidence")'])
    fill_sections(task(root, ships='none', test=command))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    git(root, 'add', 'tasks', '.workorder')
    result = cli(root, 'check', '--staged')
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r'\(reused from \d{2}:\d{2} UTC\)', result.stdout), result.stdout


def parallel_pull_requests(root, tmp_path):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    for n in (1, 2):
        (root / f'part{n}.py').write_text(f'value = {n}\n')
        (root / f'test_part{n}.py').write_text(f'from part{n} import value\ndef test_value():\n    assert value > 0\n')
        fill_sections(task(root, f'TASK-{n}', ships='none', test=f'{PYTHON} -m pytest -q test_part{n}.py'))
    base = commit_all(root)
    branches = []
    for n in (1, 2):
        clone = tmp_path / f'author{n}'
        git(tmp_path, 'clone', '-q', str(root), str(clone))
        git(clone, 'config', 'user.name', f'author{n}')
        git(clone, 'config', 'user.email', f'author{n}@example.invalid')
        git(clone, 'checkout', '-qb', f'pr{n}')
        (clone / f'part{n}.py').write_text(f'value = {n + 10}\n')
        with (clone / f'test_part{n}.py').open('a') as stream:
            stream.write(f'\ndef test_new_value():\n    assert value == {n + 10}\n')
        commit_all(clone)
        ok(clone, 'done', '--task', f'TASK-{n}')
        commit_all(clone)
        git(root, 'fetch', '-q', str(clone), f'pr{n}:pr{n}')
        branches.append(clone)
    return base, branches


def pr_check(root):
    return cli(root, 'check', '--base', 'main', '--run-tests', '--all-tests', '--require-task')


def test_two_parallel_prs_checked_on_merge_commits_then_squashed(local_repo, tmp_path):
    root = local_repo
    _, clones = parallel_pull_requests(root, tmp_path)
    for n in (1, 2):
        git(root, 'checkout', '-q', '--detach', 'main')
        git(root, 'merge', '-q', '--no-ff', '-m', 'GitHub pull request merge', f'pr{n}')
        checked = pr_check(root)
        assert checked.returncode == 0, checked.stdout + checked.stderr
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '-q', '--squash', 'pr1')
    commit_all(root)
    # GitHub checks B against the new main before its author merges main.
    git(root, 'checkout', '-q', '--detach', 'main')
    git(root, 'merge', '-q', '--no-ff', '-m', 'GitHub B merge after A squash', 'pr2')
    checked = pr_check(root)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    # The author follows the agent block, merges main, and retains the same receipts.
    second = clones[1]
    git(second, 'fetch', '-q', 'origin', 'main')
    git(second, 'merge', '-q', '--no-ff', '-m', 'Merge main into B', 'origin/main')
    git(root, 'fetch', '-q', str(second), 'pr2:pr2')
    git(root, 'checkout', '-q', '--detach', 'main')
    git(root, 'merge', '-q', '--no-ff', '-m', 'GitHub updated B merge', 'pr2')
    checked = pr_check(root)
    assert checked.returncode == 0, checked.stdout + checked.stderr
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '-q', '--squash', 'pr2')
    commit_all(root)
    ok(root, 'check', '--run-tests', '--all-tests')


def test_parallel_pr_later_branch_edit_needs_new_receipt(local_repo, tmp_path):
    root = local_repo
    _, clones = parallel_pull_requests(root, tmp_path)
    git(root, 'merge', '-q', '--squash', 'pr1')
    commit_all(root)
    second = clones[1]
    (second / 'part2.py').write_text('value = 99\n')
    commit_all(second)
    git(root, 'fetch', '-q', str(second), 'pr2:pr2')
    git(root, 'checkout', '-q', '--detach', 'main')
    git(root, 'merge', '-q', '--no-ff', '-m', 'GitHub B merge', 'pr2')
    checked = pr_check(root)
    assert checked.returncode == 1
    assert 'no task records it' in checked.stdout
    assert 'main moved since TASK-2 was tested: merge it and run workorder renew --task TASK-2' in checked.stdout


def test_shared_test_edit_has_one_problem_and_any_owner_can_accept(local_repo):
    root = local_repo
    (root / 'tests').mkdir()
    probe = root / 'tests/test_f7.py'
    probe.write_text('def test_value():\n    assert 7 > 0\n')
    command = [PYTHON, '-m', 'pytest', '-q', 'tests']
    for n in range(1, 41):
        task(root, f'TASK-{n}', ships='none', test=store.join_command(command))
    commit_all(root)
    for n in range(1, 41):
        receipt = store.receipt(root, f'TASK-{n}', 'implemented')
        store.append(root, f'TASK-{n}', dict(kind='test', commit=receipt['commit'], code=receipt['code'],
                                           command=command, exit_code=0, valid=True))
        store.set_task_field(root, f'TASK-{n}', 'status', 'DONE')
    base = commit_all(root)
    probe.write_text('def test_value():\n    assert 7 >= 0\n')
    result = cli(root, 'check', '--base', base)
    lines = [line for line in result.stdout.splitlines() if 'lost or changed' in line]
    assert len(lines) == 1, result.stdout
    assert all(f'TASK-{n}' in lines[0] for n in range(1, 41))
    for accepted in ('tests', 'TASK-7', 'TASK-40'):
        accepted_result = cli(root, 'check', '--base', base, '--allow', accepted)
        assert accepted_result.returncode == 0, accepted_result.stdout + accepted_result.stderr


@pytest.mark.parametrize('path', ['tests/test_value.py', 'helpers/assertions.py'])
def test_test_only_change_does_not_need_task_receipt(local_repo, path):
    root = local_repo
    probe = root / path
    probe.parent.mkdir()
    probe.write_text('assert 1 == 1\n')
    definition = task(root, test=f'{PYTHON} {path}')
    if path.startswith('helpers'):
        definition.write_text(definition.read_text().replace('---\n\n', f'test_paths: ["{path}"]\n---\n\n'))
    base = commit_all(root)
    probe.write_text('assert 2 == 2\n')
    store.add_note(root, 'TASK-1', 'Improve test coverage')
    result = cli(root, 'check', '--base', base, '--require-task', '--allow', 'tests')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('status', ['DONE', 'CLOSED'])
def test_renewing_finished_task_does_not_cover_unrelated_code(local_repo, status):
    root = local_repo
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, '-c', 'pass'])))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    store.set_task_field(root, 'TASK-1', 'status', status)
    base = commit_all(root)
    (root / 'unrelated.py').write_text('value = 99\n')
    commit_all(root)
    ok(root, 'renew', '--task', 'TASK-1')
    result = cli(root, 'check', '--base', base, '--require-task')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'no task records it' in result.stdout
    ok(root, 'check', '--base', base, '--require-task', '--allow', 'TASK-1')


@pytest.mark.parametrize('action', ['done', 'receipt', 'check'])
@pytest.mark.parametrize('failure', ['assertion', 'collection'])
def test_pytest_failure_summary_overrides_forced_zero_exit(local_repo, action, failure):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'app.py').write_text('value = 1\n')
    (root / 'test_app.py').write_text('from app import value\ndef test_value():\n    assert value == 1\ndef test_other():\n    assert True\n')
    fill_sections(task(root, ships='none', test=f'{PYTHON} -m pytest -q'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    commit_all(root)
    (root / 'app.py').write_text(
        'import atexit, os, sys\n'
        'def hide_exit():\n    sys.stdout.flush()\n    sys.stderr.flush()\n    os._exit(0)\n'
        'atexit.register(hide_exit)\n' + ('value = 2\n' if failure == 'assertion' else 'raise RuntimeError("broken import")\n'))
    commit_all(root)
    args = {'done': ['done', '--task', 'TASK-1'],
            'receipt': ['receipt', 'test', '--task', 'TASK-1'],
            'check': ['check', '--run-tests', '--all-tests']}[action]
    result = cli(root, *args)
    assert result.returncode == 1, result.stdout + result.stderr
    if action == 'receipt':
        assert not store.entries(root, 'TASK-1')[-1]['valid']
    elif action == 'done':
        assert len(store.entries(root, 'TASK-1')) == 2


@pytest.mark.parametrize('path', ['app/testing.py', 'app/math_testing.py', 'app/testing/helper.py',
                                 'data/expected.json', 'data/expected_output.txt', 'data/golden.json',
                                 'data/snapshot.json', 'app/__snapshots__/value.json', 'data/golden/value.json'])
def test_more_test_material_is_reviewed(local_repo, path):
    target = local_repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('original expectation\n')
    base = commit_all(local_repo)
    target.write_text('expectation changed to match a bug\n')
    result = cli(local_repo, 'check', '--base', base, '--run-tests', '--all-tests', '--require-task')
    assert 'lost or changed' in result.stdout and path in result.stdout, result.stdout
    assert 'no task records it' not in result.stdout
    ok(local_repo, 'check', '--base', base, '--require-task', '--allow', 'tests')


def test_done_refusal_on_done_task_names_failure_and_next_action(local_repo, monkeypatch):
    root = local_repo
    command = store.join_command([PYTHON, '-c', 'import os; assert not os.environ.get("WO_FAIL_NOW")'])
    fill_sections(task(root, ships='none', test=command))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    monkeypatch.setenv('WO_FAIL_NOW', '1')
    result = cli(root, 'done', '--task', 'TASK-1', '--no-code-change')
    assert result.returncode == 1
    assert 'TASK-1 stays DONE; this change was refused:' in result.stdout
    assert 'fix the code or the test, then run workorder done again' in result.stdout
    assert 'workorder receipt test' not in result.stdout


def test_refused_current_test_is_not_counted_as_supported(local_repo, monkeypatch):
    root = local_repo
    command = store.join_command([PYTHON, '-c', 'import os; assert not os.environ.get("WO_FAIL_NOW")'])
    fill_sections(task(root, ships='none', test=command))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    monkeypatch.setenv('WO_FAIL_NOW', '1')
    result = cli(root, 'check', '--run-tests', '--all-tests')
    assert result.returncode == 1
    assert 'supported claim' not in result.stdout
    assert 'Failure:' in result.stdout


def test_current_and_ref_failure_output_prints_once(local_repo):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'app.py').write_text('value = 1\n')
    (root / 'test_app.py').write_text('from app import value\ndef test_value():\n    assert value == 1\n')
    fill_sections(task(root, ships='none', test=f'{PYTHON} -m pytest -q test_app.py'))
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    base = commit_all(root)
    (root / 'app.py').write_text('value = 2\n')
    store.add_note(root, 'TASK-1', 'This change fails')
    result = cli(root, 'check', '--base', base, '--run-tests', '--all-tests')
    assert result.returncode == 1
    assert result.stdout.count('short test summary info') == 1, result.stdout
    assert 'Failure:' in result.stdout
    assert 'supported claim' not in result.stdout


def test_unfinished_wrapper_is_note_in_staged_check(local_repo):
    root = local_repo
    (root / 'runner.py').write_text('assert True\n')
    fill_sections(task(root, ships='none', test=f'{PYTHON} runner.py'))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    store.add_note(root, 'TASK-1', 'Still implementing')
    git(root, 'add', 'tasks')
    result = cli(root, 'check', '--staged')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Note: TASK-1 runs a wrapper script' in result.stdout
    assert cli(root, 'check', '--base', base).returncode == 1


@pytest.mark.parametrize('path', ['.gitignore', 'docs/guide.md', 'notes.txt', 'LICENSE', 'assets/logo.png'])
def test_done_requires_code_beyond_non_code_changes(local_repo, path):
    root = local_repo
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, '-c', 'pass'])))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('updated material\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'no code changed' in result.stdout
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')


def test_done_clears_next_field(local_repo):
    fill_sections(task(local_repo, ships='none', test=store.join_command([PYTHON, '-c', 'pass'])))
    store.set_task_field(local_repo, 'TASK-1', 'next', 'Implement sub')
    commit_all(local_repo)
    ok(local_repo, 'done', '--task', 'TASK-1', '--no-code-change')
    assert not store.record(local_repo, 'TASK-1').get('next')


def test_new_accepts_script_explicitly_named_as_its_own_test(local_repo):
    (local_repo / 'check.js').write_text('if (2 + 2 !== 4) throw Error("bad sum");\n')
    ok(local_repo, 'new', '--title', 'Check sum', '--owner', 'dev', '--test', 'node check.js', '--test-paths', 'check.js')
    record = store.record(local_repo, 'TASK-1')
    assert record['test_paths'] == ['check.js']
    assert gate.wrapper_note(record) is None


def test_staged_hook_reviews_only_tasks_touched_by_commit(local_repo):
    (local_repo / 'test_unowned.py').write_text('assert True\n')
    base = commit_all(local_repo)
    git(local_repo, 'update-ref', 'refs/remotes/origin/main', base)
    (local_repo / 'test_unowned.py').write_text('assert False\n')
    git(local_repo, 'add', 'test_unowned.py')
    result = cli(local_repo, 'check', '--staged')
    assert result.returncode == 0 and not result.stdout


@pytest.mark.parametrize('snapshot_kind', ['working', 'staged', 'ref', 'write'])
def test_extended_absolute_internal_symlink_never_writes_checkout(local_repo, tmp_path, monkeypatch, snapshot_kind):
    root = local_repo
    (root / 'shared.txt').write_text('keep checkout unchanged\n')
    link = root / 'alias.txt'
    try:
        link.symlink_to(root / 'shared.txt')
    except OSError as error:
        pytest.skip(f'creating the source symlink needs platform permission: {error}')
    base = commit_all(root)
    original = os.readlink
    extended = '\\\\?\\' + str(root / 'shared.txt')

    def windows_readlink(path, *args, **kwargs):
        value = original(path, *args, **kwargs)
        if Path(path).name == 'alias.txt':
            return extended
        return value

    monkeypatch.setattr(os, 'readlink', windows_readlink)
    destination = tmp_path / 'snapshot'
    if snapshot_kind == 'write':
        destination.mkdir()
        (destination / 'shared.txt').write_text('copy\n')
        gate._snapshot_write(destination, 'alias.txt', os.fsencode(extended), 0o120000, root)
    elif snapshot_kind == 'ref':
        gate._ref_test_snapshot(root, destination, base)
    elif snapshot_kind == 'staged':
        # Git stores the link text too; exercise _snapshot_write's extended path.
        read_bytes = store.read_bytes
        monkeypatch.setattr(store, 'read_bytes', lambda r, p, staged=False: os.fsencode(extended)
                            if p == 'alias.txt' else read_bytes(r, p, staged))
        gate._test_snapshot(root, destination, staged=True)
    else:
        gate._test_snapshot(root, destination)
    # Inspect the OS link, bypassing the simulated readlink result.
    target = original(destination / 'alias.txt')
    assert not target.startswith('\\\\?\\')
    (destination / 'alias.txt').write_text('written by test\n')
    assert (root / 'shared.txt').read_text() == 'keep checkout unchanged\n'
    assert (destination / 'shared.txt').read_text() == 'written by test\n'


@pytest.mark.parametrize('snapshot_kind', ['working', 'staged', 'ref'])
def test_ignored_dependency_link_failure_copies_and_says_so_once(local_repo, tmp_path, monkeypatch, capsys, snapshot_kind):
    root = local_repo
    (root / '.gitignore').write_text('node_modules/\n.venv/\n')
    for folder in ('node_modules', '.venv'):
        (root / folder).mkdir()
        (root / folder / 'needed.txt').write_text('dependency\n')
    base = commit_all(root)
    def denied(*args, **kwargs):
        raise OSError('symlink privilege is not held')
    monkeypatch.setattr(Path, 'symlink_to', denied)
    destination = tmp_path / 'snapshot'
    if snapshot_kind == 'ref':
        gate._ref_test_snapshot(root, destination, base)
    else:
        gate._test_snapshot(root, destination, staged=snapshot_kind == 'staged')
    for folder in ('node_modules', '.venv'):
        assert not (destination / folder).is_symlink()
        assert (destination / folder / 'needed.txt').read_text() == 'dependency\n'
    assert capsys.readouterr().out.count('copying ignored dependencies') == 1


def test_windows_exe_pytest_command_is_not_a_wrapper():
    command = [r'C:\Program Files\Python313\python.exe', '-S', '-m', 'pytest', '-q']
    record = {'id': 'TASK-1', 'test': store.join_command(command)}
    assert gate.declared_test(record) == command
    assert gate.wrapper_note(record) is None


def test_windows_extended_drive_target_rebases_case_and_dot_segments(tmp_path):
    from pathlib import PureWindowsPath
    destination = tmp_path / 'snapshot'
    destination.mkdir()
    (destination / 'shared.txt').write_text('copy\n')
    try:
        gate._snapshot_write(destination, 'alias.txt', b'\\\\?\\c:\\REPO\\nested\\..\\shared.txt',
                             0o120000, PureWindowsPath('C:/Repo'))
    except OSError as error:
        pytest.skip(f'creating a symlink needs platform permission: {error}')
    assert (destination / 'alias.txt').read_text() == 'copy\n'


@pytest.mark.parametrize('change,reason', [
    ('edit', 'lost or changed'), ('remove', 'lost or changed'),
    ('skip', 'new skip or xfail behavior'), ('xfail', 'new skip or xfail behavior'),
    ('select', 'pytest selection changed'), ('config', 'pytest configuration changed'),
    ('fewer', 'fewer tests pass'), ('base_failing', 'so any change needs review'),
    ('ref_failure', 'its tests from'),
])
def test_test_review_refuses_by_default_and_warns_on_request(local_repo, change, reason, monkeypatch):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'app.py').write_text('value = 1\n')
    (root / 'tests').mkdir()
    body = ('import pytest\nfrom app import value\n\ndef test_value():\n    assert value == 1\n'
            '\ndef test_other():\n    assert 1 == 1\n')
    probe = root / 'tests/test_app.py'
    probe.write_text(body.replace('value == 1', 'value == 2') if change == 'base_failing' else body)
    definition = fill_sections(task(root, ships='none', test=f'{PYTHON} -m pytest -q tests'))
    base = commit_all(root)
    if change == 'edit':
        probe.write_text(body.replace('value == 1', 'value > 0'))
    elif change in {'remove', 'fewer'}:
        probe.write_text(body.split('\ndef test_other():')[0])
    elif change == 'ref_failure':
        (root / 'app.py').write_text('value = 2\n')
        probe.write_text(body.replace('value == 1', 'value == 2'))
    elif change in {'skip', 'xfail'}:
        option = 'run=False, ' if change == 'xfail' else ''
        probe.write_text(body.replace('def test_value():', f'@pytest.mark.{change}({option}reason="later")\ndef test_value():'))
    elif change == 'select':
        definition.write_text(definition.read_text().replace(' -q tests', ' -q tests -k test_other'))
    elif change == 'config':
        (root / 'pytest.ini').write_text('[pytest]\naddopts = -k test_other\n')
    else:
        probe.write_text(probe.read_text().replace('    assert value == 2', '    return\n    assert value == 2'))
    # Claim only in the base-failing case to exercise its stricter REF comparison.
    if change == 'base_failing':
        commit_all(root)
        ok(root, 'done', '--task', 'TASK-1', '--needs-review')
        store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    args = ['check', '--base', base, '--run-tests']
    result = cli(root, *args, '--review', 'warn')
    assert result.returncode == 0, result.stdout + result.stderr
    assert any(line.startswith('Warning:') and reason in line for line in result.stdout.splitlines()), result.stdout
    assert re.search(r'Review: \d+ test changes need a reviewer\x27s look', result.stdout)
    if change in {'skip', 'xfail'}:
        assert f'more tests are {"skipped" if change == "skip" else "xfailed"}' in result.stdout
    strict = cli(root, *args)
    assert strict.returncode == 1 and reason in strict.stdout
    accepted = cli(root, *args, '--allow', 'TASK-1')
    assert accepted.returncode == 0 and 'Warning:' not in accepted.stdout
    assert 'Review:' not in accepted.stdout
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    annotated = cli(root, *args, '--review', 'warn')
    assert '::warning file=' in annotated.stdout and ',line=' in annotated.stdout
    assert annotated.returncode == 0


@pytest.mark.parametrize('staged', [False, True])
def test_opt_in_file_warning_has_github_location_and_allow_silences(local_repo, monkeypatch, staged):
    probe = local_repo / 'test_unowned.py'
    probe.write_text('def test_value():\n    assert True\n')
    base = commit_all(local_repo)
    probe.write_text('def test_value():\n    assert 1 == 1\n')
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    if staged:
        git(local_repo, 'add', 'test_unowned.py')
    flags = ['--staged'] if staged else []
    result = cli(local_repo, 'check', '--base', base, '--review', 'warn', *flags)
    assert result.returncode == 0, result.stdout
    assert result.stdout.count('Warning:') == 1
    assert 'Review: 1 test changes need a reviewer\'s look' in result.stdout
    assert '::warning file=test_unowned.py,line=2::tests:' in result.stdout
    accepted = cli(local_repo, 'check', '--base', base, '--allow', 'tests', *flags)
    assert 'Warning:' not in accepted.stdout and '::warning' not in accepted.stdout


@pytest.mark.parametrize('mode', ['warn', 'refuse'])
def test_review_mode_never_accepts_false_finished_claim(local_repo, mode):
    task(local_repo, status='DONE', ships='none', test=f'{PYTHON} -c pass')
    base = commit_all(local_repo)
    store.add_note(local_repo, 'TASK-1', 'No supporting receipts')
    result = cli(local_repo, 'check', '--base', base, '--review', mode, '--allow', 'TASK-1,tests,no-task')
    assert result.returncode == 1 and 'owes: implementation' in result.stdout


def test_workflow_documents_default_refusal_and_optional_warnings(local_repo):
    bootstrap.initialize(local_repo)
    path = local_repo / '.github/workflows/workorder.yml'
    text = path.read_text()
    assert 'refused unless a reviewer other than the author adds the label' in text and '--review warn' in text
    command = next(line for line in text.splitlines() if line.strip().startswith('workorder check --base'))
    assert '--review refuse' not in command
    path.write_text('old workflow\n')
    bootstrap.initialize(local_repo, update_workflow=True)
    assert '--review warn' in path.read_text()


@pytest.mark.parametrize('view', ['check', 'show'])
def test_failure_views_suggest_fixing_before_done(local_repo, view):
    fill_sections(task(local_repo, ships='none', test=store.join_command([PYTHON, '-c', 'raise SystemExit(1)'])))
    commit_all(local_repo)
    store.receipt(local_repo, 'TASK-1', 'implemented')
    store.receipt(local_repo, 'TASK-1', 'test', [PYTHON, '-c', 'raise SystemExit(1)'])
    store.set_task_field(local_repo, 'TASK-1', 'status', 'DONE')
    result = cli(local_repo, view, '--task', 'TASK-1')
    next_lines = [line for line in result.stdout.splitlines() if 'Next:' in line or 'next step:' in line]
    assert any('fix the code or the test, then run workorder done again' in line for line in next_lines), result.stdout


@pytest.mark.parametrize('material', ['helper', 'expected'])
def test_generated_pr_command_flags_hidden_test_material_bug(local_repo, material):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'app').mkdir()
    (root / 'app/__init__.py').write_text('')
    (root / 'app/calc.py').write_text('def mul(a, b):\n    return a * b\n')
    (root / 'tests').mkdir()
    if material == 'helper':
        target = root / 'app/testing.py'
        target.write_text('def check(value):\n    assert value == 12\n')
        body = 'from app.testing import check\nfrom app.calc import mul\ndef test_mul():\n    check(mul(3, 4))\n'
    else:
        (root / 'data').mkdir()
        target = root / 'data/expected.json'
        target.write_text('12\n')
        body = ('import json\nfrom pathlib import Path\nfrom app.calc import mul\ndef test_mul():\n'
                '    assert mul(3, 4) == json.loads(Path("data/expected.json").read_text())\n')
    (root / 'tests/test_mul.py').write_text(body)
    fill_sections(task(root, ships='none', test=f'{PYTHON} -m pytest -q tests/test_mul.py'))
    bootstrap.initialize(root)
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    (root / 'app/calc.py').write_text('def mul(a, b):\n    return a + b\n')
    target.write_text('def check(value):\n    pass\n' if material == 'helper' else '7\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1, result.stdout
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    commit_all(root)
    # These are the check flags written by init for the pull request.
    args = ['check', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task']
    default = cli(root, *args)
    assert default.returncode == 1, default.stdout + default.stderr
    assert 'tests: its test files lost or changed' in default.stdout
    assert target.relative_to(root).as_posix() in default.stdout
    strict = cli(root, *args)
    assert strict.returncode == 1 and 'lost or changed' in strict.stdout


def test_dependency_copy_fallback_note_is_once_per_check(local_repo, tmp_path, monkeypatch, capsys):
    (local_repo / '.gitignore').write_text('vendor/\n')
    (local_repo / 'vendor').mkdir()
    (local_repo / 'vendor/file.txt').write_text('dependency\n')
    base = commit_all(local_repo)
    def denied(*args, **kwargs):
        raise OSError('no symlink permission')
    monkeypatch.setattr(Path, 'symlink_to', denied)
    with store.test_output_session():
        gate._test_snapshot(local_repo, tmp_path / 'current')
        gate._ref_test_snapshot(local_repo, tmp_path / 'ref', base)
    assert capsys.readouterr().out.count('copying ignored dependencies') == 1


def test_windows_relative_links_keep_their_own_parent(monkeypatch):
    from pathlib import PureWindowsPath
    from types import SimpleNamespace
    class WindowsPathForTest(PureWindowsPath):
        def resolve(self):
            return self if self.is_absolute() else PureWindowsPath('C:/Repo') / self
    # Simulate Windows resolution without depending on the host OS or link rights.
    monkeypatch.setattr(gate, 'Path', WindowsPathForTest)
    monkeypatch.setattr(gate, 'os', SimpleNamespace(name='nt'))
    assert gate._internal_link_relative('sibling.txt', PureWindowsPath('C:/Repo')) is None
