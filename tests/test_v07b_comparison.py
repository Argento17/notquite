"""Compare changed tests with their base versions using disposable local repositories."""

import json

import pytest

from workorder import gate, store
from test_workorder import PYTHON, cli, git, local_repo, task
from test_v05 import commit_all
from test_v06b import supported


def setup_tests(root, body, command=None, paths=None):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir(exist_ok=True)
    probe = root / 'tests/test_app.py'
    probe.write_text(body)
    path = task(root, ships='none', test=command or f'{PYTHON} -m pytest -q tests')
    if paths is not None:
        path.write_text(path.read_text().replace('---\n\n', f'test_paths: {json.dumps(paths)}\n---\n\n'))
    return probe, commit_all(root, 'Base test definition')


def checked(root, base, *flags):
    # Exercise metadata-selected unfinished tasks as well as selection by changed tests.
    for tid, record in gate.records(root).items():
        if record['status'] not in gate.FINISHED:
            store.add_note(root, tid, 'Working on these tests')
            if '--staged' in flags:
                git(root, 'add', f'tasks/{tid}.md')
    return cli(root, 'check', '--review', 'refuse', '--base', base, '--run-tests', *flags)


@pytest.mark.parametrize('allowed', [False, True])
def test_base_tests_run_on_dirty_current_code_even_for_unfinished_tasks(local_repo, allowed):
    root = local_repo
    (root / 'app.py').write_text('value = 1\n')
    probe, base = setup_tests(root, 'from app import value\ndef test_value():\n    assert value == 1\n')
    (root / 'app.py').write_text('value = 2\n')
    probe.write_text('from app import value\ndef test_value():\n    assert value == 2\n')
    head, status = store.head(root), git(root, 'status', '--porcelain')
    result = checked(root, base, *(['--allow', 'TASK-1'] if allowed else []))
    assert result.returncode == (0 if allowed else 1)
    assert f'TASK-1: its tests from {base} fail on this code' in result.stdout
    assert store.head(root) == head
    assert git(root, 'status', '--porcelain', '--', 'app.py', 'tests') == status
    assert 'assert value == 2' in probe.read_text()


@pytest.mark.parametrize('change', ['rewrite', 'add', 'add_file'])
def test_passing_additions_are_free_but_edits_require_acceptance(local_repo, change):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert int(1) == 1\n')
    if change == 'rewrite':
        probe.write_text('def test_one():\n    assert int(2) == 2\n')
    elif change == 'add':
        probe.write_text(probe.read_text() + '\ndef test_two():\n    assert int(1) == 1\n')
    else:
        (probe.parent / 'test_added.py').write_text('def test_added():\n    assert int(1) == 1\n')
    result = checked(root, base)
    assert result.returncode == (1 if change == 'rewrite' else 0), result.stdout + result.stderr
    if change == 'rewrite':
        assert 'workorder-allow:TASK-1' in result.stdout
        assert checked(root, base, '--allow', 'TASK-1').returncode == 0
    assert 'no declared test ran' not in result.stdout
    assert ('1 passed' if change == 'rewrite' else '2 passed') in result.stdout


@pytest.mark.parametrize('allowed', [False, True])
def test_fewer_passes_require_acceptance(local_repo, allowed):
    root = local_repo
    probe, base = setup_tests(root, ''.join(f'def test_{i}():\n    assert 1 == 1\n' for i in range(5)))
    probe.write_text(''.join(f'def test_{i}():\n    assert 1 == 1\n' for i in range(3)))
    result = checked(root, base, *(['--allow', 'TASK-1'] if allowed else []))
    assert result.returncode == (0 if allowed else 1)
    assert f'TASK-1: fewer tests pass than at {base} (5 -> 3)' in result.stdout


@pytest.mark.parametrize('outcome,body', [
    ('skipped', 'import pytest\n@pytest.mark.skip(reason="later")\ndef test_extra():\n    assert 1 == 1\n'),
    ('xfailed', 'import pytest\n@pytest.mark.xfail(reason="later")\ndef test_extra():\n    assert False\n'),
])
def test_new_nonpassing_pytest_counts_can_be_accepted(local_repo, outcome, body):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert 1 == 1\n')
    probe.write_text(probe.read_text() + body)
    result = checked(root, base, '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'more tests are {outcome} than at {base} (0 -> 1)' in result.stdout


def test_new_deselected_count_can_be_accepted(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert 1 == 1\n')
    probe.write_text(probe.read_text() + '\ndef test_extra():\n    assert 1 == 1\n')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('-q tests', '-q tests -k one'))
    result = checked(root, base, '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'more tests are deselected than at {base} (0 -> 1)' in result.stdout


@pytest.mark.parametrize('addition', [
    'import pytest\n@pytest.mark.skip(reason="unused")\ndef helper():\n    pass\n',
    'import pytest\n@pytest.mark.xfail(reason="unused")\ndef helper():\n    pass\n',
    'import pytest\ndef helper():\n    pytest.skip("unused")\n',
])
def test_unused_skip_xfail_or_skip_call_needs_no_acceptance(local_repo, addition):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert 1 == 1\n')
    probe.write_text(probe.read_text() + addition)
    result = checked(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'new skip or xfail' not in result.stdout


@pytest.mark.parametrize('config,body', [
    ('conftest.py', '# a new pytest hook file\n'),
    ('pytest.ini', '[pytest]\naddopts = -q\n'),
    ('pyproject.toml', '[tool.pytest.ini_options]\naddopts = "-q"\n'),
])
def test_pytest_configuration_needs_acceptance_without_strict(local_repo, config, body):
    root = local_repo
    _, base = setup_tests(root, 'def test_one():\n    assert True\n')
    (root / config).write_text(body)
    result = checked(root, base)
    assert result.returncode == 1 and 'workorder-allow:TASK-1' in result.stdout
    accepted = checked(root, base, '--allow', 'TASK-1')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


@pytest.mark.parametrize('status', ['DONE', 'CLOSED'])
@pytest.mark.parametrize('all_tests', [False, True])
def test_finished_shared_test_users_allow_additions_in_strict_mode(local_repo, status, all_tests):
    root = local_repo
    probe, _ = setup_tests(root, 'def test_one():\n    assert int(1) == 1\n')
    for tid in ('TASK-1', 'TASK-2'):
        supported(root, tid=tid, status=status, test=f'{PYTHON} -m pytest -q tests')
    base = commit_all(root, 'Finished shared test users')
    probe.write_text(probe.read_text() + '\ndef test_two():\n    assert int(1) == 1\n')
    result = checked(root, base, '--strict-tests', *(['--all-tests'] if all_tests else []))
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'workorder-allow:' not in result.stdout
    assert 'its test files or pytest configuration changed' not in result.stdout
    assert '1 passed' not in result.stdout  # successful REF output stays quiet
    assert '2 passed' in result.stdout
    accepted = checked(root, base, '--strict-tests', '--allow', 'TASK-2')
    assert accepted.returncode == 0, accepted.stdout


def test_finished_shared_test_users_run_when_their_tests_change(local_repo):
    root = local_repo
    (root / 'app.py').write_text('value = 1\n')
    probe, _ = setup_tests(root, 'from app import value\ndef test_one():\n    assert value == 1\n')
    supported(root, test=f'{PYTHON} -m pytest -q tests')
    base = commit_all(root, 'Finished task')
    (root / 'app.py').write_text('value = 2\n# Changed code invalidates the previous import cache.\n')
    probe.write_text('from app import value\ndef test_one():\n    assert value == 2\n')
    result = checked(root, base, '--strict-tests')
    assert result.returncode == 1
    assert 'fail on this code' in result.stdout
    assert checked(root, base, '--all-tests', '--allow', 'TASK-1').returncode == 0
    assert 'workorder-allow:TASK-1' in result.stdout


def test_touching_finished_task_restores_strict_acceptance_requirement(local_repo):
    root = local_repo
    probe, _ = setup_tests(root, 'def test_one():\n    assert True\n')
    path = supported(root, test=f'{PYTHON} -m pytest -q tests')
    base = commit_all(root, 'Finished task')
    probe.write_text(probe.read_text() + '\ndef test_two():\n    assert True\n')
    path.write_text(path.read_text() + '\nA progress note.\n')
    result = checked(root, base, '--strict-tests')
    assert result.returncode == 1 and 'workorder-allow:TASK-1' in result.stdout


def test_pytest_default_discovery_compares_changed_tests(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert int(1) == 1\n', f'{PYTHON} -m pytest -q')
    probe.write_text('def test_one():\n    assert int(1) == 1\ndef test_two():\n    assert int(1) == 1\n')
    result = checked(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '2 passed' in result.stdout and '1 passed' not in result.stdout
    # Current tests run; successful comparisons stay quiet.
    assert "ran 1 task's declared test" in result.stdout


@pytest.mark.parametrize('kind', ['script', 'binary'])
def test_explicit_test_paths_restore_base_bytes(local_repo, kind):
    root = local_repo
    if kind == 'script':
        runner = root / 'runner.py'
        runner.write_text('from pathlib import Path\nexec(Path("checks/verify.txt").read_text())\n')
        path = root / 'checks/verify.txt'
        before, after = b'assert False\n', b'assert True\n'
    else:
        runner = root / 'runner.py'
        runner.write_text('from pathlib import Path\nassert Path("checks/data.bin").read_bytes() == b"\\x00new"\n')
        path = root / 'checks/data.bin'
        before, after = b'\x00old', b'\x00new'
    path.parent.mkdir()
    path.write_bytes(before)
    _, base = setup_tests(root, '', f'{PYTHON} runner.py', ['checks'])
    path.write_bytes(after)
    result = checked(root, base, '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'its tests already fail on {base} code' in result.stdout
    assert path.read_bytes() == after


def test_deleted_test_files_are_restored_for_comparison(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'def test_old():\n    assert False\n')
    probe.unlink()
    (probe.parent / 'test_new.py').write_text('def test_new():\n    assert 1 == 1\n')
    result = checked(root, base, '--allow', 'TASK-1')
    assert result.returncode == 0 and f'its tests already fail on {base} code' in result.stdout
    assert not probe.exists()


def test_staged_comparison_uses_index_not_unstaged_test_content(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert True\n')
    probe.write_text('def test_one():\n    assert 2 == 2\n')
    git(root, 'add', 'tests/test_app.py')
    probe.write_text('def test_one():\n    assert False\n')
    result = checked(root, base, '--staged', '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'its test passed when this check ran it' in result.stdout
    assert '1 passed' not in result.stdout
    assert 'assert False' in probe.read_text()


def test_failed_current_tests_do_not_stop_other_scoped_tasks(local_repo):
    root = local_repo
    probe, _ = setup_tests(root, 'def test_one():\n    assert True\n')
    second = root / 'second.py'
    second.write_text('print("SECOND TASK RAN")\n')
    task(root, 'TASK-2', ships='none', test=f'{PYTHON} second.py')
    base = commit_all(root)
    probe.write_text('def test_one():\n    assert False\n')
    second.write_text('print("SECOND TASK RAN")\n# changed\n')
    result = checked(root, base, '--allow', 'TASK-1,TASK-2')
    assert result.returncode == 1 and 'SECOND TASK RAN' in result.stdout


def test_base_test_timeout_refuses_and_preserves_files(local_repo, capsys):
    root = local_repo
    probe, base = setup_tests(root, 'import time\ntime.sleep(120)\n', f'{PYTHON} tests/test_app.py')
    probe.write_text('print("current completed")\n')
    store.add_note(root, 'TASK-1', 'Working on these tests')
    notes = []
    # Current code must have time to start and pass; only the base should time out.
    problems = gate.check(root, base=base, run_tests=True, allow={'TASK-1'}, timeout=10, notes=notes)
    assert not problems
    assert 'current completed' in capsys.readouterr().out
    assert any(f'could not compare its tests on {base}' in line and 'allowed by explicit label' in line
               for line in notes)
    assert not any('its tests already fail' in line for line in notes)
    assert probe.read_text() == 'print("current completed")\n'


def test_comparison_rebases_absolute_internal_symlinks(local_repo):
    root = local_repo
    target = root / 'shared.txt'
    target.write_text('keep this checkout unchanged\n')
    (root / 'shared-link.txt').symlink_to(target)
    body = 'from pathlib import Path\nPath("shared-link.txt").write_text("written by test\\n")\n'
    probe, base = setup_tests(root, body, f'{PYTHON} tests/test_app.py')
    probe.write_text(body + '# changed test\n')
    result = checked(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_text() == 'keep this checkout unchanged\n'


def test_comparison_requires_observable_pytest_outcomes(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert True\n',
                              f'{PYTHON} -m pytest -p no:terminal tests')
    probe.write_text(probe.read_text() + '\ndef test_two():\n    assert True\n')
    result = checked(root, base, '--allow', 'TASK-1')
    assert result.returncode == 1
    assert 'cannot compare test outcomes' in result.stdout


def test_comparison_discovers_absolute_internal_command_paths(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'assert False\n', f'{PYTHON} {root / "tests/test_app.py"}')
    probe.write_text('assert True\n')
    result = checked(root, base, '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert f'its tests already fail on {base} code' in result.stdout


def test_unused_pytest_marker_in_custom_command_needs_no_acceptance(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'assert True\n', f'{PYTHON} tests/test_app.py')
    probe.write_text('import pytest as pt\n@pt.mark.skipif(True, reason="later")\ndef helper():\n    pass\nassert True\n')
    result = checked(root, base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'new skip or xfail' not in result.stdout


@pytest.mark.parametrize('staged', [False, True])
def test_comparison_preserves_changed_symlink_test_files(local_repo, staged):
    root = local_repo
    probe, _ = setup_tests(root, '', f'{PYTHON} tests/test_app.py')
    (probe.parent / 'old.py').write_text('assert 1 == 1\n')
    (probe.parent / 'new.py').write_text('assert 2 == 2\n')
    probe.unlink()
    probe.symlink_to('old.py')
    base = commit_all(root)
    probe.unlink()
    probe.symlink_to('new.py')
    if staged:
        git(root, 'add', 'tests/test_app.py')
    result = checked(root, base, '--allow', 'TASK-1', *(['--staged'] if staged else []))
    assert result.returncode == 0, result.stdout + result.stderr
    assert probe.is_symlink() and probe.readlink().as_posix() == 'new.py'


def test_staged_comparison_omits_untracked_tests(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert True\n')
    probe.write_text('def test_one():\n    assert 2 == 2\n')
    git(root, 'add', 'tests/test_app.py')
    extra = probe.parent / 'test_untracked.py'
    extra.write_text('def test_untracked():\n    assert False\n')
    result = checked(root, base, '--staged', '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert extra.exists()


def test_base_comparison_preserves_binary_line_endings_and_non_utf8(local_repo):
    root = local_repo
    fixture = root / 'fixture.bin'
    fixture.write_bytes(b'\x00\xffa\r\nb')
    (root / 'runner.py').write_text('from pathlib import Path\nvalue = Path("fixture.bin").read_bytes()\nassert b"\\r\\n" in value and value.startswith(b"\\x00\\xff")\n')
    _, base = setup_tests(root, '', f'{PYTHON} runner.py', ['fixture.bin'])
    fixture.write_bytes(b'\x00\xffa\r\nc')
    result = checked(root, base, '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert fixture.read_bytes() == b'\x00\xffa\r\nc'


def test_unreadable_pytest_config_does_not_stop_other_tasks(local_repo):
    root = local_repo
    _, _ = setup_tests(root, 'def test_one():\n    assert True\n', f'{PYTHON} -m pytest -q')
    other = root / 'other.py'
    other.write_text('print("SECOND TASK CHECKED")\n')
    task(root, 'TASK-2', ships='none', test=f'{PYTHON} other.py')
    base = commit_all(root)
    (root / 'pyproject.toml').write_bytes(b'\xff')
    other.write_text(other.read_text() + '# updated\n')
    result = checked(root, base)
    assert result.returncode == 1
    assert 'SECOND TASK CHECKED' in result.stdout


def test_malformed_base_task_definition_does_not_stop_other_tasks(local_repo):
    root = local_repo
    probe, _ = setup_tests(root, 'assert True\n', f'{PYTHON} tests/test_app.py')
    path = root / 'tasks/TASK-1.md'
    valid = path.read_text()
    path.write_text(valid.replace(json.dumps(f'{PYTHON} tests/test_app.py'), '42'))
    other = root / 'other.py'
    other.write_text('print("SECOND TASK CHECKED")\n')
    task(root, 'TASK-2', ships='none', test=f'{PYTHON} other.py')
    base = commit_all(root)
    path.write_text(valid)
    probe.write_text('assert 2 == 2\n')
    other.write_text(other.read_text() + '# updated\n')
    result = checked(root, base)
    assert result.returncode == 1
    assert 'TASK-1:' in result.stdout and 'SECOND TASK CHECKED' in result.stdout


@pytest.mark.parametrize('config', ['.pytest.ini', 'pytest.toml', '.pytest.toml'])
def test_alternate_standard_pytest_configs_cannot_hide_selection(local_repo, config):
    if config.endswith('.toml') and pytest.version_tuple < (9,):
        pytest.skip('pytest.toml and .pytest.toml are read by pytest 9 or newer')
    root = local_repo
    _, base = setup_tests(root, 'def test_one():\n    assert True\ndef test_two():\n    assert True\n',
                          f'{PYTHON} -m pytest -q')
    body = '[pytest]\naddopts = ["-k", "two"]\n' if config.endswith('.toml') else '[pytest]\naddopts = -k two\n'
    (root / config).write_text(body)
    result = checked(root, base)
    assert result.returncode == 1 and 'workorder-allow:TASK-1' in result.stdout
    assert f'fewer tests pass than at {base} (2 -> 1)' in result.stdout


@pytest.mark.parametrize('config', ['.pytest.ini', 'pytest.toml', '.pytest.toml'])
def test_alternate_pytest_configs_supply_custom_discovery_patterns(local_repo, config):
    if config.endswith('.toml') and pytest.version_tuple < (9,):
        pytest.skip('pytest.toml and .pytest.toml are read by pytest 9 or newer')
    root = local_repo
    _, _ = setup_tests(root, '', f'{PYTHON} -m pytest -q')
    body = '[pytest]\npython_files = ["check_*.py"]\n' if config.endswith('.toml') else '[pytest]\npython_files = check_*.py\n'
    (root / config).write_text(body)
    (root / 'check_existing.py').write_text('def test_one():\n    assert True\n')
    base = commit_all(root)
    (root / 'check_added.py').write_text('def test_new():\n    assert bool(0)\n')
    result = checked(root, base)
    assert result.returncode == 0 and 'its test failed' in result.stdout
    assert 'Note:' in result.stdout and 'not claimed finished' in result.stdout
