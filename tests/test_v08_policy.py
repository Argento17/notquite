"""0.8 policy regressions, using real local Git and test commands."""

import json
from pathlib import PureWindowsPath

import pytest

from workorder import gate, store
from test_workorder import PYTHON, cli, git, local_repo, task
from test_v05 import commit_all
from test_v06b import supported
from test_v07b_comparison import setup_tests


def touch(root, tid='TASK-1'):
    store.add_note(root, tid, 'Working on this change')


def check(root, base, *flags):
    return cli(root, 'check', '--base', base, '--run-tests', *flags)


@pytest.mark.parametrize('strict', [False, True])
def test_reviewed_intended_change_accepts_ref_failure_for_each_worked_task(local_repo, strict):
    root = local_repo
    (root / 'app.py').write_text('rate = .10\n')
    probe, _ = setup_tests(root, 'from app import rate\ndef test_rate():\n    assert rate == .10\n')
    task(root, 'TASK-2', ships='none', test=f'{PYTHON} -m pytest -q tests')
    base = commit_all(root)
    (root / 'app.py').write_text('rate = .200\n')
    probe.write_text('from app import rate\ndef test_rate():\n    assert rate == .20\n')
    touch(root)
    touch(root, 'TASK-2')
    flags = ['--strict-tests'] if strict else []
    denied = check(root, base, '--allow', 'TASK-1', *flags)
    assert denied.returncode == 1 and '--allow TASK-2' in denied.stdout
    accepted = check(root, base, '--allow', 'TASK-1,TASK-2', *flags)
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert 'fail on this code' in accepted.stdout and '(allowed)' in accepted.stdout
    probe.write_text('from app import rate\ndef test_rate():\n    assert rate == .30\n')
    unfinished = check(root, base, '--allow', 'TASK-1,TASK-2', *flags)
    assert unfinished.returncode == 0
    assert 'Note:' in unfinished.stdout and 'not claimed finished' in unfinished.stdout
    assert ('no test passed' if strict else 'its test failed') in unfinished.stdout


def test_changed_test_lines_need_review_even_when_both_runs_pass(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert 1 == 1\n')
    probe.write_text('def test_one():\n    assert True\n')
    touch(root)
    denied = check(root, base)
    assert denied.returncode == 1 and 'lost or changed 1 lines' in denied.stdout
    assert '--allow TASK-1' in denied.stdout
    assert check(root, base, '--allow', 'TASK-1').returncode == 0


@pytest.mark.parametrize('change', ['deleted', 'skip', 'xfail', 'deselect'])
def test_reviewer_accepts_reduced_pytest_outcomes(local_repo, change):
    root = local_repo
    body = 'def test_one():\n    assert True\n\ndef test_two():\n    assert True\n'
    probe, base = setup_tests(root, body)
    if change == 'deleted':
        probe.write_text(body.split('\ndef test_two')[0])
    elif change == 'skip':
        probe.write_text('import pytest\n' + body.replace('def test_two', '@pytest.mark.skip(reason="retired")\ndef test_two'))
    elif change == 'xfail':
        probe.write_text('import pytest\n' + body.replace('def test_two():\n    assert True', '@pytest.mark.xfail(reason="known")\ndef test_two():\n    assert False'))
    else:
        path = root / 'tasks/TASK-1.md'
        path.write_text(path.read_text().replace('-q tests', '-q tests -k one'))
    touch(root)
    denied = check(root, base)
    assert denied.returncode == 1 and '--allow TASK-1' in denied.stdout
    accepted = check(root, base, '--allow', 'TASK-1')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr
    assert 'fewer tests pass' in accepted.stdout and '(allowed)' in accepted.stdout


def test_reviewed_test_rename_runs_ref_command_on_restored_file(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert True\n', f'{PYTHON} -m pytest -q tests/test_app.py')
    probe.rename(probe.with_name('test_renamed.py'))
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('test_app.py', 'test_renamed.py'))
    result = check(root, base, '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ran 2 declared tests' in result.stdout


@pytest.mark.parametrize('status', ['IN_PROGRESS', 'DONE'])
def test_untouched_tasks_review_shared_test_changes(local_repo, status):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert True\n')
    if status == 'DONE':
        supported(root, test=f'{PYTHON} -m pytest -q tests')
        base = commit_all(root)
    probe.write_text('def test_one():\n    assert False\n')
    result = check(root, base, '--strict-tests')
    assert result.returncode == 1 and '--allow TASK-1' in result.stdout
    assert 'its test' in result.stdout
    if status == 'DONE':
        result = check(root, base, '--all-tests', '--allow', 'TASK-1')
        assert result.returncode == 1 and "regression: TASK-1's test fails" in result.stdout


def test_allow_does_not_disable_other_tasks_all_tests(local_repo):
    root = local_repo
    (root / 'app.py').write_text('rate = .10\n')
    probe, _ = setup_tests(root, 'from app import rate\ndef test_rate():\n    assert rate == .10\n')
    (root / 'old_contract.py').write_text('from app import rate\nassert rate == .10\n')
    supported(root, tid='TASK-2', test=f'{PYTHON} old_contract.py')
    base = commit_all(root)
    (root / 'app.py').write_text('rate = .200\n')
    probe.write_text('from app import rate\ndef test_rate():\n    assert rate == .20\n')
    touch(root)
    result = check(root, base, '--all-tests', '--allow', 'TASK-1,TASK-2')
    assert result.returncode == 1 and "regression: TASK-2's test fails" in result.stdout
    assert not any(line.startswith('TASK-1:') for line in result.stdout.splitlines())


def test_worked_task_reuses_identical_ref_tests_without_test_file_edits(local_repo):
    root = local_repo
    _, base = setup_tests(root, 'def test_one():\n    assert True\n')
    touch(root)
    result = check(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'ran 2 declared tests (1 cached)' in result.stdout


@pytest.mark.parametrize('explicit', [False, True])
def test_missing_named_test_never_falls_back_to_unrelated_discovery(local_repo, explicit):
    root = local_repo
    _, base = setup_tests(root, 'def test_one():\n    assert True\n',
                          f'{PYTHON} -m pytest -q tests/test_tax.py',
                          ['tests/test_tax.py'] if explicit else None)
    (root / 'tests/test_app.py').write_text('def test_one():\n    assert False\n')
    touch(root)
    notes = []
    problems = gate.check(root, base=base, strict_tests=True, notes=notes)
    assert problems and all('--allow tests' in p for p in problems), problems
    assert any('tests/test_tax.py' in note and 'no test files' in note for note in notes)


@pytest.mark.parametrize('path', ['README.md', 'guide.rst', 'notes.txt', 'docs/build.py', 'LICENSE',
                                   'LICENSE-MIT', '.github/workflows/check.yml'])
def test_require_task_excludes_documentation_and_noncode_paths(local_repo, path):
    root = local_repo
    base = store.head(root)
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('documentation\n')
    result = cli(root, 'check', '--base', base, '--require-task')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('metadata', ['note', 'status', 'new_task'])
def test_require_task_needs_new_implementation_or_test_receipt(local_repo, metadata):
    root = local_repo
    task(root, ships='none', test=f'{PYTHON} -c pass')
    base = commit_all(root)
    (root / 'app.py').write_text('value = 2\n')
    if metadata == 'note':
        touch(root)
    elif metadata == 'status':
        path = root / 'tasks/TASK-1.md'
        path.write_text(path.read_text().replace('IN_PROGRESS', 'BLOCKED'))
    else:
        task(root, 'TASK-2', ships='none')
    commit_all(root)
    result = cli(root, 'check', '--base', base, '--require-task')
    assert result.returncode == 1 and 'implemented or test receipt' in result.stdout
    store.receipt(root, 'TASK-1', 'implemented')
    assert cli(root, 'check', '--base', base, '--require-task').returncode == 0


@pytest.mark.parametrize('status', ['DONE', 'CLOSED'])
@pytest.mark.parametrize('section', ['Scope', 'Acceptance'])
def test_finished_task_section_additions_need_acceptance(local_repo, status, section):
    root = local_repo
    path = supported(root, status=status)
    path.write_text(path.read_text() + '\n## Scope\n\nOriginal scope.\n\n## Acceptance\n\n- Original ask.\n')
    base = commit_all(root)
    path.write_text(path.read_text().replace(f'## {section}\n', f'## {section}\n\n- Another requirement.\n'))
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1 and f'{section} lines added' in result.stdout
    assert '--allow TASK-1' in result.stdout
    assert cli(root, 'check', '--base', base, '--allow', 'TASK-1').returncode == 0


def test_snapshot_command_rewrites_windows_paths_and_node_ids():
    root = PureWindowsPath('C:/project')
    copy = PureWindowsPath('C:/temp/copy')
    command = [r'C:\Python\python.exe', '-m', 'pytest', r'C:\project\tests\test_app.py::test_one',
               '--config-file=C:/project/pytest.ini', r'C:\project-other\test.py']
    result = gate._snapshot_command(command, root, copy)
    assert result == [r'C:\Python\python.exe', '-m', 'pytest', r'C:\temp\copy\tests\test_app.py::test_one',
                      r'--config-file=C:\temp\copy\pytest.ini', r'C:\project-other\test.py']


@pytest.mark.parametrize('reason', ['new', 'any', 'copy_error'])
def test_skipped_ref_comparison_is_explained(local_repo, monkeypatch, reason):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert True\n')
    if reason == 'new':
        base = git(root, 'rev-parse', base + '^')
    elif reason == 'any':
        path = root / 'tasks/TASK-1.md'
        path.write_text(path.read_text().replace(json.dumps(f'{PYTHON} -m pytest -q tests'), '"any"'))
    else:
        def fail(*args, **kwargs):
            raise OSError('temporary copy unavailable')
        monkeypatch.setattr(gate, '_test_snapshot', fail)
    probe.write_text(probe.read_text() + '\n# new line\n')
    notes = []
    gate.test_change_problems(root, base, store.record(root, 'TASK-1'), notes=notes)
    assert any('REF comparison skipped' in note for note in notes), notes


@pytest.mark.parametrize('kind', ['skip', 'config'])
def test_additive_skip_and_config_need_review_without_running_tests(local_repo, kind):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert True\n')
    touch(root)
    if kind == 'skip':
        probe.write_text(probe.read_text() + '\nimport pytest\n@pytest.mark.skip(reason="later")\ndef test_extra():\n    assert True\n')
    else:
        (root / 'conftest.py').write_text('# collection policy\n')
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1 and '--allow TASK-1' in result.stdout
    assert cli(root, 'check', '--base', base, '--allow', 'TASK-1').returncode == 0


def test_done_reports_missing_ref_instead_of_silently_skipping(local_repo):
    from test_v07b_commands import filled_task
    filled_task(local_repo)
    result = cli(local_repo, 'done', '--task', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'REF comparison skipped' in result.stdout and 'not available locally' in result.stdout


def test_staged_finished_scope_addition_is_checked_from_index(local_repo):
    root = local_repo
    path = supported(root)
    path.write_text(path.read_text() + '\n## Scope\n\nOriginal scope.\n')
    base = commit_all(root)
    original = path.read_bytes()
    path.write_bytes(original + b'\nExtra requirement.\n')
    git(root, 'add', 'tasks/TASK-1.md')
    path.write_bytes(original)
    result = cli(root, 'check', '--base', base, '--staged')
    assert result.returncode == 1 and 'Scope lines added' in result.stdout
    assert '--allow TASK-1' in result.stdout
    assert cli(root, 'check', '--base', base, '--staged', '--allow', 'TASK-1').returncode == 0


@pytest.mark.parametrize('option', ['--rootdir', '--confcutdir'])
def test_pytest_option_directory_does_not_widen_missing_named_test(local_repo, option):
    root = local_repo
    _, base = setup_tests(root, 'def test_one():\n    assert True\n',
                          f'{PYTHON} -m pytest {option} . -q tests/test_missing.py')
    (root / 'tests/test_app.py').write_text('def test_one():\n    assert False\n')
    touch(root)
    notes = []
    problems = gate.check(root, base=base, strict_tests=True, notes=notes)
    assert problems and all('--allow tests' in p for p in problems), problems
    assert any('test_missing.py' in note and 'no test files' in note for note in notes)


def test_staged_contract_requirement_is_checked_from_index(local_repo):
    root = local_repo
    relative = '.workorder/contract.json'
    path = root / relative
    path.parent.mkdir(exist_ok=True)
    before = json.dumps({'task': 'TASK-1', 'criteria': [
        {'id': 'C1', 'ask': 'original requirement', 'critical': True, 'requires': 'REAL', 'evidence': []}
    ]}).encode()
    path.write_bytes(before)
    task(root, ships='none', contract=relative)
    base = commit_all(root)
    path.write_bytes(before.replace(b'original requirement', b'changed requirement'))
    git(root, 'add', relative)
    path.write_bytes(before)
    result = cli(root, 'check', '--base', base, '--staged')
    assert result.returncode == 1 and f'contract {relative} edited' in result.stdout
    assert cli(root, 'check', '--base', base, '--staged', '--allow', 'TASK-1').returncode == 0
