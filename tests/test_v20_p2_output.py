"""1.0 polish lane P2: output that says exactly what happened (five real-project verifiers' findings)."""
import os
from pathlib import Path
import re
import subprocess

import pytest

from workorder import console, gate, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, ok, task  # noqa: F401  (fixture)
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v16b_project import settings
from test_v18g import TASK, landed

WEAK = 'from calc import value\n\ndef test_double():\n    assert value() == 1\n'
TIED = '- double(2) is 4 (test: tests/test_double.py::test_double)'


def merged(root, *args):
    """stdout and stderr in the order a reader sees them on one terminal."""
    env = dict(os.environ, PYTHONPATH=str(PACKAGE), GIT_TERMINAL_PROMPT="0")
    return subprocess.run([PYTHON, '-m', 'workorder', *args], cwd=root, env=env, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, encoding='utf-8', errors='replace', timeout=300)


# 1: a full stop after the tie keeps it; an unreadable (test: is refused, never ignored

@pytest.mark.parametrize('end', ['.', '. ', ';', '!', ' .'])
def test_tie_survives_closing_punctuation(end):
    items = store.acceptance_items(f'## Acceptance\n\n{TIED}{end}\n')
    assert items == [((TIED + end).strip(), 'tests/test_double.py::test_double')]


def test_done_checks_a_tie_that_ends_with_a_full_stop(local_repo):
    root = local_repo
    landed(root, acceptance=TIED + '.')
    output = ok(root, 'done', '--task', 'TASK-1')
    # Was: "Acceptance is not checked by Workorder" (the full stop dropped the tie).
    assert 'Acceptance: 1 of 1 item tied to tests that pass now and failed before' in output


def test_done_refuses_a_tie_it_cannot_read(local_repo):
    root = local_repo
    landed(root, acceptance='- double(2) is 4 (test: tests/test_double.py::test_double) and more')
    refused = cli(root, 'done', '--task', 'TASK-1')
    assert refused.returncode == 1, refused.stdout
    assert "acceptance item 'double(2) is 4 (test: tests/test_double.py::test_double) and more': " \
           "its (test: ...) is not read as a tie to a test" in refused.stdout
    assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'


def test_new_refuses_an_acceptance_item_with_an_unreadable_tie(local_repo):
    refused = cli(local_repo, 'new', '--title', 'X', '--owner', 'alex', '--test', 'python -m pytest -q',
                  '--test-paths', 'tests', '--acceptance', 'works (test: tests/test_x.py::test_x) mostly')
    assert refused.returncode == 1
    assert 'is not read as a tie to a test' in refused.stdout + refused.stderr


# 2: accept refuses inside a coding agent's session, naming the variable

@pytest.mark.parametrize('name', ['CLAUDECODE', 'CODEX_THREAD_ID', 'CODEX_SANDBOX', 'CURSOR_AGENT', 'GEMINI_CLI',
                                  'AI_AGENT'])
def test_accept_refuses_in_an_agent_session(local_repo, monkeypatch, name):
    root = local_repo
    landed(root)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    monkeypatch.setenv(name, '1')
    refused = cli(root, 'accept', '--task', 'TASK-1')
    assert refused.returncode == 1
    assert f'TASK-1: an agent cannot accept a task ({name} is set' in refused.stderr + refused.stdout
    monkeypatch.delenv(name)
    assert 'TASK-1 accepted by' in ok(root, 'accept', '--task', 'TASK-1')


# 3: done runs the task's own test once; other tasks' runs are labelled; Time: comes last

LOGGED = ('import os\nfrom calc import double\n\n'
          'with open({log!r}, "a") as _log:\n    _log.write(os.getcwd() + "\\n")\n\n'
          'def test_double():\n    assert double(2) == 4\n')


def test_done_runs_the_own_test_once_on_the_new_code(local_repo, tmp_path):
    root = local_repo
    log = tmp_path / 'runs.log'
    landed(root, test=LOGGED.format(log=str(log)))
    output = merged(root, 'done', '--task', 'TASK-1')
    assert output.returncode == 0, output.stdout
    runs = log.read_text().splitlines()
    new_code = [run for run in runs if Path(run) == root or Path(run).name == 'current']
    # Was 2: the work tree, then /tmp/workorder-tests-*/current, each printing "1 passed".
    assert len(new_code) == 1, runs
    assert Path(new_code[0]).name == 'current'
    assert output.stdout.count('1 passed') == 1, output.stdout
    assert output.stdout.splitlines()[-1].startswith('Time: '), output.stdout
    receipt = store.entries(root, 'TASK-1')[-1]
    assert receipt['kind'] == 'test' and receipt['exit_code'] == 0 and receipt['valid']


SECOND = TASK.replace('id: "TASK-1"', 'id: "TASK-2"').replace('title: "Double"', 'title: "Triple"').replace(
    'tests/test_double.py', 'tests/test_triple.py')
TRIPLE = ('from calc import triple\n\ndef test_triple():\n    assert triple(2) == 6\n\n'
          'def test_triple_zero():\n    assert triple(0) == 0\n')


def test_done_labels_the_tests_of_finished_tasks_it_reruns(local_repo):
    root = local_repo
    landed(root)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    git(root, 'switch', '-q', 'main')
    git(root, 'merge', '-q', '--ff-only', 'feature')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-qc', 'triple')
    (root / 'tasks/TASK-2.md').write_text(SECOND.format(acceptance='- triple(2) is 6'))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    calc = (root / 'calc.py').read_text()
    (root / 'calc.py').write_text(calc + '\ndef triple(x):\n    return 3 * x\n')
    (root / 'tests/test_triple.py').write_text(TRIPLE)
    commit_all(root)
    output = merged(root, 'done', '--task', 'TASK-2')
    assert output.returncode == 0, output.stdout
    lines = output.stdout.splitlines()
    label = lines.index("TASK-1's test (its code changed in this change):")
    assert '1 passed' in '\n'.join(lines[label:label + 3]), output.stdout
    assert '2 passed' in '\n'.join(lines[:label]), output.stdout
    assert lines[-1].startswith('Time: '), output.stdout


def test_done_prints_the_test_origin_as_a_note(local_repo):
    root = local_repo
    landed(root)
    assert "Note: TASK-1's test was written in this change" in ok(root, 'done', '--task', 'TASK-1')


# 4: the edited-test finding names the test function and what changed; "1 line", not "1 lines"

def test_changed_test_functions_name_what_changed():
    old = 'def test_a():\n    assert f() == 4\n\ndef test_b():\n    assert g()\n\ndef test_c():\n    x = 1\n'
    new = 'def test_a():\n    assert f() is not None\n\ndef test_c():\n    x = 2\n'
    assert gate.changed_test_functions(old, new) == ['assertion changed in test_a', 'test_b removed',
                                                     'test_c changed']
    assert gate.changed_test_functions('def test_a():\n    assert f()\n    assert g()\n',
                                       'def test_a():\n    assert f()\n') == ['assertion removed in test_a']


def test_edited_test_finding_names_the_function(local_repo):
    root = local_repo
    landed(root)
    ok(root, 'done', '--task', 'TASK-1')
    commit_all(root)
    base = git(root, 'rev-parse', 'HEAD')
    (root / 'tests/test_double.py').write_text(
        'from calc import double\n\ndef test_double():\n    assert double(2) is not None\n')
    commit_all(root)
    record = store.record(root, 'TASK-1')
    finding = gate.test_file_changes(root, base, record)
    assert finding == (f'TASK-1: its test files lost or changed 1 line since {base} '
                       '(tests/test_double.py: assertion changed in test_double)')
    existing = gate.test_file_changes(root, base, record, existing_only=True)
    assert existing.endswith('(tests/test_double.py: assertion changed in test_double)')


# 5: counts that agree with what is shown

def test_summary_names_tasks_run_beyond_those_checked():
    counts = {'tasks': 1, 'claimed': 0, 'tests': 2, 'tested_tasks': {'TASK-1', 'TASK-2'}}
    assert gate.check_summary(counts, run_tests=True) == (
        "checked 1 task (0 claimed DONE or CLOSED); ran 2 tasks' declared tests (TASK-1, TASK-2; "
        "a finished task's test also runs when this change touches its code or tests)")


def test_summary_of_reused_results_names_tasks_in_one_parenthesis():
    counts = {'tasks': 1, 'claimed': 0, 'tests': 2, 'cached': 2, 'tested_tasks': {'TASK-1', 'TASK-2'}}
    assert gate.check_summary(counts, run_tests=True) == (
        "checked 1 task (0 claimed DONE or CLOSED); reused 2 tasks' declared test results (TASK-1, TASK-2; "
        "cached, not run again; a finished task's test also runs when this change touches its code or tests)")


def test_summary_of_reused_results_does_not_say_ran():
    counts = {'tasks': 1, 'claimed': 1, 'tests': 1, 'cached': 1, 'tested_tasks': {'TASK-1'}}
    assert gate.check_summary(counts, run_tests=True) == (
        "checked 1 task (1 claimed DONE or CLOSED); reused 1 task's declared test result (cached, not run again)")


def two_failing_project_tests(root):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    (root / 'tests/test_project.py').write_text(
        'import app\n\ndef test_one():\n    assert app.value == 1\n\ndef test_two():\n    assert app.value == 1\n')
    (root / 'tests/test_own.py').write_text('import app\n\ndef test_own():\n    assert app.feature is True\n')
    fill_sections(task(root, ships='none', test='python -m pytest -q tests/test_own.py'))
    settings(root, 'python -m pytest -q tests/test_project.py')
    (root / 'app.py').write_text('value = 1\nfeature = False\n')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')
    (root / 'app.py').write_text('value = 0\nfeature = True\n')
    commit_all(root)


def test_not_ok_count_says_findings_were_grouped(local_repo):
    root = local_repo
    two_failing_project_tests(root)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    assert result.returncode == 1
    assert '2 project tests fail on this code that pass on origin/main: ' in result.stdout
    # Was "check: NOT OK (2 findings)" above one grouped line. Only the saved-output pointer follows it.
    lines = [line for line in result.stdout.splitlines() if not line.startswith('Full output: ')]
    assert lines[-1] == 'check: NOT OK (2 findings, 1 shown grouped)', result.stdout
    assert 'Note: the rerun on origin/main of the 2 project tests that fail on this code (2 passed' in result.stdout


# 6: show names the missing done run, and after a weak-test refusal, the test

def test_show_before_done_asks_for_a_done_run(local_repo):
    root = local_repo
    landed(root)
    output = ok(root, 'show', '--task', 'TASK-1')
    assert 'owes: a done run' in output.splitlines()[0]
    assert 'owes: implementation' not in output
    assert output.splitlines()[-1] == 'next step: run workorder done --task TASK-1', output


def test_show_after_a_weak_test_refusal_asks_for_a_better_test(local_repo):
    root = local_repo
    landed(root, test=WEAK)
    assert cli(root, 'done', '--task', 'TASK-1').returncode == 1
    output = ok(root, 'show', '--task', 'TASK-1')
    assert 'done refused this code: TASK-1: ' in output
    assert output.splitlines()[-1].startswith('next step: make the test show the change'), output
    assert 'finish the Acceptance items' not in output


# 7: no failure body for Note-only project failures (tests/test_v16c_project_output.py), long lines capped,
# the verdict last

def test_long_test_output_lines_are_capped_and_saved(local_repo, capsys):
    with console.session(local_repo) as run:
        console.test_output('x' * 1000 + '\nshort\n')
    shown = capsys.readouterr().out
    assert 'x' * 300 + ' [... 700 more characters in the full output]' in shown
    assert 'x' * 301 not in shown and 'short' in shown
    assert run.shortened is not None
    assert 'x' * 1000 in (local_repo / '.git/workorder/last-output.txt').read_text()


def test_check_ok_comes_after_the_notes(local_repo):
    root = local_repo
    landed(root)
    output = ok(root, 'check', '--base', 'origin/main', '--run-tests')
    lines = output.splitlines()
    assert lines[-1] == 'check: OK', output
    assert any(line.startswith('Note: ') for line in lines[:-1])


# 8: the quick start appends to .gitignore

def test_quick_start_appends_to_gitignore():
    readme = (PACKAGE / 'README.md').read_text(encoding='utf-8')
    assert "printf '__pycache__/\\n' >> .gitignore" in readme
    assert not re.search(r"[^>]> \.gitignore", readme)
    assert ("`done` runs the task's own test, and the tests of finished tasks whose code this change touches."
            in readme)
