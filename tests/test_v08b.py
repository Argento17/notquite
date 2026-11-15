"""Round-10 regressions: real Git snapshots and offline test commands."""

import io
import os
import subprocess
import sys

import pytest

from workorder import bootstrap, gate, hooks, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, task
from test_v05 import commit_all
from test_v07b_comparison import setup_tests
from test_v07b_setup import local_claim, refuse_network, workflow_commands


def local_change(root, kind):
    (root / 'app.py').write_text('value = 1\n')
    body = 'from app import value\ndef test_one():\n    assert value == 1\n\ndef test_two():\n    assert int(2) == 2\n'
    probe, _ = setup_tests(root, body)
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text() + '\n## Scope\n\nCalculate.\n\n## Acceptance\n\n- Correct result.\n')
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-q', '-b', 'feature')
    if kind == 'ref':
        (root / 'app.py').write_text('value = 200\n')
        probe.write_text(body.replace('== 1', '== 200'))
    elif kind == 'fewer':
        probe.write_text(body.split('\ndef test_two')[0])
    elif kind == 'skip':
        probe.write_text('import pytest\n' + body.replace('def test_two', '@pytest.mark.skip(reason="later")\ndef test_two'))
    elif kind == 'config':
        (root / 'pytest.ini').write_text('[pytest]\naddopts = -q\n')
    else:
        probe.write_text(body.replace('assert int(2) == 2', 'assert int(1) == 1'))
    commit_all(root)
    return path, base


@pytest.mark.parametrize('action', ['done', pytest.param('pre-commit', marks=pytest.mark.skipif(
    os.name == 'nt', reason='executes a POSIX pre-commit hook')), 'stop'])
@pytest.mark.parametrize('kind,reason', [('ref', 'fail on this code'), ('fewer', 'fewer tests pass'),
                                       ('skip', 'new skip or xfail'), ('config', 'configuration changed'),
                                       ('lines', 'lost or changed')])
def test_local_commands_review_test_changes(local_repo, monkeypatch, capsys, action, kind, reason):
    root = local_repo
    if action == 'pre-commit':
        bootstrap.initialize(root)
    path, _ = local_change(root, kind)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-1')
        code, output = result.returncode, result.stdout + result.stderr
        assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'
    else:
        store.receipt(root, 'TASK-1', 'implemented')
        store.receipt(root, 'TASK-1', 'test', [PYTHON, '-m', 'pytest', '-q', 'tests'])
        store.set_task_field(root, 'TASK-1', 'status', 'DONE')
        if action == 'pre-commit':
            git(root, 'add', '-A')
            env = dict(os.environ, PYTHONPATH=str(PACKAGE),
                       PATH=str(os.path.dirname(PYTHON)) + os.pathsep + os.environ['PATH'])
            result = subprocess.run([str(root / '.git/hooks/pre-commit')], cwd=root, env=env,
                                    capture_output=True, text=True)
            code, output = result.returncode, result.stdout + result.stderr
        else:
            local_claim(root, 'TASK-1')
            refuse_network(monkeypatch)
            monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
            code = hooks.run(root, 'stop')
            captured = capsys.readouterr()
            output = captured.out + captured.err
        assert store.record(root, 'TASK-1')['status'] == 'DONE'
    assert code == (1 if action == 'done' else 0), output
    if action != 'done':
        assert 'Note:' in output and 'NOT DONE' not in output
    assert reason in output
    assert 'a reviewer accepts it with the label workorder-allow:TASK-1 (or an approving review)' in output


def test_precommit_compares_index_not_working_tests(local_repo):
    root = local_repo
    probe, base = setup_tests(root, 'def test_one():\n    assert True\n')
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    probe.write_text('def test_one():\n    assert 1 == 1\n')
    store.add_note(root, 'TASK-1', 'Review')
    git(root, 'add', '-A')
    probe.write_text('def test_one():\n    assert False\n')
    result = cli(root, 'check', '--staged')
    assert result.returncode == 0
    assert 'lost or changed' in result.stdout and 'workorder-allow:TASK-1' in result.stdout
    assert 'its test failed' not in result.stdout


def test_ref_failure_already_present_is_not_blame_and_is_cached(local_repo, monkeypatch):
    root = local_repo
    (root / 'app.py').write_text('value = 0\n')
    probe, _ = setup_tests(root, 'from app import value\ndef test_one():\n    assert value == 1\n')
    task(root, 'TASK-2', ships='none', test=f'{PYTHON} -m pytest -q tests')
    base = commit_all(root)
    (root / 'app.py').write_text('value = 200\n')
    probe.write_text('from app import value\ndef test_one():\n    assert value == 200\n')
    for tid in ('TASK-1', 'TASK-2'):
        store.add_note(root, tid, 'Implementing')
    real_run = gate._run_test
    base_runs = []

    def observe(directory, *args, **kwargs):
        if (directory / 'app.py').read_text() == 'value = 0\n':
            base_runs.append(directory)
        return real_run(directory, *args, **kwargs)

    monkeypatch.setattr(gate, '_run_test', observe)
    notes = []
    problems = gate.check(root, base=base, run_tests=True, notes=notes, allow={'TASK-1', 'TASK-2'})
    assert not problems, problems
    assert not any('fail on this code' in note for note in notes)
    assert sum('already fail on' in note for note in notes) == 2, notes
    assert len(base_runs) == 1


@pytest.mark.parametrize('selection', ['tests/test_app.py', '', '-c pytest.ini'])
@pytest.mark.parametrize('helper', ['helper.py', '__init__.py', 'data/sample.txt'])
def test_implicit_test_tree_guards_all_files(local_repo, selection, helper):
    root = local_repo
    _, _ = setup_tests(root, 'def test_one():\n    assert True\n', f'{PYTHON} -m pytest -q {selection}')
    (root / 'pytest.ini').write_text('[pytest]\ntestpaths = tests\n')
    path = root / 'tests' / helper
    path.parent.mkdir(exist_ok=True)
    path.write_text('old = 1\n')
    base = commit_all(root)
    path.write_text('new = 2\n')
    store.add_note(root, 'TASK-1', 'Working')
    result = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert result.returncode == 1 and path.relative_to(root).as_posix() in result.stdout
    # A named file, a pathless pytest command, and -c do not own every file under tests/.
    assert 'workorder-allow:tests' in result.stdout


def test_import_outside_test_roots_does_not_make_a_package_a_test_file(local_repo):
    root = local_repo
    setup_tests(root, 'from checks import helper\ndef test_one():\n    assert helper.value\n',
                f'{PYTHON} -m pytest -q tests/test_app.py')
    (root / 'checks').mkdir()
    helper = root / 'checks/helper.py'
    helper.write_text('value = 1\n')
    (root / 'checks/__init__.py').write_text('')
    other = root / 'checks/test_other.py'
    other.write_text('def test_other():\n    pass\n')
    base = commit_all(root)
    helper.write_text('value = 2\n')
    store.add_note(root, 'TASK-1', 'Working')
    result = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'checks/helper.py' not in result.stdout
    helper.write_text('value = 1\n')
    other.write_text('def test_other():\n    assert True\n')
    refused = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert refused.returncode == 1 and 'checks/test_other.py' in refused.stdout


@pytest.mark.parametrize('command,script', [('sh run.sh', 'run.sh'), ('bash run.sh', 'run.sh'),
                                         (f'{PYTHON} runner.py', 'runner.py'), ('make test', 'Makefile')])
def test_wrapper_note_and_script_protection(local_repo, command, script):
    root = local_repo
    task(root, ships='none', test=command)
    (root / script).write_text('# before\n')
    base = commit_all(root)
    (root / script).write_text('# after\n')
    store.add_note(root, 'TASK-1', 'Working')
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1 and script in result.stdout
    assert 'TASK-1 runs a wrapper script; set test_paths so changes to its tests are reviewed' in result.stdout


@pytest.mark.parametrize('before,after', [('DONE', 'IN_PROGRESS'), ('DONE', 'BLOCKED'),
    ('CLOSED', 'IN_PROGRESS'), ('CLOSED', 'BLOCKED'), ('DONE', 'CANCELLED'),
    ('CLOSED', 'CANCELLED'), ('IN_PROGRESS', 'CANCELLED'), ('BLOCKED', 'CANCELLED')])
def test_cancellation_needs_review_but_reopening_is_honest_work(local_repo, before, after):
    root = local_repo
    task(root, ships='none', status=before)
    base = commit_all(root)
    store.set_task_field(root, 'TASK-1', 'status', after)
    result = cli(root, 'check', '--base', base)
    if after != 'CANCELLED':
        assert result.returncode == 0, result.stdout
        assert 'status changed:' not in result.stdout
        return
    assert result.returncode == 1 and 'workorder-allow:TASK-1' in result.stdout
    assert f'status changed: {before} -> {after}' in result.stdout
    if after == 'CANCELLED':
        assert 'a reviewer accepts it with the label workorder-allow:TASK-1 (or an approving review)' in result.stdout
    assert cli(root, 'check', '--base', base, '--allow', 'TASK-1').returncode == 0


@pytest.mark.parametrize('committed', [False, True])
def test_stop_refuses_new_cancellation_of_held_task(local_repo, monkeypatch, capsys, committed):
    root = local_repo
    task(root, ships='none')
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    local_claim(root, 'TASK-1')
    store.set_task_field(root, 'TASK-1', 'status', 'CANCELLED')
    if committed:
        commit_all(root)
    refuse_network(monkeypatch)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    assert hooks.run(root, 'stop') == 2
    output = capsys.readouterr().err
    assert 'CANCELLED' in output and 'workorder-allow:TASK-1' in output


@pytest.mark.skipif(os.name == 'nt', reason='executes the Linux workflow shell step')
def test_no_dependencies_workflow_warns_with_actionable_placeholder(local_repo):
    task(local_repo, test=f'{PYTHON} runner.py')
    bootstrap.initialize(local_repo)
    commands = workflow_commands(local_repo)
    placeholder = next((command for command in commands if 'add your test dependencies here' in command), '')
    assert placeholder
    result = subprocess.run(['/bin/sh', '-c', placeholder], capture_output=True, text=True)
    assert result.returncode == 0 and 'warning' in result.stdout + result.stderr
    assert 'add your test dependencies here' in result.stdout + result.stderr
    assert not any('install pytest' in command for command in commands)


@pytest.mark.parametrize('check_only', [False, True])
def test_update_workflow_shows_diff_and_check_does_not_write(local_repo, check_only):
    bootstrap.initialize(local_repo)
    path = local_repo / '.github/workflows/workorder.yml'
    original = path.read_bytes()
    task(local_repo, test=f'{PYTHON} -m pytest tests')
    flags = ['--check'] if check_only else []
    result = cli(local_repo, 'init', '--update-workflow', *flags)
    assert result.returncode == int(check_only), result.stderr
    assert '--- ' in result.stdout and '+++ ' in result.stdout
    assert '+        run: python -m pip install pytest' in result.stdout
    assert (path.read_bytes() == original) == check_only


@pytest.mark.parametrize('action', ['done', 'check'])
@pytest.mark.parametrize('command,missing', [(f'{PYTHON} -S -m pytest', 'pytest'),
                                           ('workorder-missing-test-tool', 'workorder-missing-test-tool')])
def test_missing_test_dependencies_are_named(local_repo, action, command, missing):
    root = local_repo
    path = task(root, ships='none', test=command)
    path.write_text(path.read_text() + '\n## Scope\n\nCalculate.\n\n## Acceptance\n\n- Correct.\n')
    base = commit_all(root)
    store.add_note(root, 'TASK-1', 'Working')
    args = ['done', '--no-code-change', '--task', 'TASK-1'] if action == 'done' else ['check', '--base', base, '--run-tests']
    result = cli(root, *args)
    assert result.returncode == (1 if action == 'done' else 0)
    if action == 'check':
        assert 'Note:' in result.stdout and 'not claimed finished' in result.stdout
    assert f'{missing} is not installed in this environment' in result.stdout + result.stderr
    assert 'weakened or broke' not in result.stdout


@pytest.mark.parametrize('verbose', [False, True])
def test_check_groups_notes_and_verbose_expands_them(local_repo, verbose):
    root = local_repo
    setup_tests(root, 'def test_one():\n    assert True\n')
    for tid in ('TASK-1', 'TASK-2', 'TASK-3'):
        task(root, tid, ships='none', test=f'{PYTHON} -m pytest -q tests')
    base = commit_all(root)
    for tid in ('TASK-1', 'TASK-2', 'TASK-3'):
        store.add_note(root, tid, 'Working')
    result = cli(root, 'check', '--base', base, '--run-tests', *(['--verbose'] if verbose else []))
    assert result.returncode == 0, result.stderr
    if verbose:
        assert sum('its test passed when this check ran it' in line for line in result.stdout.splitlines()) == 3
    else:
        assert "Note: 3 tasks' tests passed when this check ran them" in result.stdout
        assert 'its test passed when this check ran it' not in result.stdout


def test_done_explains_passing_but_void_run(local_repo):
    root = local_repo
    (root / 'runner.py').write_text('from pathlib import Path\nPath("__pycache__").mkdir(exist_ok=True)\n'
                                  'Path("__pycache__/generated.pyc").write_bytes(b"cache")\n')
    path = task(root, ships='none', test=f'{PYTHON} runner.py')
    path.write_text(path.read_text() + '\n## Scope\n\nCalculate.\n\n## Acceptance\n\n- Correct.\n')
    commit_all(root)
    result = cli(root, 'done', '--no-code-change', '--task', 'TASK-1')
    assert result.returncode == 1
    assert 'files changed while the command ran' in result.stdout
    assert '__pycache__' in result.stdout and '.gitignore' in result.stdout
    assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'


def test_ref_self_run_rebases_absolute_internal_symlinks(local_repo):
    root = local_repo
    target = root / 'saved.txt'
    target.write_text('keep the checkout unchanged\n')
    (root / 'alias.txt').symlink_to(target)
    probe, base = setup_tests(root, 'from pathlib import Path\n'
                             'Path("alias.txt").write_text("test write\\n")\nassert False\n',
                             f'{PYTHON} tests/test_app.py')
    probe.write_text('assert True\n')
    store.add_note(root, 'TASK-1', 'Working')
    result = cli(root, 'check', '--base', base, '--run-tests', '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'already fail on' in result.stdout
    assert target.read_text() == 'keep the checkout unchanged\n'


def test_direct_python_test_infers_its_helper_folder(local_repo):
    root = local_repo
    setup_tests(root, 'from helper import value\nassert value\n', f'{PYTHON} tests/test_app.py')
    helper = root / 'tests/helper.py'
    helper.write_text('value = 1\n')
    base = commit_all(root)
    helper.write_text('value = 2\n')
    store.add_note(root, 'TASK-1', 'Working')
    result = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert result.returncode == 1 and 'tests/helper.py' in result.stdout
    assert 'wrapper script' not in result.stdout


@pytest.mark.parametrize('action', ['done', 'check'])
def test_wrapper_advice_without_local_base(local_repo, action):
    root = local_repo
    (root / 'runner.py').write_text('pass\n')
    path = task(root, ships='none', test=f'{PYTHON} runner.py')
    path.write_text(path.read_text() + '\n## Scope\n\nCalculate.\n\n## Acceptance\n\n- Correct.\n')
    commit_all(root)
    result = cli(root, *(['done', '--no-code-change', '--task', 'TASK-1'] if action == 'done' else ['check']))
    assert result.returncode == 0
    assert 'TASK-1 runs a wrapper script; set test_paths so changes to its tests are reviewed' in result.stdout


@pytest.mark.parametrize('config', ['pytest.ini', 'pyproject.toml'])
def test_nested_config_testpaths_are_relative_to_config(local_repo, config):
    root = local_repo
    (root / 'component').mkdir()
    (root / 'component/checks').mkdir()
    (root / 'component/checks/helper.py').write_text('value = 1\n')
    (root / 'component/checks/cases').mkdir()
    (root / 'component/checks/cases/test_app.py').write_text('def test_one():\n    pass\n')
    body = ('[tool.pytest.ini_options]\ntestpaths = ["checks"]\n' if config.endswith('.toml')
            else '[pytest]\ntestpaths = checks\n')
    (root / 'component' / config).write_text(body)
    # No default-name test in extras: the configured root must include this helper too.
    (root / 'component/checks/extra.txt').write_text('old\n')
    task(root, ships='none', test=f'{PYTHON} -m pytest -c component/{config}')
    base = commit_all(root)
    (root / 'component/checks/extra.txt').write_text('new\n')
    store.add_note(root, 'TASK-1', 'Working')
    result = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert result.returncode == 1 and 'component/checks/extra.txt' in result.stdout


@pytest.mark.skipif(os.name == 'nt', reason='uses a POSIX virtual environment interpreter path')
def test_ref_self_run_uses_ignored_local_test_environment(local_repo):
    root = local_repo
    (root / '.git/info/exclude').write_text('.venv/\n')
    (root / '.venv/bin').mkdir(parents=True)
    interpreter = root / '.venv/bin/python'
    interpreter.symlink_to(PYTHON)
    probe, _ = setup_tests(root, 'assert False\n', '.venv/bin/python tests/test_app.py')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n.venv/\n')
    base = commit_all(root)
    probe.write_text('assert True\n')
    store.add_note(root, 'TASK-1', 'Working')
    result = cli(root, 'check', '--base', base, '--run-tests', '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'already fail on' in result.stdout
    assert interpreter.is_symlink()


def test_reviewed_module_rename_is_not_a_missing_dependency(local_repo):
    root = local_repo
    (root / 'old_api.py').write_text('value = 1\n')
    probe, base = setup_tests(root, 'from old_api import value\ndef test_one():\n    assert value == 1\n')
    (root / 'old_api.py').rename(root / 'new_api.py')
    probe.write_text(probe.read_text().replace('old_api', 'new_api'))
    store.add_note(root, 'TASK-1', 'Rename the module')
    result = cli(root, 'check', '--base', base, '--run-tests', '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'fail on this code' in result.stdout and '(allowed)' in result.stdout
    assert 'old_api is not installed in this environment' not in result.stdout


def test_pytest_ancestor_package_init_is_a_test_file(local_repo):
    root = local_repo
    setup_tests(root, '', f'{PYTHON} -m pytest tests/unit/test_app.py')
    (root / 'tests/unit').mkdir()
    (root / 'tests/unit/test_app.py').write_text('def test_one():\n    pass\n')
    (root / 'tests/unit/__init__.py').write_text('')
    parent = root / 'tests/__init__.py'
    parent.write_text('value = 1\n')
    base = commit_all(root)
    parent.write_text('value = 2\n')
    store.add_note(root, 'TASK-1', 'Working')
    result = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert result.returncode == 1 and 'tests/__init__.py' in result.stdout


def test_default_discovery_includes_helpers_above_nested_tests(local_repo):
    root = local_repo
    (root / 'tests/unit').mkdir(parents=True)
    (root / 'tests/unit/test_app.py').write_text('def test_one():\n    pass\n')
    helper = root / 'tests/helper.py'
    helper.write_text('value = 1\n')
    task(root, ships='none', test=f'{PYTHON} -m pytest -q')
    base = commit_all(root)
    helper.write_text('value = 2\n')
    store.add_note(root, 'TASK-1', 'Working')
    result = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert result.returncode == 1 and 'tests/helper.py' in result.stdout


def test_named_repository_root_does_not_treat_task_records_as_tests(local_repo):
    root = local_repo
    _, base = setup_tests(root, 'def test_one():\n    pass\n', f'{PYTHON} -m pytest -q .')
    store.set_task_field(root, 'TASK-1', 'status', 'BLOCKED')
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'test files lost or changed' not in result.stdout
