"""The optional project command catches failures outside a task's declared test."""

import importlib
import json

import pytest

from workorder import store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections


UNSET = ("only the declared tests ran; the project's other tests did not run "
         "(set a project test with workorder init --project-test)")
FAILURE = ('the project test fails on this code: tests/test_project.py::test_project '
           '(passes on origin/main)')


def settings(root, command):
    folder = root / '.workorder'
    folder.mkdir(exist_ok=True)
    (folder / 'config.json').write_text(json.dumps({'project_test': command}) + '\n')


def project(root, *, configured=True, broken_base=False, broken_head=True, with_task=False,
            python_flags='', parameter_id=None):
    """An unchanged project test plus a task test made red-to-green by the code."""
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests').mkdir()
    if python_flags:
        (root / 'pytest.ini').write_text('[pytest]\npythonpath = .\n')
    log = root.parent / 'project-runs.log'
    (root / 'app.py').write_text(f'value = {0 if broken_base else 1}\nfeature = False\n')
    declaration = ('def test_project():\n' if parameter_id is None else
                   f'@pytest.mark.parametrize("value", [1], ids=[{parameter_id!r}])\n'
                   'def test_project(value):\n')
    (root / 'tests/test_project.py').write_text(
        'from pathlib import Path\nimport app, pytest\n\n'
        'def record(name):\n'
        f'    with Path({str(log)!r}).open("a") as output:\n'
        '        output.write(name + ":" + str(app.feature) + "\\n")\n\n'
        + declaration + '    record("project")\n    assert app.value == 1\n\n'
        'def test_unrelated():\n    record("unrelated")\n    assert len([1]) == 1\n')
    if configured:
        settings(root, f'python {python_flags} -m pytest -q tests/test_project.py')
    if with_task:
        (root / 'tests/test_own.py').write_text(
            'import app\n\ndef test_own():\n    assert app.feature is True\n')
        fill_sections(task(root, ships='none', test='python -m pytest -q tests/test_own.py'))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')
    (root / 'app.py').write_text(f'value = {0 if broken_head else 1}\nfeature = True\n')
    commit_all(root)
    return log


def test_init_persists_project_command_and_keeps_it_on_later_init(local_repo):
    root = local_repo
    command = 'python -m pytest -q tests'
    result = cli(root, 'init', '--project-test', command)
    assert result.returncode == 0, result.stdout + result.stderr
    path = root / '.workorder/config.json'
    assert json.loads(path.read_text())['project_test'] == command
    ok(root, 'init')
    assert json.loads(path.read_text())['project_test'] == command
    assert '.workorder/config.json' in git(root, 'status', '--porcelain', '--untracked-files=all')


def test_init_check_does_not_write_project_config(local_repo):
    root = local_repo
    ok(root, 'init')
    result = cli(root, 'init', '--check', '--project-test', 'python -m pytest')
    assert result.returncode == 1, result.stdout + result.stderr
    assert not (root / '.workorder/config.json').exists()


def test_check_refuses_a_new_project_failure_and_runs_only_failure_on_base(local_repo):
    root = local_repo
    log = project(root)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert FAILURE in output
    assert 'workorder-allow:tests' in output
    assert log.read_text().splitlines() == ['project:True', 'unrelated:True', 'project:False']


def test_preexisting_project_failure_is_a_note_with_no_unrelated_base_run(local_repo):
    root = local_repo
    log = project(root, broken_base=True)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert any(line.startswith('Note:') and 'test_project' in line and 'origin/main' in line
               for line in output.splitlines()), output
    assert log.read_text().splitlines() == ['project:True', 'unrelated:True', 'project:False']


@pytest.mark.parametrize('flags', ['', '-I'])
def test_preexisting_parameter_id_with_separator_is_rerun_exactly(local_repo, flags):
    root = local_repo
    log = project(root, broken_base=True, python_flags=flags, parameter_id='a - b')
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert ('Note: the project test also fails on origin/main: '
            'tests/test_project.py::test_project[a - b]') in output
    assert log.read_text().splitlines() == ['project:True', 'unrelated:True', 'project:False']
    assert git(root, 'status', '--porcelain', '--untracked-files=all') == ''


@pytest.mark.parametrize('allowance', [('--allow', 'tests'), ('--approving-review',)])
def test_project_failure_can_be_accepted_by_pr_review(local_repo, allowance):
    root = local_repo
    project(root)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests', *allowance)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert FAILURE in output


def test_review_warn_does_not_accept_a_current_project_failure(local_repo):
    root = local_repo
    project(root)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--review', 'warn')
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert FAILURE in output


@pytest.mark.parametrize('flags', ['-I', '-E', '-I -E'])
def test_project_failure_comparison_preserves_python_isolation(local_repo, flags):
    root = local_repo
    log = project(root, python_flags=flags)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    output = result.stdout + result.stderr
    assert result.returncode == 1 and FAILURE in output, output
    assert log.read_text().splitlines() == ['project:True', 'unrelated:True', 'project:False']


def test_identical_task_and_project_command_runs_project_once_after_own_test(local_repo):
    root = local_repo
    project(root, with_task=True, broken_head=False)
    log = root.parent / 'same-command.log'
    own = root / 'tests/test_own.py'
    own.write_text('from pathlib import Path\nimport app, sys\n\n'
                   'def test_own():\n'
                   '    phase = "project" if "-rfE" in sys.argv else "own"\n'
                   f'    with Path({str(log)!r}).open("a") as output:\n'
                   '        output.write(phase + ":" + str(app.feature) + "\\n")\n'
                   '    assert app.feature is True\n')
    settings(root, 'python -m pytest -q tests/test_own.py')
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    assert store.record(root, 'TASK-1')['status'] == 'DONE', output
    lines = log.read_text().splitlines()
    assert lines[:2] == ['own:True', 'project:True'], lines
    assert lines.count('project:True') == 1, lines


def test_done_refuses_project_regression_after_own_test_and_rolls_back(local_repo):
    root = local_repo
    log = project(root, with_task=True)
    result = cli(root, 'done', '--task', 'TASK-1')
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert FAILURE in output
    assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'
    assert store.entries(root, 'TASK-1') == []
    assert log.read_text().splitlines() == ['project:True', 'unrelated:True', 'project:False']


def test_done_runs_a_passing_project_command_once(local_repo):
    root = local_repo
    log = project(root, with_task=True, broken_head=False)
    output = ok(root, 'done', '--task', 'TASK-1')
    assert store.record(root, 'TASK-1')['status'] == 'DONE', output
    assert log.exists(), output
    assert log.read_text().splitlines() == ['project:True', 'unrelated:True']


@pytest.mark.parametrize('action', ['done', 'check'])
def test_unset_project_command_explains_test_scope(local_repo, action):
    root = local_repo
    project(root, with_task=True, configured=False, broken_head=False)
    flags = ('done', '--task', 'TASK-1') if action == 'done' else (
        'check', '--base', 'origin/main', '--run-tests')
    output = ok(root, *flags)
    assert UNSET in output


@pytest.mark.parametrize('flags', [(), ('--staged',), ('--staged', '--run-tests')])
def test_metadata_checks_and_precommit_do_not_run_project_command(local_repo, flags):
    root = local_repo
    log = project(root)
    result = cli(root, 'check', *flags)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not log.exists()


def test_opaque_failure_is_not_reclassified_by_rerunning_whole_base(local_repo):
    root = local_repo
    log = root.parent / 'opaque-runs.log'
    script = ('from pathlib import Path\n'
              f'with Path({str(log)!r}).open("a") as output: output.write("ran\\n")\n'
              'raise SystemExit(1)\n')
    (root / 'suite.py').write_text(script)
    settings(root, 'python suite.py')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert 'could not identify failing tests' in output
    assert log.read_text().splitlines() == ['ran']


def test_project_helpers_preserve_other_settings_and_reject_invalid_commands(local_repo):
    helper = importlib.import_module('workorder.project_tests')
    root = local_repo
    folder = root / '.workorder'
    folder.mkdir()
    path = folder / 'config.json'
    path.write_text(json.dumps({'other': {'kept': True}}))
    helper.configure(root, 'python -m pytest -q')
    assert json.loads(path.read_text()) == {'other': {'kept': True}, 'project_test': 'python -m pytest -q'}
    assert helper.command(root) == ['python', '-m', 'pytest', '-q']
    for invalid in ('', '   ', 'pytest && echo hidden'):
        with pytest.raises(store.Refused):
            helper.configure(root, invalid)
    assert helper.command(root) == ['python', '-m', 'pytest', '-q']


def test_missing_base_node_refuses_without_running_unrelated_base_tests(local_repo):
    root = local_repo
    log = project(root)
    path = root / 'tests/test_project.py'
    path.write_text(path.read_text() + '\ndef test_added():\n    assert app.value == 2\n')
    commit_all(root)
    helper = importlib.import_module('workorder.project_tests')
    notes = []
    problems = helper.run(root, base='origin/main', notes=notes)
    assert problems
    assert 'could not compare' in '\n'.join(problems)
    assert all(line.endswith(':True') for line in log.read_text().splitlines())


def test_project_base_selection_ignores_extra_paths_from_pytest_addopts(local_repo):
    root = local_repo
    log = project(root)
    (root / 'pytest.ini').write_text('[pytest]\naddopts = tests/test_project.py\n')
    commit_all(root)
    helper = importlib.import_module('workorder.project_tests')
    # Carry the same configuration to the base to exercise hidden collection paths.
    git(root, 'checkout', '-q', 'origin/main')
    (root / 'pytest.ini').write_text('[pytest]\naddopts = tests/test_project.py\n')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'checkout', '-q', 'feature')
    problems = helper.run(root, base='origin/main')
    assert problems and FAILURE in '\n'.join(problems)
    assert log.read_text().splitlines().count('unrelated:False') == 0


def test_project_base_classifies_each_failure_separately(local_repo):
    root = local_repo
    project(root, broken_base=True)
    # The first failure predates this change; only the second is new.
    git(root, 'checkout', '-q', 'origin/main')
    path = root / 'tests/test_project.py'
    added = '\ndef test_regression():\n    assert app.feature is False\n'
    path.write_text(path.read_text() + added)
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'checkout', '-q', 'feature')
    path.write_text(path.read_text() + added)
    commit_all(root)
    helper = importlib.import_module('workorder.project_tests')
    notes = []
    problems = helper.run(root, base='origin/main', notes=notes)
    assert len(problems) == 1 and '::test_regression (passes on origin/main)' in problems[0]
    assert len(notes) == 2
    assert notes[0].startswith('the project test on origin/main (1 failed, 1 passed in ')
    assert '::test_project' in notes[1] and 'origin/main' in notes[1]


@pytest.mark.parametrize('payload', ['{', '[]', '{"project_test": []}', '{"project_test": ""}'])
def test_malformed_project_configuration_fails_closed(local_repo, payload):
    root = local_repo
    folder = root / '.workorder'
    folder.mkdir()
    (folder / 'config.json').write_text(payload)
    result = cli(root, 'check', '--run-tests')
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert '.workorder/config.json' in output
