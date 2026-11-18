"""0.19 speed: done leaves the full suite to the pull-request check, shows where the time went,
reuses an unchanged base result and stops base runs at the first failure."""

import re

import pytest

from workorder import gate, store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v16b_project import FAILURE, project


DEFERRED = ('Note: the project test runs in the pull-request check (check --run-tests), not in done; '
            'run done --project-test to run it now')


def test_done_leaves_the_project_test_to_the_pull_request_check(local_repo):
    root = local_repo
    log = project(root, with_task=True)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert store.record(root, 'TASK-1')['status'] == 'DONE', output
    assert DEFERRED in output.splitlines(), output
    assert not log.exists(), output  # the project command did not run
    # The 0.18 protection stays in the pull-request check, which runs the suite once.
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests')
    assert result.returncode == 1, result.stdout + result.stderr
    assert FAILURE in result.stdout + result.stderr
    assert log.read_text().splitlines() == ['project:True', 'unrelated:True', 'project:False']


def test_done_project_test_flag_runs_and_refuses_the_suite(local_repo):
    root = local_repo
    log = project(root, with_task=True)
    result = cli(root, 'done', '--task', 'TASK-1', '--project-test')
    assert result.returncode == 1, result.stdout + result.stderr
    assert FAILURE in result.stdout + result.stderr
    assert DEFERRED not in result.stdout
    assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'
    assert log.read_text().splitlines() == ['project:True', 'unrelated:True', 'project:False']


TIME = re.compile(r'^Time: \d+\.\ds: setup \d+\.\ds, new code (\d+\.\ds|not run), base code (\d+\.\ds|not run), '
                  r'stubbed (\d+\.\ds|not run), project test (\d+\.\ds|not run), (other \d+\.\ds|runs overlapped)$')


def test_done_and_the_pull_request_check_print_where_the_time_went(local_repo):
    root = local_repo
    project(root, with_task=True, broken_head=False)

    def times(*args):
        result = cli(root, *args)
        assert result.returncode == 0, result.stdout + result.stderr
        assert 'Time:' not in result.stdout  # a diagnostic on stderr, after the findings
        return [line for line in result.stderr.splitlines() if line.startswith('Time:')]

    done = times('done', '--task', 'TASK-1')
    assert len(done) == 1 and TIME.match(done[0]), done
    assert 'project test not run' in done[0] and 'new code not run' not in done[0]
    pull = times('check', '--base', 'origin/main', '--run-tests')
    assert len(pull) == 1 and TIME.match(pull[0]), pull
    assert 'project test not run' not in pull[0]
    assert not times('check')


@pytest.mark.parametrize('kind', ['current', 'ref'])
def test_comparison_copies_share_the_object_store_instead_of_copying_it(local_repo, tmp_path, kind):
    root = local_repo
    (root / 'app.py').write_text('value = 1\n')
    commit_all(root)
    destination = tmp_path / 'copy'
    if kind == 'current':
        gate._test_snapshot(root, destination)
    else:
        gate._ref_test_snapshot(root, destination, store.head(root))
    objects = destination / '.git' / 'objects'
    assert (objects / 'info' / 'alternates').is_file()
    copied = [path for path in objects.rglob('*') if path.is_file() and 'info' not in path.parts]
    assert copied == []  # nothing duplicated from the work tree's object store
    assert (destination / 'app.py').read_text() == 'value = 1\n'
    # The copy stays independent: its commits do not reach the work tree's repository.
    (destination / 'app.py').write_text('value = 2\n')
    git(destination, '-c', 'user.name=copy', '-c', 'user.email=copy@example.com', 'commit', '-qam', 'copy only')
    assert git(root, 'rev-parse', 'HEAD') != git(destination, 'rev-parse', 'HEAD')
    assert (root / 'app.py').read_text() == 'value = 1\n'


def base_project(root, layout):
    """A task whose test logs which code it ran on: 'base' on the base code, 'new' on the change."""
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    log = root.parent / 'base-runs.log'
    if layout == 'package':
        (root / 'app').mkdir()
        code = root / 'app' / '__init__.py'
    else:
        code = root / 'app.py'
    code.write_text('version = "base"\n\n\ndef feature():\n    return False\n')
    fill_sections(task(root, ships='none', test='python -m pytest -q tests/test_own.py'))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')
    code.write_text('version = "new"\n\n\ndef feature():\n    return True\n')
    (root / 'tests').mkdir()
    (root / 'tests' / 'test_own.py').write_text(
        'from pathlib import Path\n\nimport app\n\n\ndef test_own():\n'
        f'    with Path({str(log)!r}).open("a") as output:\n'
        '        output.write(app.version + "\\n")\n'
        '    assert app.feature() is True\n')
    commit_all(root)
    return code, log


@pytest.mark.parametrize('layout', ['module', 'package'])
def test_a_second_done_on_the_same_base_reuses_the_base_result_only_while_inputs_match(
        local_repo, monkeypatch, layout):
    root = local_repo
    code, log = base_project(root, layout)

    def base_runs():
        return log.read_text().splitlines().count('base')

    ok(root, 'done', '--task', 'TASK-1')
    assert base_runs() == 1
    code.write_text(code.read_text() + '# a later change to the new code only\n')
    commit_all(root)
    second = ok(root, 'done', '--task', 'TASK-1')
    assert base_runs() == 1, second
    assert '(result reused from the run at ' in second
    # Any differing input runs the base again: the test file's content,
    test = root / 'tests' / 'test_own.py'
    test.write_text(test.read_text() + '\n# changed\n')
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-1')
    assert base_runs() == 2
    # the timeout,
    ok(root, 'done', '--task', 'TASK-1', '--timeout', '100')
    assert base_runs() == 3
    # the interpreter's environment,
    monkeypatch.setenv('PYTHONSAFEPATH', '')
    ok(root, 'done', '--task', 'TASK-1', '--timeout', '100')
    assert base_runs() == 4
    # and CI always runs fresh.
    monkeypatch.setenv('CI', '1')
    ok(root, 'done', '--task', 'TASK-1', '--timeout', '100')
    assert base_runs() == 5


def test_the_base_run_stops_at_its_first_failure_while_the_new_code_run_reports_every_test(local_repo):
    root = local_repo
    code, log = base_project(root, 'package')
    test = root / 'tests' / 'test_own.py'
    test.write_text(test.read_text() + '\n\ndef test_second():\n    test_own()\n')
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1')
    runs = log.read_text().splitlines()
    assert runs.count('base') == 1, (runs, output)  # -x: the first base failure proves the change
    assert runs.count('new') >= 2, runs  # the new code still runs both tests
    assert store.record(root, 'TASK-1')['status'] == 'DONE'


def test_a_base_pass_is_still_found_with_the_early_stop(local_repo):
    root = local_repo
    code, log = base_project(root, 'package')
    test = root / 'tests' / 'test_own.py'
    test.write_text(test.read_text().replace('assert app.feature() is True', 'assert app.feature() in (True, False)'))
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1, result.stdout + result.stderr
    assert "pass on origin/main's code too" in result.stdout + result.stderr


def timed_project(root):
    code, log = base_project(root, 'package')
    test = root / 'tests' / 'test_own.py'
    test.write_text(
        'import time\nfrom pathlib import Path\n\nimport app\n\n\ndef test_own():\n'
        '    start = time.time()\n    time.sleep(1)\n'
        f'    with Path({str(log)!r}).open("a") as output:\n'
        '        output.write(f"{app.version} {start} {time.time()}\\n")\n'
        '    assert app.feature() is True\n')
    commit_all(root)
    return log


def intervals(log):
    return [(version, float(start), float(end))
            for version, start, end in (line.split() for line in log.read_text().splitlines())]


@pytest.mark.parametrize('jobs', [None, '1'])
def test_done_runs_the_base_and_stubbed_and_new_code_copy_at_the_same_time(local_repo, jobs):
    root = local_repo
    log = timed_project(root)
    import os
    os.environ['WORKORDER_DEBUG'] = '1'  # a parallel run that fails says why on stderr
    try:
        result = cli(root, 'done', '--task', 'TASK-1', *(('--jobs', jobs) if jobs else ()))
    finally:
        del os.environ['WORKORDER_DEBUG']
    assert result.returncode == 0, result.stdout + result.stderr
    first = result.stdout
    runs = intervals(log)
    assert 'parallel run failed' not in result.stderr, result.stderr
    base = [run for run in runs if run[0] == 'base']
    assert len(base) == 1, runs
    _, start, end = base[0]
    overlapping = [run for run in runs if run[0] == 'new' and run[1] < end and start < run[2]]
    if jobs == '1':
        assert overlapping == [], runs
        assert 'runs overlapped' not in result.stdout + result.stderr
    else:
        # done started the runs side by side (its Time line says so). Whether two 1-second
        # runs actually overlap depends on the machine: shared CI runners can serialise them.
        assert 'runs overlapped' in result.stdout + result.stderr, (runs, result.stderr)
        if not os.environ.get('GITHUB_RUN_ID'):  # conftest removes GITHUB_ACTIONS and CI
            assert overlapping, (runs, result.stderr)
    assert store.record(root, 'TASK-1')['status'] == 'DONE', first



@pytest.mark.parametrize('layout', ['module', 'package'])
def test_the_base_result_key_ignores_permission_bits_git_does_not_record(local_repo, tmp_path, layout):
    """done keys a work-tree test file by its lstat mode (100664); the pre-commit hook by
    its staged mode (100644). Both describe the same run, so the hook reuses done's result."""
    root = local_repo
    code, log = base_project(root, layout)
    base = git(root, 'rev-parse', 'origin/main')
    test = (root / 'tests' / 'test_own.py').read_bytes()
    command = [PYTHON, '-m', 'pytest', '-q', 'tests/test_own.py']
    with store.base_reuse():
        first = gate.run_ref_test(root, tmp_path / 'a', base, command, None, None,
                                  {'tests/test_own.py': (test, 0o100664)})
        second = gate.run_ref_test(root, tmp_path / 'b', base, command, None, None,
                                   {'tests/test_own.py': (test, 0o100644)})
        executable = gate.run_ref_test(root, tmp_path / 'c', base, command, None, None,
                                       {'tests/test_own.py': (test, 0o100755)})
    assert first[0] == second[0] == executable[0] == 1, (first, second, executable)
    # The executable bit is recorded by Git and can change a run: it is a different key.
    assert log.read_text().splitlines().count('base') == 2
