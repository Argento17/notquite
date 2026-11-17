"""Round-12 regressions: task-first edits, test ownership, and other runners."""

import io
import os
import shutil
import subprocess
import sys

import pytest

from workorder import bootstrap, gate, hooks, store
from test_workorder import PACKAGE, PYTHON, cli, clones, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import supported
from test_v07b_setup import local_claim, refuse_network


def _power_repo(root, weaken):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'calc.py').write_text('def power(a, b):\n    return 1\n')
    (root / 'tests').mkdir()
    body = 'from calc import power\n\ndef test_power():\n    assert power(2, 3) == 8\n'
    (root / 'tests/test_power.py').write_text(body)
    (root / 'pytest.ini').write_text('[pytest]\npythonpath = .\n')
    path = task(root, ships='none', test=f'{PYTHON} -m pytest -q tests/test_power.py')
    path.write_text(path.read_text() + '\n## Scope\n\nPower.\n\n## Acceptance\n\n- power(2, 3) is 8.\n')
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-q', '-b', 'feature')
    probe = root / 'tests/test_power.py'
    if weaken == 'return':
        probe.write_text(body.replace('def test_power():\n', 'def test_power():\n    return\n', 1))
    else:
        probe.write_text(body + '\ndef test_power():\n    assert power is not None\n')
    commit_all(root)
    return base


@pytest.mark.parametrize('weaken', ['return', 'shadow'])
@pytest.mark.parametrize('action', ['done', 'pre-commit', 'stop', 'check'])
def test_task_first_added_lines_cannot_hide_a_failing_ref_test(local_repo, monkeypatch, capsys, weaken, action):
    # 0.18: local hooks refuse reviewer findings and hand-set DONE; this exercises the note path,
    # which is now warning mode (WORKORDER_REVIEW=warn, the local --review warn).
    monkeypatch.setenv('WORKORDER_REVIEW', 'warn')
    if action == 'pre-commit' and os.name == 'nt':
        pytest.skip('executes a POSIX pre-commit hook')
    root = local_repo
    if action == 'pre-commit':
        bootstrap.initialize(root)
    base = _power_repo(root, weaken)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-1')
        output = result.stdout + result.stderr
        assert result.returncode == 1, output
        assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'
    else:
        store.receipt(root, 'TASK-1', 'implemented')
        store.receipt(root, 'TASK-1', 'test', [PYTHON, '-m', 'pytest', '-q', 'tests/test_power.py'])
        store.set_task_field(root, 'TASK-1', 'status', 'DONE')
        if action == 'pre-commit':
            git(root, 'add', '-A')
            env = dict(os.environ, PYTHONPATH=str(PACKAGE),
                       PATH=str(os.path.dirname(PYTHON)) + os.pathsep + os.environ['PATH'])
            result = subprocess.run([str(root / '.git/hooks/pre-commit')], cwd=root, env=env,
                                    capture_output=True, text=True)
            output = result.stdout + result.stderr
            assert result.returncode == 0, output
            assert "Note:" in output and "NOT DONE" not in output
        elif action == 'stop':
            local_claim(root, 'TASK-1')
            refuse_network(monkeypatch)
            monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
            assert hooks.run(root, 'stop') == 0
            captured = capsys.readouterr()
            output = captured.out + captured.err
        else:
            result = cli(root, 'check', '--review', 'refuse', '--base', base, '--run-tests', '--all-tests', '--require-task')
            output = result.stdout + result.stderr
            assert result.returncode == 1, output
            assert 'this change edits code but no task records it' not in output
    assert 'so any change needs review' in output
    assert 'its tests from' in output and 'fail on this code' in output
    assert 'workorder-allow:TASK-1' in output
    allowed = cli(root, 'check', '--review', 'refuse', '--base', base, '--run-tests', '--allow', 'tests')
    assert allowed.returncode == 1, allowed.stdout + allowed.stderr
    assert 'so any change needs review' in allowed.stdout


def test_allow_id_accepts_a_task_first_edit(local_repo):
    root = local_repo
    base = _power_repo(root, 'return')
    store.receipt(root, 'TASK-1', 'implemented')
    store.receipt(root, 'TASK-1', 'test', [PYTHON, '-m', 'pytest', '-q', 'tests/test_power.py'])
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    result = cli(root, 'check', '--base', base, '--run-tests', '--all-tests', '--require-task',
                 '--allow', 'TASK-1')
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert 'so any change needs review (allowed)' in output or 'so any change needs review' in output
    assert '(allowed)' in output


def test_named_file_does_not_own_the_rest_of_tests(local_repo):
    root = local_repo
    (root / 'tests').mkdir()
    (root / 'tests/test_one.py').write_text('def test_one():\n    assert True\n')
    (root / 'tests/test_two.py').write_text('def test_two():\n    assert True\n')
    (root / 'tests/helper.py').write_text('value = 1\n')
    (root / 'tests/conftest.py').write_text('value = 1\n')
    task(root, 'TASK-1', ships='none', test=f'{PYTHON} -m pytest -q tests/test_one.py')
    task(root, 'TASK-2', ships='none', test=f'{PYTHON} -m pytest -q tests/test_two.py')
    for number in range(3, 8):
        task(root, f'TASK-{number}', ships='none', test=f'{PYTHON} -m pytest -q')
    base = commit_all(root)
    probe = root / 'tests/test_one.py'
    probe.write_text(probe.read_text().replace('assert True', 'assert int(1) == 1'))
    refused = cli(root, 'check', '--review', 'refuse', '--base', base)
    output = refused.stdout + refused.stderr
    assert refused.returncode == 1, output
    assert output.count('lost or changed') == 1
    assert 'TASK-1, TASK-3, TASK-4, TASK-5, TASK-6, TASK-7:' in output and 'tests/test_one.py' in output
    assert 'TASK-2:' not in output
    assert cli(root, 'check', '--review', 'refuse', '--base', base, '--allow', 'tests').returncode == 0
    helper = root / 'tests/helper.py'
    helper.write_text('value = 2\n')
    probe.write_text('def test_one():\n    assert True\n')
    helper_change = cli(root, 'check', '--review', 'refuse', '--base', base)
    helper_output = helper_change.stdout + helper_change.stderr
    assert helper_change.returncode == 1, helper_output
    assert 'tests/helper.py' in helper_output and 'workorder-allow:TASK-3' in helper_output
    assert 'TASK-1:' not in helper_output
    helper.write_text('value = 1\n')
    (root / 'tests/conftest.py').write_text('value = 2\n')
    shared = cli(root, 'check', '--review', 'refuse', '--base', base)
    shared_output = shared.stdout + shared.stderr
    assert shared.returncode == 1, shared_output
    assert 'TASK-1, TASK-2:' in shared_output
    assert 'conftest.py' in shared_output
    assert cli(root, 'check', '--review', 'refuse', '--base', base, '--allow', 'tests').returncode == 0
    one = cli(root, 'check', '--review', 'refuse', '--base', base, '--allow', 'TASK-1')
    assert one.returncode == 0
    assert 'lost or changed' not in one.stdout


def test_allow_tests_accepts_lines_owned_by_every_named_folder(local_repo):
    root = local_repo
    (root / 'tests').mkdir()
    probe = root / 'tests/test_app.py'
    probe.write_text('def test_one():\n    assert True\n')
    for number in range(1, 4):
        task(root, f'TASK-{number}', ships='none', test=f'{PYTHON} -m pytest -q tests')
    base = commit_all(root)
    probe.write_text('def test_one():\n    assert int(1) == 1\n')
    refused = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert refused.returncode == 1
    assert refused.stdout.count('lost or changed') == 1
    assert 'TASK-1, TASK-2, TASK-3:' in refused.stdout
    assert cli(root, 'check', '--review', 'refuse', '--base', base, '--allow', 'tests').returncode == 0
    one = cli(root, 'check', '--review', 'refuse', '--base', base, '--allow', 'TASK-1')
    assert one.returncode == 0
    assert 'lost or changed' not in one.stdout


def test_new_records_test_paths_when_the_command_names_them(local_repo):
    (local_repo / 'tests').mkdir()
    (local_repo / 'tests/test_power.py').write_text('def test_power():\n    assert 2 ** 3 == 8\n')
    ok(local_repo, 'new', '--title', 'Add power', '--owner', 'dev', '--local',
       '--test', f'{PYTHON} -m pytest -q tests/test_power.py')
    record = store.record(local_repo, 'TASK-1')
    assert record['test_paths'] == ['tests/test_power.py']
    bare = local_repo / 'bare'
    bare.mkdir()
    git(bare, 'init', '-q')
    git(bare, 'config', 'user.name', 'dev')
    git(bare, 'config', 'user.email', 'dev@example.invalid')
    result = cli(bare, 'new', '--title', 'Run everything', '--owner', 'dev', '--local',
                 '--test', f'{PYTHON} -m pytest -q')
    assert result.returncode == 1 and '--test-paths' in result.stderr
    assert not (bare / 'tasks').exists()


def test_other_languages_count_as_test_files(local_repo):
    root = local_repo
    files = {
        'src/calc.test.js': 'old\n',
        'src/calc.spec.ts': 'old\n',
        'pkg/calc_test.go': 'old\n',
        'frontend/__tests__/calc.js': 'old\n',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    base = commit_all(root)
    for name in files:
        (root / name).write_text('new\n')
    refused = cli(root, 'check', '--review', 'refuse', '--base', base)
    output = refused.stdout + refused.stderr
    assert refused.returncode == 1, output
    for name in files:
        assert name in output
    assert cli(root, 'check', '--review', 'refuse', '--base', base, '--allow', 'tests').returncode == 0


@pytest.mark.skipif(not shutil.which('node'), reason='needs Node.js to run the declared JavaScript tests')
def test_finished_node_suite_needs_test_paths(local_repo):
    root = local_repo
    (root / 'src').mkdir()
    (root / 'src/calc.js').write_text('exports.add = (a, b) => a + b;\n')
    (root / 'src/calc.test.js').write_text(
        "const test = require('node:test');\nconst assert = require('node:assert');\n"
        "const { add } = require('./calc.js');\n"
        "test('adds', () => { assert.strictEqual(add(2, 2), 4); });\n")
    path = task(root, ships='none', test='node --test')
    path.write_text(path.read_text() + '\n## Scope\n\nAdd.\n\n## Acceptance\n\n- add(2, 2) is 4.\n')
    commit_all(root)
    done = cli(root, 'done', '--no-code-change', '--task', 'TASK-1')
    assert done.returncode == 0, done.stdout + done.stderr
    base = commit_all(root)
    (root / 'src/calc.js').write_text('exports.add = (a, b) => a + b + 1;\n')
    (root / 'src/calc.test.js').write_text(
        "const test = require('node:test');\nconst assert = require('node:assert');\n"
        "const { add } = require('./calc.js');\n"
        "test('adds', () => { assert.strictEqual(add(2, 2), 5); });\n")
    commit_all(root)
    store.receipt(root, 'TASK-1', 'implemented')
    store.receipt(root, 'TASK-1', 'test', ['node', '--test'])
    result = cli(root, 'check', '--base', base, '--run-tests', '--all-tests', '--require-task')
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert 'TASK-1: no test files are known for its test; set test_paths' in output
    assert 'check: OK' not in output


def test_explicit_runs_stay_fresh_in_the_summary(local_repo):
    root = local_repo
    (root / 'a.py').write_text('raise SystemExit(0)\n')
    (root / 'b.py').write_text('raise SystemExit(0)\n')
    supported(root, tid='TASK-1', test=f'{PYTHON} a.py')
    supported(root, tid='TASK-2', test=f'{PYTHON} b.py')
    for tid, name in (('TASK-1', 'a.py'), ('TASK-2', 'b.py')):
        path = root / f'tasks/{tid}.md'
        path.write_text(path.read_text().replace('\ncontract:', f'\ntest_paths: ["{name}"]\ncontract:', 1))
    base = commit_all(root)
    first = cli(root, 'check', '--review', 'refuse', '--base', base, '--run-tests', '--all-tests')
    assert first.returncode == 0, first.stdout + first.stderr
    assert "no task changed in this change; ran 2 finished tasks' declared tests" in first.stdout
    second = cli(root, 'check', '--review', 'refuse', '--base', base, '--run-tests', '--all-tests')
    output = second.stdout + second.stderr
    assert second.returncode == 0, output
    assert "no task changed in this change; ran 2 finished tasks' declared tests" in output
    assert '(cached)' not in output
    assert "ran 0 tasks' declared tests" not in output
    assert "2 tasks' tests passed when this check ran them" in output


def test_failing_current_test_has_no_allow_label(local_repo, monkeypatch, capsys):
    root = local_repo
    (root / 'probe.py').write_text('assert True\n')
    path = task(root, ships='none', test=f'{PYTHON} probe.py')
    path.write_text(path.read_text() + '\n## Scope\n\nProbe.\n\n## Acceptance\n\n- It passes.\n')
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    store.receipt(root, 'TASK-1', 'implemented')
    store.receipt(root, 'TASK-1', 'test', [PYTHON, 'probe.py'])
    store.set_task_field(root, 'TASK-1', 'status', 'DONE')
    (root / 'probe.py').write_text('assert False\n')
    local_claim(root, 'TASK-1')
    refuse_network(monkeypatch)
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    assert hooks.run(root, 'stop') == 2
    output = capsys.readouterr().err
    matched = [line for line in output.splitlines() if 'its test failed when this check ran it' in line]
    assert matched, output
    assert all('workorder-allow' not in line for line in matched)


def test_codeowners_example_covers_records_tests_and_workflows():
    text = (PACKAGE / 'docs/reference.md').read_text(encoding='utf-8')
    example = text.split('# .github/CODEOWNERS', 1)[1].split('```', 1)[0]
    for path in ('/tasks/', '/.workorder/', '/tests/', '/.github/'):
        assert path in example


def test_session_start_drops_a_claim_force_released_elsewhere(clones, capsys):
    one, two = clones
    store.claim(one, 'TASK-1')
    assert hooks.run(one, 'session-start') == 0
    assert 'TASK-1 held' in capsys.readouterr().out
    assert store.release(two, 'TASK-1', force=True) is True
    assert hooks.run(one, 'session-start') == 0
    output = capsys.readouterr().out
    assert 'TASK-1 held' not in output
    assert 'workorder show --task TASK-1' not in output


def test_session_start_keeps_local_claims_when_origin_cannot_be_read(local_repo, monkeypatch, capsys):
    task(local_repo, ships='none')
    local_claim(local_repo, 'TASK-1')
    git(local_repo, 'remote', 'add', 'origin', 'https://invalid.example/no-network.git')
    refuse_network(monkeypatch, allow_claim_refresh=True)
    assert hooks.run(local_repo, 'session-start') == 0
    assert 'TASK-1 held' in capsys.readouterr().out
