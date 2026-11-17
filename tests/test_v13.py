"""Round-15 regressions. Every repository and service response is local."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from workorder import bootstrap, gate, hooks, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v09_setup import check_steps


MUL = 'def mul(a, b):\n    return a * b\n'
SUB = '\ndef sub(a, b):\n    return a + b\n'


def subtraction_task(root, test_exists=False):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'calc.py').write_text(MUL + SUB)
    if test_exists:
        (root / 'test_sub.py').write_text('from calc import sub\nassert sub is not None\n')
    fill_sections(task(root, 'TASK-2', ships='none',
                       test=store.join_command([PYTHON, 'test_sub.py'])))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    return base


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('action', ['done', 'base'])
@pytest.mark.parametrize('target', ['origin/main', 'main'])
def test_vacuous_test_refuses_completion(local_repo, existing, action, target):
    root = local_repo
    subtraction_task(root, existing)
    if target == 'main':
        git(root, 'update-ref', '-d', 'refs/remotes/origin/main')
    (root / 'calc.py').write_text(MUL + SUB + 'pass\n')
    (root / 'test_sub.py').write_text('from calc import sub\nassert sub is not None\n')
    commit_all(root)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-2')
        assert store.record(root, 'TASK-2')['status'] == 'IN_PROGRESS', result.stdout
    else:
        ok(root, 'receipt', 'implemented', '--task', 'TASK-2')
        ok(root, 'receipt', 'test', '--task', 'TASK-2')
        store.set_task_field(root, 'TASK-2', 'status', 'DONE')
        result = cli(root, 'check', '--base', target, '--run-tests', '--all-tests', '--require-task')
    assert result.returncode == 1, result.stdout + result.stderr
    assert (f"TASK-2: its test passes on {target}'s code too, so it does not show the change works; "
            'make the test check the new behaviour') in result.stdout
    if action == 'base':
        ok(root, 'check', '--base', target, '--run-tests', '--allow', 'TASK-2')
        still_refused = cli(root, 'check', '--base', target, '--run-tests', '--allow', 'tests')
        assert still_refused.returncode == 1


def test_honest_new_test_runs_on_base_and_current_without_label(local_repo, monkeypatch):
    root = local_repo
    base = subtraction_task(root)
    (root / 'calc.py').write_text(MUL + SUB.replace('a + b', 'a - b'))
    (root / 'test_sub.py').write_text('from calc import sub\nassert sub(6, 3) == 3\n')
    commit_all(root)
    ok(root, 'receipt', 'implemented', '--task', 'TASK-2')
    ok(root, 'receipt', 'test', '--task', 'TASK-2')
    store.set_task_field(root, 'TASK-2', 'status', 'DONE')
    runs = []
    original = store.run_test

    def observe(command, cwd, *args, **kwargs):
        result = original(command, cwd, *args, **kwargs)
        runs.append(((cwd / 'calc.py').read_text(), (cwd / 'test_sub.py').read_text(), result.returncode))
        return result

    monkeypatch.setattr(store, 'run_test', observe)
    problems = gate.check(root, base='origin/main', run_tests=True)
    assert problems == []
    assert any(code == MUL + SUB and status != 0 for code, test, status in runs), runs
    assert any('a - b' in code and status == 0 for code, test, status in runs), runs
    assert 'TASK-2 DONE:' in ok(root, 'done', '--task', 'TASK-2')
    ok(root, 'check', '--base', base, '--run-tests', '--all-tests', '--require-task')


def test_verification_exemption_survives_later_base_check(local_repo):
    root = local_repo
    subtraction_task(root, True)
    output = ok(root, 'done', '--task', 'TASK-2', '--no-code-change')
    assert 'TASK-2 DONE:' in output
    receipt = store.entries(root, 'TASK-2')[-1]
    assert receipt.get('no_code_change') is True
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests')
    assert result.returncode == 1 and 'verification completion needs review' in result.stdout
    ok(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests', '--allow', 'TASK-2')


def finished_multiplication(root):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'calc.py').write_text(MUL)
    (root / 'test_mul.py').write_text('from calc import mul\nassert mul(3, 4) == 12\n')
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, 'test_mul.py'])))
    fill_sections(task(root, 'TASK-2', ships='none', test=store.join_command([PYTHON, 'test_sub.py'])))
    commit_all(root)
    # Existing 0.12 receipts must keep working, with or without path metadata.
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    ok(root, 'receipt', 'test', '--task', 'TASK-1')
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    (root / 'calc.py').write_text(MUL.replace('a * b', 'a + b') + SUB.replace('a + b', 'a - b'))
    (root / 'test_sub.py').write_text('from calc import sub\nassert sub(6, 3) == 3\n')
    commit_all(root)


@pytest.mark.parametrize('action', ['done', 'staged'])
def test_local_completion_refuses_other_finished_task_regression(local_repo, action):
    root = local_repo
    finished_multiplication(root)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-2')
        assert store.record(root, 'TASK-2')['status'] == 'IN_PROGRESS', result.stdout
    else:
        ok(root, 'receipt', 'implemented', '--task', 'TASK-2')
        ok(root, 'receipt', 'test', '--task', 'TASK-2')
        store.set_task_field(root, 'TASK-2', 'status', 'DONE')
        git(root, 'add', 'tasks', '.workorder')
        result = cli(root, 'check', '--staged')
    assert result.returncode == 1, result.stdout + result.stderr
    expected = ('1 other task has findings (TASK-1)' if action == 'done'
                else "regression: TASK-1's test fails on this code")
    assert expected in result.stdout


def test_done_reports_missing_target_once(local_repo):
    root = local_repo
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, '-c', 'pass'])))
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    assert output.count('could not compare with the target: fetch origin first') == 1


@pytest.mark.parametrize('path', ['api/latest/pricing.py', 'contest/app.py', 'fixture_factory/app.py'])
def test_production_folder_names_require_task_evidence(local_repo, path):
    root = local_repo
    base = store.head(root)
    file = root / path
    file.parent.mkdir(parents=True)
    file.write_text('price = 12\n')
    result = cli(root, 'check', '--base', base, '--require-task')
    assert result.returncode == 1, result.stdout
    assert 'no task records it' in result.stdout
    assert not gate.looks_like_test(path)


@pytest.mark.parametrize('folder', ['test', 'tests', 'testing', '__tests__', 'spec', 'specs', 'fixtures',
                                    'testdata', 'test_data', 'testsupport', 'test_support', 'golden',
                                    '__snapshots__', 'test_helpers', 'unit_test', 'unit_tests'])
def test_exact_test_folders_keep_review(local_repo, folder):
    root = local_repo
    file = root / folder / 'data.json'
    file.parent.mkdir()
    file.write_text('{"answer": 1}\n')
    base = commit_all(root)
    file.write_text('{"answer": 2}\n')
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1, result.stdout
    assert 'lost or changed' in result.stdout


@pytest.mark.parametrize('operation', ['fingerprint', 'copy'])
def test_staged_files_use_bounded_git_processes(local_repo, tmp_path, monkeypatch, operation):
    root = local_repo
    for number in range(2000):
        (root / f'file{number}.py').write_text(f'value = {number}\n')
    commit_all(root)
    # Make the working tree exactly what a checkout writes (core.autocrlf on
    # Windows runners), so the staged and working fingerprints describe the same bytes.
    # Git skips stat-clean files even with -f, so remove them first.
    for path in git(root, 'ls-files').splitlines():
        (root / path).unlink()
    git(root, 'checkout-index', '-a')
    calls = []
    original = subprocess.Popen

    def counted(command, *args, **kwargs):
        if command[0] == 'git':
            calls.append(command)
        return original(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, 'Popen', counted)
    with store.read_session():
        if operation == 'fingerprint':
            result = gate.snapshot_fingerprint(root, staged=True)
            assert result == store._test_state(root)[1]
        else:
            target = tmp_path / 'copy'
            target.mkdir()
            gate._copy_selected_files(root, target, staged=True)
            assert (target / 'file1999.py').read_text() == 'value = 1999\n'
    assert len(calls) <= 12, f'{len(calls)} Git processes for 2,000 tracked files'


@pytest.mark.parametrize('action', ['check', 'explain', 'stop'])
def test_refusal_uses_plain_words_and_preserves_exit(local_repo, monkeypatch, capsys, action):
    root = local_repo
    task(root, status='DONE')
    if action == 'stop':
        monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
        assert hooks.run(root, 'stop') == 2
        output = capsys.readouterr().err
    else:
        result = cli(root, 'check', *(['TASK-1'] if action == 'explain' else []))
        assert result.returncode == 1
        output = result.stdout
    assert 'NOT DONE' in output
    assert 'NOT QUITE' not in output


def test_init_light_writes_trial_options_and_default_restoration(local_repo):
    root = local_repo
    result = cli(root, 'init', '--light')
    assert result.returncode == 0, result.stdout + result.stderr
    path = root / '.github/workflows/workorder.yml'
    generated = path.read_text()
    pull = next(command for step, command in check_steps(generated) if '--base' in command)
    assert '--review warn' in pull
    assert '--require-task' not in pull
    assert 'workorder init --update-workflow' in generated
    before = path.read_bytes()
    ok(root, 'init', '--light', '--check', '--update-workflow')
    assert path.read_bytes() == before
    ok(root, 'init', '--update-workflow')
    pull = next(command for step, command in check_steps(path.read_text()) if '--base' in command)
    assert '--require-task' in pull and '--review warn' not in pull.splitlines()[-1]


@pytest.mark.skipif(os.name == 'nt', reason='executes the generated POSIX workflow')
def test_generated_workflow_ignores_author_labels(local_repo, tmp_path):
    root = local_repo
    bootstrap.initialize(root)
    generated = (root / '.github/workflows/workorder.yml').read_text()
    command = next(command for step, command in check_steps(generated) if '--base' in command)
    fixture = Path(__file__).parent / 'fixtures/label-events.json'
    data = json.loads(fixture.read_text())
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'pull_request': data['pull_request']}))
    binary = tmp_path / 'bin'
    binary.mkdir()
    # All service traffic is replaced by the recorded pages. Unexpected API calls fail.
    gh = binary / 'gh'
    gh.write_text(f'#!{PYTHON}\nimport json, sys\n'
                  'assert sys.argv[1:4] == ["api", "--paginate", "--slurp"], sys.argv\n'
                  'assert sys.argv[4] in ["repos/example/project/issues/7/events", '
                  '"repos/example/project/pulls/7/reviews"], sys.argv\n'
                  f'print(json.dumps(json.load(open({str(fixture)!r}))["pages"] '
                  'if sys.argv[4].endswith("events") else [[]]))\n')
    gh.chmod(0o755)
    runner = binary / 'workorder'
    runner.write_text(f'#!{PYTHON}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
    runner.chmod(0o755)
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + str(Path(PYTHON).parent) + os.pathsep + os.environ['PATH'],
           'PYTHONPATH': str(PACKAGE), 'GITHUB_EVENT_PATH': str(event), 'RUNNER_TEMP': str(tmp_path),
           'GH_REPO': 'example/project', 'PR_NUMBER': '7', 'BASE': 'origin/main'}
    result = subprocess.run(['/bin/sh', '-eu', '-c', command], cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    args = json.loads(result.stdout)
    assert args == ['check', '--base', 'origin/main', '--run-tests', '--all-tests', '--require-task',
                    '--allow', 'TASK-3,tests'], args
    assert 'ignored workorder-allow:TASK-2: added by the pull request author' in result.stderr
    assert 'ignored workorder-allow:no-task: added by the pull request author' in result.stderr


def test_label_filter_on_recorded_events():
    from workorder import review_labels
    fixture = json.loads((Path(__file__).parent / 'fixtures/label-events.json').read_text())
    allowed, notices = review_labels.accepted_labels(fixture['pull_request'], fixture['pages'])
    assert allowed == ['TASK-3', 'tests']
    assert len(notices) == 2


def test_vacuous_test_cannot_use_an_old_shared_helper_as_its_red_result(local_repo):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'calc.py').write_text(SUB)
    (root / 'tests').mkdir()
    (root / 'tests/helpers.py').write_text('# Shared test helpers\n')
    fill_sections(task(root, 'TASK-2', ships='none',
                       test=store.join_command([PYTHON, '-m', 'pytest', '-q', 'tests/test_sub.py'])))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    (root / 'calc.py').write_text(SUB + '# TODO pow\n')
    (root / 'tests/helpers.py').write_text('# Shared test helpers\ndef check_sub(sub):\n    assert sub is not None\n')
    (root / 'tests/test_sub.py').write_text(
        'from helpers import check_sub\nfrom calc import sub\ndef test_sub():\n    check_sub(sub)\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-2')
    assert result.returncode == 1, result.stdout + result.stderr
    assert "its new or changed tests (test_sub) pass on origin/main's code too" in result.stdout


def test_new_namespace_package_is_a_code_failure_on_base(local_repo):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, 'test_feature.py'])))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    (root / 'newpkg').mkdir()
    (root / 'newpkg/feature.py').write_text('def sub(a, b):\n    return a - b\n')
    (root / 'test_feature.py').write_text('from newpkg.feature import sub\nassert sub(6, 3) == 3\n')
    commit_all(root)
    assert 'TASK-1 DONE:' in ok(root, 'done', '--task', 'TASK-1')
    ok(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests')


def test_staged_copy_includes_skip_worktree_files(local_repo, tmp_path):
    root = local_repo
    (root / 'sparse.py').write_text('value = 12\n')
    commit_all(root)
    git(root, 'update-index', '--skip-worktree', 'sparse.py')
    (root / 'sparse.py').unlink()
    destination = tmp_path / 'copy'
    destination.mkdir()
    gate._copy_selected_files(root, destination, staged=True)
    assert (destination / 'sparse.py').read_text() == 'value = 12\n'
    assert gate.snapshot_fingerprint(root, staged=True)


def test_done_reviews_changed_tests_of_other_finished_tasks(local_repo):
    root = local_repo
    finished_multiplication(root)
    (root / 'test_mul.py').write_text('from calc import mul\nassert mul is not None\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-2')
    assert result.returncode == 1, result.stdout + result.stderr
    assert '1 other task has findings (TASK-1)' in result.stdout
    assert store.record(root, 'TASK-2')['status'] == 'IN_PROGRESS'
    output = ok(root, 'done', '--task', 'TASK-2', '--needs-review')
    assert 'pending review: the pull request needs workorder-allow:TASK-2' in output
    assert '1 other task has findings (TASK-1)' in output
    ok(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests', '--allow', 'TASK-1,TASK-2')


def test_done_reviews_changed_shared_expected_data(local_repo):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'app.py').write_text('value = 1\n')
    (root / 'expected.json').write_text('1\n')
    (root / 'test_app.py').write_text(
        'from pathlib import Path\nfrom app import value\nassert value == int(Path("expected.json").read_text())\n')
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, 'test_app.py'])))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    (root / 'app.py').write_text('value = 2\n')
    (root / 'expected.json').write_text('2\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'tests: its test files lost or changed' in result.stdout
    assert 'workorder-allow:tests' in result.stdout
    output = ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    assert 'pending review: the pull request needs workorder-allow:TASK-1' in output
    assert 'workorder-allow:tests' in output
    ok(root, 'check', '--base', 'origin/main', '--run-tests', '--allow', 'tests,TASK-1')
