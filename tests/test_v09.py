"""Round-11 contracts, exercised against disposable repositories without a network."""

import io
import sys

import pytest

from workorder import gate, hooks, store
from test_workorder import PYTHON, cli, git, local_repo, task
from test_v05 import commit_all
from test_v06b import supported
from test_v07b_comparison import setup_tests


def test_hook_stop_blocks_with_exit_2_and_reason_on_stderr_exits_0_when_stop_hook_active(
        local_repo, monkeypatch, capsys):
    task(local_repo, status='DONE')
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    assert hooks.run(local_repo, 'stop') == 2
    output = capsys.readouterr()
    assert 'NOT QUITE' in output.err and 'owes' in output.err
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{"stop_hook_active": true}'))
    assert hooks.run(local_repo, 'stop') == 0
    assert not capsys.readouterr().err


@pytest.mark.parametrize('status', ['IN_PROGRESS', 'DONE'])
def test_changed_tests_select_their_owner_without_task_edits(local_repo, status):
    root = local_repo
    (root / 'app.py').write_text('value = 1\n')
    probe, base = setup_tests(root, 'from app import value\ndef test_one():\n    assert value == 1\n')
    if status == 'DONE':
        supported(root, test=f'{PYTHON} -m pytest -q tests')
        base = commit_all(root)
    (root / 'app.py').write_text('value = 200\n')
    probe.write_text(probe.read_text().replace('== 1', '== 200'))
    result = cli(root, 'check', '--review', 'refuse', '--base', base, '--run-tests')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'lost or changed' in result.stdout and '--allow TASK-1' in result.stdout
    assert f'its tests from {base} fail on this code' in result.stdout


@pytest.mark.parametrize('path', ['tests/data.txt', 'test/helper.py', 'checks/test_other.py',
                                 'checks/other_test.py', 'nested/conftest.py', 'pytest.ini',
                                 'suite/helper.py'])
def test_unowned_test_changes_need_tests_acceptance(local_repo, path):
    root = local_repo
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('[pytest]\naddopts = -q\n' if path == 'pytest.ini' else 'value = 1\n')
    (root / 'pyproject.toml').write_text('[tool.pytest.ini_options]\ntestpaths = ["suite"]\n')
    base = commit_all(root)
    target.write_text(target.read_text().replace('1', '2').replace('-q', '-qq'))
    refused = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert refused.returncode == 1, refused.stdout
    assert path in refused.stdout and '--allow tests' in refused.stdout
    assert cli(root, 'check', '--review', 'refuse', '--base', base, '--allow', 'tests').returncode == 0


def test_unowned_test_additions_pass_without_acceptance(local_repo):
    root = local_repo
    (root / 'tests').mkdir()
    probe = root / 'tests/test_one.py'
    probe.write_text('def test_one():\n    assert True\n')
    base = commit_all(root)
    probe.write_text(probe.read_text() + '\ndef test_two():\n    assert True\n')
    assert cli(root, 'check', '--base', base).returncode == 0


def test_work_on_division_cannot_hide_changed_multiplication_tests(local_repo):
    root = local_repo
    (root / 'calc.py').write_text('def mul(a, b):\n    return a * b\n')
    setup_tests(root, '', f'{PYTHON} -m pytest -q tests/mul')
    (root / 'tests/mul').mkdir()
    probe = root / 'tests/mul/test_mul.py'
    probe.write_text('from calc import mul\ndef test_negative():\n    assert mul(-3, 4) == -12\n')
    (root / 'tests/div').mkdir()
    (root / 'tests/div/test_div.py').write_text('def test_div():\n    assert True\n')
    task(root, 'TASK-2', ships='none', test=f'{PYTHON} -m pytest -q tests/div')
    supported(root, test=f'{PYTHON} -m pytest -q tests/mul')
    base = commit_all(root)
    git(root, 'checkout', '-qb', 'feature')
    (root / 'calc.py').write_text('def mul(a, b):\n    return a + b if a < 0 else a * b\n')
    probe.write_text(probe.read_text().replace('== -12', '== 1'))
    commit_all(root)
    store.receipt(root, 'TASK-2', 'implemented')
    store.receipt(root, 'TASK-2', 'test', [PYTHON, '-m', 'pytest', '-q', 'tests/div'])
    commit_all(root)
    result = cli(root, 'check', '--review', 'refuse', '--base', base, '--run-tests', '--all-tests', '--require-task')
    assert result.returncode == 1, result.stdout + result.stderr
    assert '--allow TASK-1' in result.stdout and 'fail on this code' in result.stdout


def test_any_task_uncounted_receipt_cannot_hide_changed_tests(local_repo):
    root = local_repo
    (root / 'calc.py').write_text('def mul(a, b):\n    return a * b\n')
    probe, _ = setup_tests(root, 'from calc import mul\ndef test_negative():\n    assert mul(-3, 4) == -12\n')
    supported(root, test=f'{PYTHON} -m pytest -q tests')
    base = commit_all(root)
    git(root, 'checkout', '-qb', 'feature')
    (root / 'calc.py').write_text('def mul(a, b):\n    return a + b if a < 0 else a * b\n')
    probe.write_text(probe.read_text().replace('== -12', '== 1'))
    task(root, 'TASK-2', ships='none', test='any')
    commit_all(root)
    assert cli(root, 'receipt', 'test', '--task', 'TASK-2', '--', PYTHON, '-c', 'pass').returncode == 1
    commit_all(root)
    result = cli(root, 'check', '--base', base, '--run-tests', '--all-tests', '--require-task')
    assert result.returncode == 1, result.stdout + result.stderr
    assert '--allow TASK-1' in result.stdout and 'no task records' in result.stdout


@pytest.mark.parametrize('status', ['IN_PROGRESS', 'BLOCKED'])
@pytest.mark.parametrize('missing', [False, True])
def test_unfinished_failing_or_missing_tests_are_notes_in_base_and_hook(local_repo, status, missing):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert False\n')
    if missing:
        probe.unlink()
        base = commit_all(root)
    store.set_task_field(root, 'TASK-1', 'status', status)
    store.add_note(root, 'TASK-1', 'Half done; continue tomorrow')
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'add', '-A')
    for flags in [('--base', base, '--run-tests', '--allow', 'TASK-1'), ('--staged',)]:
        result = cli(root, 'check', *flags)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Note:' in result.stdout and ('failed' in result.stdout or 'no tests ran' in result.stdout)


def test_failed_finished_receipt_can_be_committed_with_reopening(local_repo):
    root = local_repo
    supported(root)
    base = commit_all(root)
    definition = store.record(root, 'TASK-1')
    # A real failed run of the declared command, controlled by an ignored environment file.
    command = gate.declared_test(definition)
    from unittest.mock import patch
    import subprocess
    with patch.object(store, 'run_test', return_value=subprocess.CompletedProcess(command, 1, '', 'failure')):
        store.receipt(root, 'TASK-1', 'test', command)
    git(root, 'add', '-A')
    refusal = cli(root, 'check', '--staged')
    assert refusal.returncode == 1 and 'IN_PROGRESS' in refusal.stdout
    store.set_task_field(root, 'TASK-1', 'status', 'IN_PROGRESS')
    git(root, 'add', '-A')
    assert cli(root, 'check', '--staged').returncode == 0
    assert cli(root, 'check', '--base', base).returncode == 0


@pytest.mark.parametrize('kind', ['any', 'missing_implementation', 'wrong_command', 'old_code'])
def test_require_task_only_accepts_counting_concrete_receipts(local_repo, kind):
    root = local_repo
    task(root, ships='none', test='any' if kind == 'any' else f'{PYTHON} -c pass')
    base = commit_all(root)
    if kind == 'old_code':
        store.receipt(root, 'TASK-1', 'implemented')
    (root / 'app.py').write_text('value = 2\n')
    commit_all(root)
    if kind != 'missing_implementation' and kind != 'old_code':
        store.receipt(root, 'TASK-1', 'implemented')
    command = [PYTHON, '-c', 'pass']
    if kind == 'wrong_command':
        command[-1] = 'print(42)'
        # A previously committed implementation cannot satisfy this change's receipt requirement.
        commit_all(root)
        base = store.head(root)
        (root / 'app.py').write_text('value = 3\n')
        commit_all(root)
    store.receipt(root, 'TASK-1', 'test', command)
    result = cli(root, 'check', '--base', base, '--require-task', '--allow', 'TASK-1')
    assert result.returncode == 1, result.stdout
    assert 'no task records' in result.stdout


@pytest.mark.parametrize('command', ['make test', 'npm test', 'npx jest', 'yarn test', 'pnpm test',
                                   'go test ./...', 'cargo test', 'tox', 'nox', 'sh run.sh',
                                   'bash run.sh', f'{PYTHON} runner.py', 'env npm test',
                                   'sh -c pytest', './run-checks', 'node runner.js'])
def test_wrapper_without_test_paths_is_refused_under_base(local_repo, command):
    root = local_repo
    task(root, ships='none', test=command)
    base = commit_all(root)
    store.add_note(root, 'TASK-1', 'Work in progress')
    result = cli(root, 'check', '--base', base, '--allow', 'TASK-1')
    assert result.returncode == 1, result.stdout
    assert 'set test_paths so changes to its tests are reviewed' in result.stdout


def test_untouched_unfinished_wrapper_does_not_refuse_other_work(local_repo):
    root = local_repo
    task(root, ships='none', test='npm test')
    base = commit_all(root)
    (root / 'app.py').write_text('value = 2\n')
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 0, result.stdout
    assert 'set test_paths' not in result.stdout


def test_production_refactor_is_not_an_inferred_test_change(local_repo):
    """A root-level test must not make src/app.py a test file when tests stay the same."""
    root = local_repo
    (root / 'src').mkdir()
    (root / 'src/__init__.py').write_text('')
    app = root / 'src/app.py'
    app.write_text('def mul(a, b):\n    return a * b\n')
    (root / 'tests').mkdir()
    (root / 'tests/test_app.py').write_text(
        'from src.app import mul\n\ndef test_mul():\n    assert mul(3, 4) == 12\n')
    (root / 'test_probe.py').write_text('def test_probe():\n    assert True\n')
    task(root, ships='none', test=f'{PYTHON} -m pytest -q tests')
    base = commit_all(root)
    app.write_text('def mul(a, b):\n    return b * a\n')
    commit_all(root)
    store.receipt(root, 'TASK-1', 'implemented')
    store.receipt(root, 'TASK-1', 'test', [PYTHON, '-m', 'pytest', '-q', 'tests'])
    result = cli(root, 'check', '--base', base, '--run-tests', '--all-tests', '--require-task')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert 'test files' not in output
    assert 'Traceback' not in output
    assert 'src/app.py' not in output


@pytest.mark.parametrize('command', [f'{PYTHON} -m compileall -q src', 'ruff check src',
                                   'flake8 src', 'mypy src', 'pyright src', 'black --check src'])
def test_non_test_commands_do_not_own_code_folders(local_repo, command):
    root = local_repo
    (root / 'src').mkdir()
    source = root / 'src/app.py'
    source.write_text('value = 1\n')
    task(root, ships='none', test=command)
    base = commit_all(root)
    source.write_text('value = 2\n')
    store.add_note(root, 'TASK-1', 'Compile the source')
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 0, result.stdout
    assert 'test files lost' not in result.stdout


def test_no_task_acceptance_waives_only_task_receipt_requirement(local_repo):
    root = local_repo
    base = store.head(root)
    (root / 'app.py').write_text('value = 2\n')
    accepted = cli(root, 'check', '--base', base, '--require-task', '--allow', 'no-task')
    assert accepted.returncode == 0, accepted.stdout
    task(root, status='DONE')
    assert cli(root, 'check', '--base', base, '--require-task', '--allow', 'no-task').returncode == 1


def test_all_tests_without_base_checks_current_finished_tasks(local_repo):
    supported(local_repo)
    result = cli(local_repo, 'check', '--run-tests', '--all-tests')
    assert result.returncode == 0, result.stdout + result.stderr


def test_nothing_to_ship_and_implemented_code_wording(local_repo):
    root = local_repo
    supported(root)
    commit_all(root)
    for arguments in [('check', 'TASK-1'), ('show', '--task', 'TASK-1')]:
        result = cli(root, *arguments)
        assert 'Evidence: supports the claim (nothing to ship)' in result.stdout
    (root / 'another.py').write_text('value = 2\n')
    commit_all(root)
    result = cli(root, 'check', 'TASK-1')
    assert 'Result: the declared test passed on the implemented code' in result.stdout


def test_successful_ref_comparison_does_not_print_its_output(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'print("REF-ONLY-OUTPUT")\n', f'{PYTHON} tests/test_app.py')
    probe.write_text('print("CURRENT-OUTPUT")\n')
    store.add_note(root, 'TASK-1', 'Refresh output')
    result = cli(root, 'check', '--base', base, '--run-tests', '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'REF-ONLY-OUTPUT' not in result.stdout + result.stderr
