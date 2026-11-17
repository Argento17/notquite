"""Output an agent can read: grouped findings, one failure body, each Note once, full text in .git."""

import io
import re
import sys

import pytest

from workorder import console
from test_workorder import cli, git, local_repo, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v16b_project import settings

LOG = '.git/workorder/last-output.txt'


def many_failures_project(root, count=12, *, own_failure=False, ids=None):
    """A project test with `count` tests broken by this change; the task's own test passes."""
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    ids = ids or [f'case{number:02}' for number in range(count)]
    (root / 'tests/test_project.py').write_text(
        'import app, pytest\n\n'
        f'@pytest.mark.parametrize("number", range({len(ids)}), ids={ids!r})\n'
        'def test_value(number):\n'
        '    for line in range(40):\n'
        '        print(f"failure detail {line:03}")\n'
        '    assert app.value == 1\n')
    (root / 'tests/test_own.py').write_text(
        'import app\n\ndef test_own():\n' + ('    for line in range(40):\n        print(f"own detail {line:03}")\n'
                                              if own_failure else '')
        + '    assert app.feature is True\n')
    fill_sections(task(root, ships='none', test='python -m pytest -q tests/test_own.py'))
    settings(root, 'python -m pytest -q tests/test_project.py')
    (root / 'app.py').write_text('value = 1\nfeature = False\n')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')
    (root / 'app.py').write_text(f'value = 0\nfeature = {not own_failure}\n')
    commit_all(root)


def run_action(root, action, *extra):
    flags = ('done', '--task', 'TASK-1') if action == 'done' else (
        'check', '--base', 'origin/main', '--run-tests')
    return cli(root, *flags, *extra)


@pytest.mark.parametrize('action', ['done', 'check'])
def test_many_project_failures_print_one_grouped_line_and_save_the_full_list(local_repo, action):
    root = local_repo
    many_failures_project(root)
    result = run_action(root, action)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    grouped = [line for line in output.splitlines() if 'project tests fail on this code' in line]
    assert len(grouped) == 1, output
    assert grouped[0].startswith(('  Missing:  12 project tests fail on this code that pass on origin/main: ',
                                  '12 project tests fail on this code that pass on origin/main: ')), output
    assert 'tests/test_project.py::test_value[case00]' in grouped[0]
    assert 'tests/test_project.py::test_value[case05]' not in grouped[0]
    assert '(+7 more); a reviewer accepts them with the label workorder-allow:tests' in grouped[0]
    assert output.count('(passes on origin/main)') == 0, output
    # Pytest's FAILED lines are capped, and only one failure body is shown, cut at 20 lines.
    assert len(re.findall(r'^FAILED ', output, re.M)) == 5, output
    assert '(+7 more failed tests in the full output)' in output
    assert 'failure detail 000' in output and 'failure detail 020' not in output, output
    assert len(output.splitlines()) <= 45, output
    assert f'Full output: {LOG} (rerun with --verbose to print it all)' in output
    if action == 'done':
        assert 'TASK-1: DONE was not written' in output
        assert 'Next:' in output
    saved = (root / LOG).read_text(encoding='utf-8')
    for number in range(12):
        assert f'test_value[case{number:02}] (passes on origin/main)' in saved
    assert 'failure detail 039' in saved
    assert git(root, 'status', '--porcelain', '--untracked-files=all') == ''

    verbose = run_action(root, action, '--verbose')
    assert verbose.returncode == 1
    assert verbose.stdout.count('(passes on origin/main)') == 12, verbose.stdout
    assert 'Full output:' not in verbose.stdout + verbose.stderr


def test_a_test_name_that_looks_like_a_task_id_is_not_another_task(local_repo):
    root = local_repo
    many_failures_project(root, ids=['SHOUT-1', 'SHOUT-2'])
    result = run_action(root, 'done')
    assert result.returncode == 1
    assert 'Other:' not in result.stdout, result.stdout
    assert '2 project tests fail on this code that pass on origin/main: ' in result.stdout


def test_declared_test_failure_body_is_cut_and_shown_once(local_repo):
    root = local_repo
    many_failures_project(root, own_failure=True)
    result = run_action(root, 'done')
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert 'own detail 000' in output and 'own detail 020' not in output, output
    assert '[first failure: 20 of ' in output
    assert 'FAILED tests/test_own.py::test_own' in output
    assert '  Missing:  ' in output, output
    assert 'TASK-1: DONE was not written' in output
    assert len(output.splitlines()) <= 40, output
    assert 'own detail 039' in (root / LOG).read_text(encoding='utf-8')


def test_check_shows_one_failure_body_per_run(local_repo):
    root = local_repo
    many_failures_project(root)
    result = run_action(root, 'check')
    output = result.stdout + result.stderr
    # Several failing outputs in one run: the start of a failure body is printed once.
    assert output.count('failure detail 000') == 1, output


def test_each_distinct_note_prints_once_per_run(monkeypatch):
    out = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', out)
    with console.session(None):
        console.print('Note: same')
        console.print('Note: same\nNote: other\nplain')
        console.print('plain')
        console.test_output('Note: test output is never deduplicated\n')
        console.test_output('Note: test output is never deduplicated\n')
    assert out.getvalue() == ('Note: same\nNote: other\nplain\nplain\n'
                              'Note: test output is never deduplicated\n'
                              'Note: test output is never deduplicated\n')


def test_grouping_keeps_other_lines_and_their_order():
    messages = ['first', '', *[f'the project test fails on this code: t.py::t[{n}] (passes on main)'
                                for n in range(3)], '', 'NOT DONE  the project test also fails on main: a::b',
                'NOT DONE  the project test also fails on main: a::c']
    with console.session(None):
        assert console.group(messages) == [
            'first', '', '3 project tests fail on this code that pass on main: t.py::t[0], t.py::t[1], t.py::t[2]',
            '', 'NOT DONE  2 project tests also fail on main: a::b, a::c']
    with console.session(None, verbose=True):
        assert console.group(messages) == messages


def test_show_accepts_verbose(local_repo):
    root = local_repo
    fill_sections(task(root, ships='none', test='python -m pytest -q'))
    commit_all(root)
    result = cli(root, 'show', '--task', 'TASK-1', '--verbose')
    assert result.returncode == 0, result.stderr
    assert 'TASK-1' in result.stdout


def test_a_run_that_shortens_nothing_removes_an_older_full_output(local_repo):
    root = local_repo
    many_failures_project(root)
    assert run_action(root, 'check').returncode == 1
    assert (root / LOG).is_file()
    # The same change, fixed: nothing is shortened, so no older file may be left to mislead.
    (root / 'app.py').write_text('value = 1\nfeature = True\n')
    commit_all(root)
    result = run_action(root, 'check')
    assert 'Full output:' not in result.stdout + result.stderr
    assert not (root / LOG).exists(), result.stdout + result.stderr
