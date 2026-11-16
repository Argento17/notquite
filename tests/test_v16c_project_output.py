"""Project-test diagnostics stay useful even when a large suite is noisy."""

import re

import pytest

from workorder import project_tests, store
from test_workorder import cli, git, local_repo, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v16b_project import settings


def output_project(root, *, broken_head=True, broken_base=False, quiet='-q'):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    (root / 'pytest.ini').write_text('[pytest]\naddopts = -rA --color=yes\n')
    (root / 'tests/test_project.py').write_text(
        'import app, pytest\n\n'
        '@pytest.mark.parametrize("number", range(60))\n'
        'def test_passing(number):\n'
        '    print("PASSING CAPTURE MUST STAY HIDDEN")\n'
        '    assert number >= 0\n\n'
        '@pytest.mark.parametrize("number", [1], ids=["a - b"])\n'
        'def test_project(number):\n'
        '    for line in range(100):\n'
        '        print(f"failure detail {line:03}")\n'
        '    assert app.value == number\n\n'
        '@pytest.mark.skip(reason="not applicable")\n'
        'def test_skipped():\n    assert False\n\n'
        '@pytest.mark.xfail(reason="known limitation")\n'
        'def test_xfailed():\n    assert False\n')
    (root / 'tests/test_own.py').write_text(
        'import app\n\ndef test_own():\n    assert app.feature is True\n')
    fill_sections(task(root, ships='none', test='python -m pytest -q -rN --color=no tests/test_own.py'))
    settings(root, f'python -m pytest {quiet} tests/test_project.py')
    (root / 'app.py').write_text(f'value = {0 if broken_base else 1}\nfeature = False\n')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')
    (root / 'app.py').write_text(f'value = {0 if broken_head else 1}\nfeature = True\n')
    commit_all(root)


def run_action(root, action):
    flags = ('done', '--task', 'TASK-1') if action == 'done' else (
        'check', '--base', 'origin/main', '--run-tests')
    return cli(root, *flags)


@pytest.mark.parametrize('action', ['done', 'check'])
@pytest.mark.parametrize('broken_base', [False, True])
def test_project_failure_output_is_bounded_and_base_prints_only_counts(local_repo, action, broken_base):
    root = local_repo
    output_project(root, broken_base=broken_base)
    result = run_action(root, action)
    output = result.stdout + result.stderr
    assert result.returncode == (0 if broken_base else 1), output
    assert not re.search(r'^PASSED\b', output, re.M), output
    assert 'PASSING CAPTURE MUST STAY HIDDEN' not in output
    assert not re.search(r'^[.sxF]{10,}', output, re.M), output
    assert '\x1b[' not in output
    assert 'FAILED tests/test_project.py::test_project[a - b]' in output
    assert 'assert app.value == number' in output
    assert '1 failed, 60 passed, 1 skipped, 1 xfailed in ' in output
    assert 'failure detail 099' not in output
    assert 'failure section truncated after 60 lines' in output
    section = next(i for i, line in enumerate(output.splitlines())
                   if line.startswith('_') and 'test_project[a - b]' in line)
    cut = next(i for i, line in enumerate(output.splitlines())
               if 'failure section truncated after 60 lines' in line)
    assert cut - section == 60, output
    assert len(output.splitlines()) < 120, output
    assert len(re.findall(r'^FAILED tests/test_project.py::test_project', output, re.M)) == 1
    expected_base = '1 failed' if broken_base else '1 passed'
    assert re.search(r'Note: the project test on origin/main \(' + expected_base
                     + r' in [\d.]+s\)', output), output
    if not broken_base:
        assert '(passes on origin/main)' in output
        if action == 'done':
            assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'
            assert store.entries(root, 'TASK-1') == []


@pytest.mark.parametrize('action', ['done', 'check'])
@pytest.mark.parametrize('quiet', ['-q', '-qq', '-q -n 2'])
def test_passing_project_prints_one_line_with_pytest_counts(local_repo, action, quiet):
    root = local_repo
    output_project(root, broken_head=False, quiet=quiet)
    result = run_action(root, action)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    lines = [line for line in output.splitlines() if 'project test' in line]
    assert len(lines) == 1, output
    assert re.fullmatch(r'Note: the project test passed on this code '
                        r'\(61 passed, 1 skipped, 1 xfailed in [\d.]+s\)', lines[0]), output
    assert not re.search(r'^(?:PASSED|SKIPPED|XFAIL)\b', output, re.M), output
    assert 'PASSING CAPTURE MUST STAY HIDDEN' not in output
    assert 'failure detail' not in output
    assert 'test_project.py' not in output
    assert len(output.splitlines()) < 40, output


@pytest.mark.parametrize('phase', ['setup', 'teardown', 'collect'])
def test_project_errors_keep_diagnostics_without_passing_output(local_repo, capsys, phase):
    root = local_repo
    output_project(root, broken_head=False)
    if phase == 'collect':
        (root / 'tests/test_bad.py').write_text('raise RuntimeError("COLLECT BROKEN")\n')
    else:
        body = '    yield\n' if phase == 'teardown' else ''
        (root / 'tests/test_bad.py').write_text(
            'import pytest\n\n@pytest.fixture\ndef broken():\n' + body
            + f'    raise RuntimeError("{phase.upper()} BROKEN")\n\n'
            'def test_bad(broken):\n    assert 1 + 1 == 2\n')
    settings(root, 'python -m pytest -q tests/test_project.py tests/test_bad.py')
    problems = project_tests.run(root, base='origin/main')
    output = capsys.readouterr().out
    assert problems
    assert phase.upper() + ' BROKEN' in output
    assert re.search(r'^ERROR tests/test_bad.py', output, re.M), output
    assert '1 error' in output
    assert 'PASSING CAPTURE MUST STAY HIDDEN' not in output
    assert not re.search(r'^PASSED\b', output, re.M), output
    assert len(output.splitlines()) < 100, output


def test_each_failing_test_gets_its_own_sixty_line_limit(local_repo, capsys):
    root = local_repo
    output_project(root)
    path = root / 'tests/test_project.py'
    path.write_text(path.read_text() + '\n\ndef test_second():\n'
                    '    for line in range(100):\n'
                    '        print(f"second detail {line:03}")\n'
                    '    assert app.value == 1\n')
    problems = project_tests.run(root, base='origin/main')
    output = capsys.readouterr().out
    assert len(problems) == 2
    assert output.count('failure section truncated after 60 lines') == 2
    assert 'FAILED tests/test_project.py::test_second' in output
    assert 'second detail 000' in output
    assert 'second detail 099' not in output
    assert len(output.splitlines()) < 135


def test_parallel_project_failure_keeps_exact_node_id(local_repo, capsys):
    root = local_repo
    output_project(root, quiet='-q -n 2')
    notes = []
    problems = project_tests.run(root, base='origin/main', notes=notes)
    output = capsys.readouterr().out
    assert len(problems) == 1
    assert 'test_project[a - b] (passes on origin/main)' in problems[0]
    assert any('the project test on origin/main (1 passed in ' in note for note in notes)
    assert re.search(r'^FAILED tests/test_project.py::test_project\[a - b\]', output, re.M)
    assert '1 failed, 60 passed, 1 skipped, 1 xfailed in ' in output
    assert len(output.splitlines()) < 70


def test_project_startup_failure_keeps_error_and_bounds_output(local_repo, capsys):
    root = local_repo
    output_project(root)
    (root / 'conftest.py').write_text(
        'for line in range(100):\n    print(f"startup noise {line:03}")\n'
        'raise RuntimeError("STARTUP BROKEN")\n')
    problems = project_tests.run(root, base='origin/main')
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert problems and 'could not identify failing tests' in problems[0]
    assert 'STARTUP BROKEN' in output
    assert 'project-test output truncated to last 60 lines' in output
    assert len(output.splitlines()) <= 61


@pytest.mark.parametrize('interruption', ['keyboard', 'internal'])
def test_interrupted_project_does_not_replay_passing_output(local_repo, capsys, interruption):
    root = local_repo
    output_project(root)
    crash = ('raise KeyboardInterrupt("INTERRUPTED")' if interruption == 'keyboard'
             else 'assert 1 + 1 == 2')
    (root / 'tests/test_project.py').write_text(
        'def test_pass():\n    print("PASSING CAPTURE MUST STAY HIDDEN")\n'
        '    assert 1 + 1 == 2\n\n'
        f'def test_crash():\n    {crash}\n')
    if interruption == 'internal':
        (root / 'conftest.py').write_text(
            'def pytest_runtest_protocol(item, nextitem):\n'
            '    if item.name == "test_crash":\n'
            '        raise RuntimeError("INTERNAL BROKEN")\n')
    settings(root, 'python -m pytest -q -s tests/test_project.py')
    problems = project_tests.run(root, base='origin/main')
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert problems and 'could not identify failing tests' in problems[0]
    assert ('INTERRUPTED' if interruption == 'keyboard' else 'INTERNAL BROKEN') in output
    assert 'PASSING CAPTURE MUST STAY HIDDEN' not in output
    assert not re.search(r'^\.+', output, re.M)
    assert output.count('1 passed in ') == 1
    assert len(output.splitlines()) < 70


def test_project_usage_error_keeps_error_without_duplicate_counts(local_repo, capsys):
    root = local_repo
    output_project(root)
    settings(root, 'python -m pytest -q tests/missing.py')
    problems = project_tests.run(root, base='origin/main')
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert problems and 'could not identify failing tests' in problems[0]
    assert 'ERROR: file or directory not found: tests/missing.py' in output
    assert output.count('no tests ran in ') == 1


def test_unspecified_session_failure_does_not_replay_passing_output(local_repo, capsys):
    root = local_repo
    output_project(root, broken_head=False)
    (root / 'conftest.py').write_text(
        'def pytest_sessionfinish(session, exitstatus):\n    session.exitstatus = 1\n')
    settings(root, 'python -m pytest -q -s tests/test_project.py')
    problems = project_tests.run(root, base='origin/main')
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert problems and 'could not identify failing tests' in problems[0]
    assert 'PASSING CAPTURE MUST STAY HIDDEN' not in output
    assert not re.search(r'^\.+', output, re.M)
    assert output.count('61 passed, 1 skipped, 1 xfailed in ') == 1
    assert len(output.splitlines()) == 1
