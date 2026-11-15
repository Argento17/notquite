"""Second-pass regressions; Git and recorded service responses stay local."""

import io
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from workorder import bootstrap, gate, hooks, review_labels, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v09_setup import check_steps
from test_v11b import parallel_pull_requests


@pytest.mark.parametrize('action', ['done', 'check', 'staged', 'stop', 'run'])
@pytest.mark.parametrize('outcome', ['skip', 'xfail'])
def test_skipped_run_never_supports_done(local_repo, monkeypatch, capsys, action, outcome):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'test_app.py').write_text(f'import pytest\n@pytest.mark.{outcome}(reason="later")\ndef test_app(): assert False\n')
    command = store.join_command([PYTHON, '-m', 'pytest', '-q', 'test_app.py'])
    fill_sections(task(root, ships='none', test=command))
    base = commit_all(root)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-1', '--no-code-change')
        assert result.returncode == 1, result.stdout
        assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'
        output = result.stdout
    else:
        store.receipt(root, 'TASK-1', 'implemented')
        store.receipt(root, 'TASK-1', 'test', store.split_command(command))
        store.set_task_field(root, 'TASK-1', 'status', 'DONE')
        if action == 'stop':
            monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
            assert hooks.run(root, 'stop') == 2
            output = capsys.readouterr().err
        else:
            args = []
            if action == 'staged':
                git(root, 'add', 'tasks', '.workorder')
                args = ['--staged']
            elif action == 'run':
                args = ['--base', base, '--run-tests', '--all-tests', '--allow', 'TASK-1,tests']
            result = cli(root, 'check', *args)
            assert result.returncode == 1, result.stdout
            output = result.stdout
    summary = '1 skipped' if outcome == 'skip' else '1 xfailed'
    assert f'its test ran but no test passed ({summary})' in output


REVIEWS = json.loads((Path(__file__).parent / 'fixtures/reviews.json').read_text())


@pytest.mark.parametrize('case', REVIEWS['cases'], ids=lambda case: case['name'])
def test_review_filter_recorded_json(case):
    assert review_labels.accepted_review(REVIEWS['pull_request'], case['pages']) is case['accepted']


@pytest.mark.skipif(os.name == 'nt', reason='executes the generated POSIX workflow')
def test_workflow_review_event_and_allowances(local_repo, tmp_path):
    root = local_repo
    task(root, 'TASK-3')
    bootstrap.initialize(root)
    generated = (root / '.github/workflows/workorder.yml').read_text()
    assert 'pull_request_review:\n    types: [submitted]' in generated
    assert 'github.event.pull_request.head.sha' in generated
    step, command = next((step, command) for step, command in check_steps(generated) if '--base' in command)
    assert "github.event_name == 'pull_request_review'" in step
    event = tmp_path / 'event.json'
    event.write_text(json.dumps({'pull_request': REVIEWS['pull_request']}))
    binary = tmp_path / 'bin'
    binary.mkdir()
    gh = binary / 'gh'
    gh.write_text(f'#!{PYTHON}\nimport json, sys\n'
                  'assert sys.argv[1:4] == ["api", "--paginate", "--slurp"]\n'
                  'path = sys.argv[4]\n'
                  'assert path in ["repos/example/project/issues/7/events", "repos/example/project/pulls/7/reviews"]\n'
                  f'print(json.dumps({REVIEWS["cases"][0]["pages"]!r} if path.endswith("reviews") else [[]]))\n')
    gh.chmod(0o755)
    runner = binary / 'workorder'
    runner.write_text(f'#!{PYTHON}\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n')
    runner.chmod(0o755)
    env = {**os.environ, 'PATH': str(binary) + os.pathsep + str(Path(PYTHON).parent) + os.pathsep + os.environ['PATH'],
           'PYTHONPATH': str(PACKAGE), 'GITHUB_EVENT_PATH': str(event), 'RUNNER_TEMP': str(tmp_path),
           'GH_REPO': 'example/project', 'PR_NUMBER': '7', 'BASE': 'main'}
    result = subprocess.run(['/bin/sh', '-eu', '-c', command], cwd=root, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    args = json.loads(result.stdout)
    assert '--allow' not in args  # approval does not manufacture explicit labels
    assert '--approving-review' in args


def test_approval_names_accepted_findings(local_repo):
    root = local_repo
    (root / 'tests').mkdir()
    (root / 'tests/test_calc.py').write_text('def test_calc():\n    assert 1 == 1\n')
    base = commit_all(root)
    fill_sections(task(root, 'TASK-3', ships='none', test=store.join_command([PYTHON, 'tests/test_calc.py'])))
    (root / 'tests/test_calc.py').write_text('def test_calc():\n    assert 2 == 2\n')
    commit_all(root)
    store.receipt(root, 'TASK-3', 'implemented')
    store.receipt(root, 'TASK-3', 'test', [PYTHON, 'tests/test_calc.py'])
    store.set_task_field(root, 'TASK-3', 'status', 'DONE')
    output = ok(root, 'check', '--base', base, '--allow', 'TASK-3,tests,no-task', '--approving-review')
    assert 'accepted by an approving review: TASK-3 (new task), tests/test_calc.py (changed lines)' in output
    pending = ok(root, 'done', '--task', 'TASK-3', '--needs-review', '--no-code-change')
    assert 'pending review: the pull request needs workorder-allow:TASK-3 or an approving review' in pending


def test_approval_cannot_accept_current_test_failure(local_repo):
    root = local_repo
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, '-c', 'raise SystemExit(1)'])))
    base = commit_all(root)
    store.receipt(root, 'TASK-1', 'implemented')
    store.receipt(root, 'TASK-1', 'test', gate.declared_test(store.record(root, 'TASK-1')))
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    result = cli(root, 'check', '--base', base, '--run-tests', '--all-tests',
                 '--allow', 'TASK-1,tests,no-task', '--approving-review')
    assert result.returncode == 1 and "TASK-1's test fails on this code" in result.stdout


def test_legacy_receipt_current_skipped_run_refused(local_repo):
    root = local_repo
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'test_app.py').write_text('import pytest\n@pytest.mark.skip(reason="later")\ndef test_app(): pass\n')
    command = [PYTHON, '-m', 'pytest', '-q', 'test_app.py']
    task(root, ships='none', test=store.join_command(command))
    base = commit_all(root)
    store.receipt(root, 'TASK-1', 'implemented')
    # 0.12 stored only exit_code and valid, without the run's outcomes.
    store.append(root, 'TASK-1', dict(kind='test', commit=base, code=store.code_digest(root, base),
                                       command=command, exit_code=0, valid=True))
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    result = cli(root, 'check', '--run-tests', '--all-tests')
    assert result.returncode == 1 and 'no test passed (1 skipped)' in result.stdout


def test_explicit_zero_passed_summary_cannot_count():
    assert gate.pytest_no_pass('0 passed in 0.01s\n') == '0 passed'


@pytest.mark.parametrize('replacement', [
    '    assert 4 == add(2, 2)\n',
    '    assert add(2, 2) == 4, "addition failed"\n',
    '    """Explain the test."""\n    # Exercise addition.\n    assert add(2, 2) == 4\n',
    '    assert  add(2, 2) == 4  # add\n',
])
@pytest.mark.parametrize('staged', [False, True])
def test_harmless_python_test_edits(local_repo, replacement, staged):
    root = local_repo
    path = root / 'test_calc.py'
    path.write_text('from calc import add\ndef test_add():\n    assert add(2, 2) == 4\n')
    base = commit_all(root)
    path.write_text('from calc import add\ndef test_add():\n' + replacement)
    if staged:
        git(root, 'add', 'test_calc.py')
        path.write_text('def test_add():\n    assert False\n')
    assert gate.test_file_changes(root, base, {'id': 'tests', 'test': 'pytest'}, staged,
                                  protected={'test_calc.py'}) is None


@pytest.mark.parametrize('replacement', [
    '    assert add(2, 2) == 5\n', '    assert add(2, 2) != 4\n', '    pass\n',
    '    assert 1 < add(2, 2) < 9\n',
])
def test_real_python_test_edits_still_need_review(local_repo, replacement):
    path = local_repo / 'test_calc.py'
    path.write_text('def test_add():\n    assert add(2, 2) == 4\n')
    base = commit_all(local_repo)
    path.write_text('def test_add():\n' + replacement)
    result = cli(local_repo, 'check', '--base', base)
    assert result.returncode == 1 and 'lost or changed' in result.stdout


def test_inequality_docstrings_and_other_functions(local_repo):
    root = local_repo
    path = root / 'test_calc.py'
    original = ('class TestCalc:\n    def test_add(self):\n        """Old explanation."""\n'
                '        assert add(2, 2) != 5\n\ndef test_other():\n    assert sub(2, 2) == 0\n')
    path.write_text(original)
    base = commit_all(root)
    harmless = original.replace('Old explanation.', '').replace('add(2, 2) != 5', '5 != add(2, 2), "message"')
    path.write_text(harmless)
    ok(root, 'check', '--base', base)
    path.write_text(harmless.replace('sub(2, 2) == 0', 'True'))
    assert cli(root, 'check', '--base', base).returncode == 1


def test_comment_edits_with_added_test_are_harmless(local_repo):
    path = local_repo / 'test_calc.py'
    path.write_text('# Old introduction\ndef test_add():\n    assert add(2, 2) == 4\n    # Old explanation\n')
    base = commit_all(local_repo)
    path.write_text('# New introduction\ndef test_add():\n    assert add(2, 2) == 4\n'
                    '\ndef test_sub():\n    assert sub(2, 2) == 0\n')
    ok(local_repo, 'check', '--base', base)


@pytest.mark.skipif(os.name == 'nt', reason='installs a POSIX shell hook')
def test_parallel_squash_commits_with_installed_hook(local_repo, tmp_path):
    root = local_repo
    _, branches = parallel_pull_requests(root, tmp_path)
    originals = [store.entries(branch, f'TASK-{n}')[0]['commit'] for n, branch in enumerate(branches, 1)]
    hook = root / '.git/hooks/pre-commit'
    hook.write_text(f'#!/bin/sh\nexec {store.join_command([PYTHON, "-m", "workorder", "check", "--staged"])}\n')
    hook.chmod(0o755)
    for n in (1, 2):
        git(root, 'merge', '--squash', f'pr{n}')
        result = subprocess.run(['git', '-c', 'core.hooksPath=.git/hooks', 'commit', '-qm', f'land {n}'],
                                cwd=root, env={**os.environ, 'PYTHONPATH': str(PACKAGE)}, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout + result.stderr
        # Local remote-tracking ref represents the push; no network is used.
        git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
        git(root, 'branch', '-D', f'pr{n}')
    clone = tmp_path / 'fresh-hooked'
    git(tmp_path, 'clone', '--no-local', '--single-branch', '-q', str(root), str(clone))
    assert all(not store.has_commit(clone, commit) for commit in originals)
    ok(clone, 'check', '--run-tests', '--all-tests')


def test_review_advice_is_one_sentence():
    output = gate.reviewer_label('TASK-1: changed test; a reviewer accepts it with --allow TASK-1', 'TASK-1')
    assert output == ('TASK-1: changed test; a reviewer accepts it with the label '
                      'workorder-allow:TASK-1 (or an approving review)')


def test_reused_time_printed_once(capsys):
    run = SimpleNamespace(cached=True, reused_at='12:34', returncode=0, stdout='', stderr='')
    with store.test_output_session():
        gate.test_result(run, {})
        gate.test_result(run, {})
    assert capsys.readouterr().out.count('(reused from 12:34 UTC)') == 1


def code_path_task(root, paths):
    (root / 'src').mkdir()
    (root / 'src/owned.py').write_text('value = 1\n')
    (root / 'src/unrelated.py').write_text('value = 1\n')
    path = task(root, 'TASK-2', ships='none', test=store.join_command([PYTHON, '-c', 'pass']))
    if paths is not None:
        path.write_text(path.read_text().replace('\ncontract:', '\ncode_paths: ' + json.dumps(paths) + '\ncontract:'))
    return commit_all(root)


@pytest.mark.parametrize('paths,changed,refused', [
    (['src/owned.py'], 'src/unrelated.py', True),
    (['src/owned.py'], 'src/owned.py', False),
    (['src'], 'src/unrelated.py', False),
    (['src/owned'], 'src/owned.py', True),
    ([], 'src/unrelated.py', True),
    (None, 'src/unrelated.py', False),
])
def test_optional_code_paths_require_task(local_repo, paths, changed, refused):
    root = local_repo
    base = code_path_task(root, paths)
    (root / changed).write_text('value = 2\n')
    commit_all(root)
    ok(root, 'renew', '--task', 'TASK-2')
    result = cli(root, 'check', '--base', base, '--require-task')
    assert result.returncode == int(refused), result.stdout + result.stderr
    if refused:
        assert f'{changed} is outside the code paths of TASK-2' in result.stdout
        assert 'workorder-allow:TASK-2 (or an approving review)' in result.stdout
        ok(root, 'check', '--base', base, '--require-task', '--allow', 'TASK-2')
        assert cli(root, 'check', '--base', base, '--require-task', '--allow', 'tests').returncode == 1


def test_code_paths_use_union_and_legacy_opt_out(local_repo):
    root = local_repo
    code_path_task(root, ['src/owned.py'])
    other = task(root, 'TASK-3', ships='none', test=store.join_command([PYTHON, '-c', 'pass']))
    other.write_text(other.read_text().replace('\ncontract:', '\ncode_paths: ["src/unrelated.py"]\ncontract:'))
    base = commit_all(root)
    (root / 'src/unrelated.py').write_text('value = 2\n')
    commit_all(root)
    for tid in ('TASK-2', 'TASK-3'):
        ok(root, 'renew', '--task', tid)
    ok(root, 'check', '--base', base, '--require-task')
    other.write_text(other.read_text().replace('code_paths: ["src/unrelated.py"]\n', ''))
    ok(root, 'check', '--base', base, '--require-task')


def test_code_paths_test_receipt_and_staged_definition(local_repo):
    root = local_repo
    code_path_task(root, ['src/owned.py'])
    (root / 'src/unrelated.py').write_text('value = 2\n')
    commit_all(root)
    store.receipt(root, 'TASK-2', 'implemented')
    base = commit_all(root)
    store.receipt(root, 'TASK-2', 'test', [PYTHON, '-c', 'pass'])
    # Exercise scope selection with a new test receipt and an existing implementation.
    changed = gate.changed_paths(root, base) | {'src/unrelated.py'}
    problems = gate.code_path_problems(root, base, changed, {'src/unrelated.py'}, gate.records(root), False, (), [])
    assert any('src/unrelated.py is outside the code paths of TASK-2' in line for line in problems)
    git(root, 'add', 'tasks', '.workorder')
    path = root / 'tasks/TASK-2.md'
    path.write_text(path.read_text().replace('src/owned.py', 'src'))
    problems = gate.code_path_problems(root, base, changed, {'src/unrelated.py'}, gate.records(root, True), True, (), [])
    assert problems


def test_no_task_label_still_accepts_code_with_a_new_open_task(local_repo):
    root = local_repo
    base = store.head(root)
    task(root, 'TASK-2', ships='none', test=store.join_command([PYTHON, '-c', 'pass']))
    (root / 'app.py').write_text('value = 2\n')
    commit_all(root)
    ok(root, 'renew', '--task', 'TASK-2')
    ok(root, 'check', '--base', base, '--require-task', '--allow', 'no-task')


def test_any_new_receipt_participates_in_code_paths_opt_in(local_repo):
    root = local_repo
    code_path_task(root, ['src/owned.py'])
    task(root, 'TASK-3', ships='repo')
    base = commit_all(root)
    (root / 'src/unrelated.py').write_text('value = 2\n')
    commit_all(root)
    ok(root, 'renew', '--task', 'TASK-2')
    store.append(root, 'TASK-3', dict(kind='merged', commit=store.head(root),
                                     code=store.code_digest(root, 'HEAD'), ref='refs/heads/main'))
    ok(root, 'check', '--base', base, '--require-task')
