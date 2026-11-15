"""Second-pass regressions; all repositories and review fixtures stay local."""

import json
import os
from pathlib import Path
import shlex
import subprocess

import pytest

from workorder import gate, store
from test_workorder import PYTHON, cli, git, local_repo, ok
from test_v05 import commit_all
from test_v14 import CODE, TEST, branch, claim, check


@pytest.mark.parametrize('existing', [False, True])
@pytest.mark.parametrize('action', ['done', 'check'])
def test_test_origin_is_visible(local_repo, existing, action):
    root = local_repo
    branch(root, test_text=TEST if existing else None, code=CODE.replace('a / b', '0'))
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text(TEST)
    commit_all(root)
    if action == 'done':
        output = ok(root, 'done', '--task', 'TASK-2')
    else:
        claim(root)
        output = ok(root, 'check', '--base', 'origin/main')
    expected = ("TASK-2's test landed on origin/main before this change and is unchanged" if existing
                else "TASK-2's test was written in this change")
    assert expected in output


@pytest.mark.parametrize('change', ['added', 'changed', 'pathless'])
def test_test_first_requires_task_label_even_with_approval(local_repo, change):
    root = local_repo
    branch(root, test_text=None if change == 'added' else TEST, code=CODE.replace('a / b', '0'))
    if change == 'pathless':
        definition = root / 'tasks/TASK-2.md'
        definition.write_text(definition.read_text().replace(' -q tests/test_div.py', ' -q').replace(
            'ships:', 'test_paths: ["tests/test_div.py"]\nships:'))
        commit_all(root)
        git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (root / 'calc.py').write_text(CODE)
    (root / 'tests/test_div.py').write_text(TEST + '# changed here\n')
    commit_all(root)
    claim(root)
    result = check(root, '--require-test-first', '--approving-review', '--allow', 'tests')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'test must land on origin/main, failing, before the work' in result.stdout
    assert 'approval did not cover' in result.stdout
    accepted = check(root, '--require-test-first', '--approving-review', '--allow', 'TASK-2')
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_test_first_accepts_unchanged_red_test_on_main(local_repo):
    root = local_repo
    branch(root, test_text=TEST, code=CODE.replace('a / b', '0'))
    (root / 'calc.py').write_text(CODE)
    commit_all(root)
    ok(root, 'done', '--task', 'TASK-2', '--require-test-first')
    result = check(root, '--require-test-first')
    assert result.returncode == 0, result.stdout + result.stderr


def test_done_test_first_refuses_new_callable_test_without_writing_receipts(local_repo):
    root = local_repo
    branch(root, code=None)
    (root / 'calc.py').write_text('def div(a, b):\n    raise NotImplementedError\n')
    (root / 'tests/test_div.py').write_text('from calc import div\ndef test_div():\n    assert callable(div)\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-2', '--require-test-first', '--needs-review')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'test must land on origin/main, failing, before the work' in result.stdout
    assert store.entries(root, 'TASK-2') == []
    assert store.record(root, 'TASK-2')['status'] == 'IN_PROGRESS'


@pytest.mark.parametrize('state', ['missing', 'passing', 'failing', 'partly-missing'])
def test_new_test_first_checks_files_and_runs_before_creating_task(local_repo, state):
    root = local_repo
    if state != 'missing':
        (root / 'test_red.py').write_text('assert 2 + 2 == ' + ('4\n' if state == 'passing' else '5\n'))
    result = cli(root, 'new', '--test-first', '--title', 'Add', '--owner', 'you', '--local',
                 '--test', store.join_command([PYTHON, 'test_red.py']), '--test-paths', 'test_red.py',
                 *(['missing.py'] if state == 'partly-missing' else []))
    if state == 'failing':
        assert result.returncode == 0, result.stdout + result.stderr
        assert store.record(root, 'TASK-1')['test_paths'] == ['test_red.py']
    else:
        assert result.returncode == 1, result.stdout + result.stderr
        assert ('already passes' if state == 'passing' else 'does not exist') in result.stdout + result.stderr
        assert not (root / 'tasks/TASK-1.md').exists()


def test_init_test_first_writes_enforced_workflow(local_repo):
    ok(local_repo, 'init', '--test-first')
    workflow = (local_repo / '.github/workflows/workorder.yml').read_text()
    command = next(line.strip() for line in workflow.splitlines() if 'workorder check --base' in line)
    assert '--require-test-first' in shlex.split(command)


def approval_args(root, tmp_path, labels=()):
    pr = {'user': {'login': 'author'}, 'head': {'sha': 'head'},
          'labels': [{'name': 'workorder-allow:' + label} for label in labels]}
    events = [[{'event': 'labeled', 'label': label, 'actor': {'login': 'reviewer'}} for label in pr['labels']]]
    reviews = [[{'user': {'login': 'reviewer'}, 'commit_id': 'head', 'state': 'APPROVED'}]]
    files = []
    for name, data in [('event', {'pull_request': pr}), ('labels', events), ('reviews', reviews)]:
        path = tmp_path / (name + '.json')
        path.write_text(json.dumps(data))
        files.append(str(path))
    result = subprocess.run([PYTHON, '-m', 'workorder.review_labels', *files, 'origin/main'], cwd=root,
                            env={**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[1])},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return shlex.split(result.stdout)


@pytest.mark.parametrize('owned', [False, True])
def test_approval_cannot_accept_hidden_behavior_change(local_repo, tmp_path, owned):
    root = local_repo
    branch(root, test_text=TEST)
    if not owned:
        (root / 'tasks/TASK-2.md').unlink()
        commit_all(root)
        git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    (root / 'tests/test_div.py').write_text(TEST.replace('== 2', '== 18'))
    commit_all(root)
    if owned:
        claim(root)
    result = check(root, *approval_args(root, tmp_path))
    assert result.returncode == 1, result.stdout + result.stderr
    expected = ('its tests from origin/main fail on this code' if owned
                else 'its version from origin/main fails on this code (behaviour changed)')
    assert expected in result.stdout
    assert 'approval did not cover' in result.stdout
    label = 'TASK-2' if owned else 'tests'
    accepted = check(root, *approval_args(root, tmp_path, [label]))
    assert accepted.returncode == 0, accepted.stdout + accepted.stderr


def test_unowned_old_test_is_compared_without_approval(local_repo):
    root = local_repo
    branch(root, test_text=TEST)
    (root / 'tasks/TASK-2.md').unlink()
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    (root / 'tests/test_div.py').write_text(TEST.replace('== 2', '== 18'))
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    assert result.returncode == 1
    assert 'tests/test_div.py: its version from origin/main fails on this code (behaviour changed)' in result.stdout
    assert 'lost or changed' not in result.stdout


@pytest.mark.parametrize('fresh_clone', [False, True])
@pytest.mark.parametrize('later', [None, 'recorded', 'unrecorded'])
def test_rebase_keeps_receipts_only_for_recorded_code_and_target_changes(local_repo, tmp_path, later, fresh_clone):
    root = local_repo
    branch(root, test_text=TEST, code=CODE.replace('a / b', '0'))
    (root / 'calc.py').write_text(CODE)
    commit_all(root)
    claim(root)
    original = store.entries(root, 'TASK-2')[0]['commit']
    commit_all(root)
    git(root, 'checkout', '-q', 'main')
    (root / 'unrelated.py').write_text('value = 1\n')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'checkout', '-q', 'feature')
    git(root, 'rebase', 'main')
    if fresh_clone:
        clone = tmp_path / 'rebased-clone'
        git(tmp_path, 'clone', '--no-local', '--branch', 'feature', str(root), str(clone))
        git(clone, 'config', 'user.name', 'you')
        git(clone, 'config', 'user.email', 'you@example.com')
        assert not store.has_commit(clone, original)
        root = clone
    if later:
        path = root / ('calc.py' if later == 'recorded' else 'unrelated.py')
        path.write_text(path.read_text() + '# edited after testing\n')
        commit_all(root)
    result = check(root)
    assert result.returncode == (1 if later else 0), result.stdout + result.stderr
    if later:
        assert 'no task records it' in result.stdout


@pytest.mark.parametrize('action', ['show', 'check-task', 'check'])
@pytest.mark.parametrize('ships,status', [('none', 'DONE'), ('repo', 'DONE'), ('none', 'CLOSED'), ('repo', 'CLOSED')])
def test_recorded_evidence_is_not_presented_as_a_rerun(local_repo, action, ships, status):
    root = local_repo
    branch(root, test_text=TEST, code=CODE.replace('a / b', '0'))
    (root / 'calc.py').write_text(CODE)
    commit_all(root)
    claim(root)
    definition = root / 'tasks/TASK-2.md'
    definition.write_text(definition.read_text().replace('ships: "none"', 'ships: ' + json.dumps(ships)))
    if ships == 'repo' and status == 'CLOSED':
        commit_all(root)
        git(root, 'checkout', '-q', 'main')
        git(root, 'merge', '-q', 'feature')
        git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    store.set_task_field(root, 'TASK-2', 'status', status)
    args = ['show', '--task', 'TASK-2'] if action == 'show' else ['check', 'TASK-2'] if action == 'check-task' else ['check']
    output = ok(root, *args)
    assert 'Evidence: recorded receipts support the claim (not re-run here; `check --run-tests` re-runs it)' in output


def test_comparison_summary_counts_tasks_not_both_code_runs(local_repo):
    root = local_repo
    branch(root, test_text=TEST, code=CODE.replace('a / b', '0'))
    (root / 'calc.py').write_text(CODE)
    commit_all(root)
    claim(root)
    for _ in range(2):
        result = check(root)
        assert result.returncode == 0, result.stdout + result.stderr
        assert "ran 1 task's declared test" in result.stdout
        assert 'ran 2 declared tests' not in result.stdout


@pytest.mark.parametrize('action', ['done', 'check'])
def test_inline_test_keeps_its_base_provenance(local_repo, action):
    root = local_repo
    branch(root, code=CODE.replace('a / b', '0'))
    definition = root / 'tasks/TASK-2.md'
    previous = store.record(root, 'TASK-2')['test']
    command = store.join_command([PYTHON, '-c', 'from calc import div; assert div(6,3) == 2'])
    definition.write_text(definition.read_text().replace(json.dumps(previous), json.dumps(command)))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (root / 'calc.py').write_text(CODE)
    commit_all(root)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-2', '--require-test-first')
    else:
        claim(root)
        result = check(root, '--require-test-first')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "TASK-2's test landed on origin/main before this change and is unchanged" in result.stdout


def test_unowned_unavailable_comparison_is_not_accepted_by_approval(local_repo):
    root = local_repo
    branch(root, test_text='import missing_test_dependency_xyz\n' + TEST)
    (root / 'tasks/TASK-2.md').unlink()
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    (root / 'calc.py').write_text(CODE.replace('a / b', 'a * b'))
    (root / 'tests/test_div.py').write_text(TEST.replace('== 2', '== 18'))
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--approving-review')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'could not compare tests/test_div.py' in result.stdout
    assert 'missing_test_dependency_xyz' in result.stdout


@pytest.mark.parametrize('action', ['done', 'check-run'])
def test_fresh_runs_keep_fresh_evidence_wording(local_repo, action):
    root = local_repo
    branch(root, test_text=TEST, code=CODE.replace('a / b', '0'))
    (root / 'calc.py').write_text(CODE)
    commit_all(root)
    if action == 'done':
        output = ok(root, 'done', '--task', 'TASK-2')
        assert 'its declared test passed on this code' in output
    else:
        claim(root)
        output = ok(root, 'check', '--run-tests')
        assert 'its test passed when this check ran it' in output
    assert 'not re-run here' not in output
