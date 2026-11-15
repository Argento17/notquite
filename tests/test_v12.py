"""Round-14 regressions, with local disposable Git repositories only."""

import io
import re
import sys

import pytest

from workorder import gate, hooks, store
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v05 import commit_all
from test_v06b import fill_sections
from test_v11b import parallel_pull_requests


def reviewed_task(root):
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'test_app.py').write_text('assert 1 == 1\n')
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, 'test_app.py'])))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    return base


def test_review_refuses_by_default_but_warn_is_optional(local_repo):
    root = local_repo
    base = reviewed_task(root)
    (root / 'test_app.py').write_text('assert True\n')
    refused = cli(root, 'check', '--base', base)
    assert refused.returncode == 1, refused.stdout
    assert 'lost or changed' in refused.stdout
    warned = cli(root, 'check', '--base', base, '--review', 'warn')
    assert warned.returncode == 0, warned.stdout
    assert 'Warning:' in warned.stdout
    accepted = cli(root, 'check', '--base', base, '--allow', 'TASK-1')
    assert accepted.returncode == 0, accepted.stdout


def test_needs_review_records_evidence_and_done(local_repo):
    root = local_repo
    base = reviewed_task(root)
    (root / 'test_app.py').write_text('assert True\n')
    commit_all(root)
    output = ok(root, 'done', '--task', 'TASK-1', '--needs-review')
    assert store.record(root, 'TASK-1')['status'] == 'DONE', output
    note = 'pending review: the pull request needs workorder-allow:TASK-1'
    assert note in output
    assert store.entries(root, 'TASK-1')[-1]['pending_review'] is True
    assert [item['kind'] for item in store.entries(root, 'TASK-1')] == ['implemented', 'test']
    # The command writes the claim; the label accepts its changed tests.
    refused = cli(root, 'check', '--base', base, '--run-tests')
    assert refused.returncode == 1
    ok(root, 'check', '--base', base, '--run-tests', '--allow', 'TASK-1')
    commit_all(root)
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '-q', '--squash', 'feature')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    ok(root, 'check', '--run-tests', '--all-tests')


@pytest.mark.parametrize('status', ['DONE', 'IN_PROGRESS'])
def test_new_task_receipts_get_one_precise_definition_request(local_repo, status):
    root = local_repo
    base = store.head(root)
    git(root, 'checkout', '-qb', 'feature')
    (root / 'app.py').write_text('items = [1]\n')
    fill_sections(task(root, 'TASK-4', ships='none', test=store.join_command([PYTHON, '-c', 'pass'])))
    commit_all(root)
    ok(root, 'done', '--no-code-change', '--task', 'TASK-4')
    store.set_task_field(root, 'TASK-4', 'status', status)
    result = cli(root, 'check', '--base', base, '--require-task')
    assert result.returncode == 1, result.stdout
    expected = ('TASK-4 is new in this pull request: a reviewer accepts its definition with the label '
                'workorder-allow:TASK-4 (or an approving review)')
    assert result.stdout.count(expected) == 1, result.stdout
    assert 'no task records it' not in result.stdout
    ok(root, 'check', '--base', base, '--require-task', '--allow', 'TASK-4')
    (root / 'app.py').write_text('items = [2]\n')
    commit_all(root)
    uncovered = cli(root, 'check', '--base', base, '--require-task')
    assert uncovered.returncode == 1 and 'no task records it' in uncovered.stdout


@pytest.mark.parametrize('all_tests', [False, True])
def test_unrelated_unfinished_wrapper_does_not_refuse_base_check(local_repo, all_tests):
    root = local_repo
    (root / 'runner.py').write_text('raise SystemExit(1)\n')
    task(root, test=store.join_command([PYTHON, 'runner.py']))
    base = commit_all(root)
    (root / 'README.md').write_text('A documentation change.\n')
    result = cli(root, 'check', '--base', base, *(['--run-tests', '--all-tests'] if all_tests else []))
    assert result.returncode == 0, result.stdout
    assert 'checked 0 tasks' in result.stdout


def test_new_wrapper_requests_explicit_test_paths_before_writing(local_repo):
    root = local_repo
    (root / 'runner.py').write_text('pass\n')
    command = store.join_command([PYTHON, 'runner.py'])
    result = cli(root, 'new', '--title', 'Wrapper task', '--owner', 'you', '--test', command)
    assert result.returncode == 1, result.stdout
    assert '--test-paths' in result.stderr + result.stdout
    assert not list((root / 'tasks').glob('*.md'))
    ok(root, 'new', '--title', 'Wrapper task', '--owner', 'you', '--test', command,
       '--test-paths', 'runner.py', 'tests')
    assert store.record(root, 'TASK-1')['test_paths'] == ['runner.py', 'tests']


@pytest.mark.parametrize('action', ['done', 'staged', 'stop'])
def test_changed_command_refuses_locally(local_repo, monkeypatch, capsys, action):
    root = local_repo
    reviewed_task(root)
    (root / 'app.py').write_text('items = [1]\n')
    definition = root / 'tasks/TASK-1.md'
    definition.write_text(definition.read_text().replace('test_app.py', '-c pass'))
    commit_all(root)
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-1', '--needs-review')
        assert result.returncode == 1, result.stdout
        output = result.stdout + result.stderr
    else:
        store.receipt(root, 'TASK-1', 'implemented')
        store.receipt(root, 'TASK-1', 'test', [PYTHON, '-c', 'pass'])
        store.set_task_field(root, 'TASK-1', 'status', 'DONE')
        if action == 'staged':
            git(root, 'add', 'tasks', '.workorder')
            result = cli(root, 'check', '--staged')
            assert result.returncode == 1, result.stdout
            output = result.stdout + result.stderr
        else:
            monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
            assert hooks.run(root, 'stop') == 2
            captured = capsys.readouterr()
            output = captured.out + captured.err
    assert 'test changed:' in output
    assert 'workorder-allow:TASK-1' in output


def test_check_ok_follows_warnings(local_repo):
    root = local_repo
    base = reviewed_task(root)
    (root / 'test_app.py').write_text('assert True\n')
    output = ok(root, 'check', '--base', base, '--review', 'warn')
    assert output.index('check: OK') > output.index('Warning:'), output


def test_done_prints_success_output_once(local_repo):
    root = local_repo
    reviewed_task(root)
    (root / 'test_app.py').write_text('assert 1 == 1\nprint("unique test output")\n')
    commit_all(root)
    output = ok(root, 'done', '--no-code-change', '--task', 'TASK-1')
    assert output.count('unique test output') == 1, output


@pytest.mark.parametrize('ships', ['none', 'repo'])
def test_parallel_squashes_survive_deleted_branches_in_fresh_clone(local_repo, tmp_path, ships):
    root = local_repo
    _, branches = parallel_pull_requests(root, tmp_path)
    originals = [store.entries(branch, f'TASK-{n}')[0]['commit'] for n, branch in enumerate(branches, 1)]
    for n in (1, 2):
        git(root, 'merge', '-q', '--squash', f'pr{n}')
        commit_all(root)
        git(root, 'branch', '-D', f'pr{n}')
    clone = tmp_path / 'fresh'
    git(tmp_path, 'clone', '--no-local', '--single-branch', '-q', str(root), str(clone))
    for original in originals:
        assert not store.has_commit(clone, original)
    if ships == 'repo':
        for n in (1, 2):
            path = clone / f'tasks/TASK-{n}.md'
            path.write_text(path.read_text().replace('ships: "none"', 'ships: "repo"').replace('"DONE"', '"CLOSED"'))
    ok(clone, 'check', '--run-tests', '--all-tests')
    # A new claim after changing a task's implemented path still needs evidence.
    (clone / 'part2.py').write_text('value = 99\n')
    git(clone, 'config', 'user.name', 'fresh')
    git(clone, 'config', 'user.email', 'fresh@example.invalid')
    store.set_task_field(clone, 'TASK-2', 'status', 'IN_PROGRESS')
    commit_all(clone)
    store.set_task_field(clone, 'TASK-2', 'status', 'DONE')
    assert cli(clone, 'check').returncode == 1


@pytest.mark.parametrize('multiline', [False, True])
def test_crlf_import_addition_and_assertion_review_on_every_platform(local_repo, multiline):
    root = local_repo
    git(root, 'config', 'core.autocrlf', 'true')
    old = b'from math import sin\n\ndef test_sin():\n    assert sin(0) == 0\n'
    probe = root / 'test_math.py'
    probe.write_bytes(old)
    base = commit_all(root)
    new = old.replace(b'from math import sin', b'from math import (\n    sin, cos,\n)' if multiline
                      else b'from math import sin, cos')
    probe.write_bytes(new.replace(b'\n', b'\r\n'))
    result = cli(root, 'check', '--review', 'refuse', '--base', base)
    assert result.returncode == 0, result.stdout
    probe.write_bytes(new.replace(b'== 0', b'== 1').replace(b'\n', b'\r\n'))
    assert cli(root, 'check', '--review', 'refuse', '--base', base).returncode == 1


def test_staged_crlf_reuses_working_result_without_copy(local_repo, monkeypatch, capsys):
    root = local_repo
    git(root, 'config', 'core.autocrlf', 'true')
    # Explicit CRLF bytes reproduce Windows checkout on all platforms.
    (root / 'app.py').write_bytes(b'items = []\r\n')
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, '-c', 'print("cache seed")'])))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    git(root, 'add', 'tasks', '.workorder')
    def no_copy(*args, **kwargs):
        pytest.fail('cache hit copied the repository')
    monkeypatch.setattr(gate, '_test_snapshot', no_copy)
    assert gate.check(root, staged=True) == []
    assert re.search(r'\(reused from \d{2}:\d{2} UTC\)', capsys.readouterr().out)


def test_all_tests_does_not_rerun_identical_current_and_ref_commands(local_repo):
    root = local_repo
    counter = root.parent / 'runs.txt'
    (root / 'test_count.py').write_text(
        f'from pathlib import Path; p=Path({str(counter)!r}); '
        'p.write_text(p.read_text()+"run\\n" if p.exists() else "run\\n")')
    command = [PYTHON, 'test_count.py']
    for n in (1, 2):
        fill_sections(task(root, f'TASK-{n}', ships='none', test=store.join_command(command)))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    (root / 'app.py').write_text('items = [1]\n')
    commit_all(root)
    for n in (1, 2):
        ok(root, 'done', '--no-code-change', '--task', f'TASK-{n}')
    counter.unlink()
    result = cli(root, 'check', '--base', base, '--run-tests', '--all-tests')
    assert result.returncode == 0, result.stdout + result.stderr
    assert counter.read_text().splitlines() == ['run']


@pytest.mark.parametrize('action', ['done', 'stop'])
def test_command_edit_without_remote_is_refused_against_head(local_repo, monkeypatch, capsys, action):
    root = local_repo
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, '-c', 'print(1)'])))
    commit_all(root)
    (root / 'tasks/TASK-1.md').write_text((root / 'tasks/TASK-1.md').read_text().replace('print(1)', 'pass'))
    if action == 'done':
        result = cli(root, 'done', '--task', 'TASK-1', '--no-code-change', '--needs-review')
        assert result.returncode == 1, result.stdout
        assert 'test changed:' in result.stdout
    else:
        store.receipt(root, 'TASK-1', 'implemented')
        store.receipt(root, 'TASK-1', 'test', [PYTHON, '-c', 'pass'])
        store.set_task_field(root, 'TASK-1', 'status', 'DONE')
        monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
        assert hooks.run(root, 'stop') == 2
        assert 'test changed:' in capsys.readouterr().err


@pytest.mark.parametrize('attributes', [False, True])
def test_cached_changed_tests_do_not_copy_current_or_ref_code(local_repo, monkeypatch, attributes):
    root = local_repo
    if attributes:
        (root / '.gitattributes').write_text('* text=auto eol=lf\n')
    reviewed_task(root)
    (root / 'test_app.py').write_text('assert 1 == 1\nassert 2 == 2\n')
    commit_all(root)
    ok(root, 'done', '--no-code-change', '--task', 'TASK-1')
    git(root, 'add', 'tasks', '.workorder')
    assert gate.check(root, staged=True) == []
    def no_copy(*args, **kwargs):
        pytest.fail('cached test comparison copied a repository')
    monkeypatch.setattr(gate, '_test_snapshot', no_copy)
    monkeypatch.setattr(gate, '_ref_test_snapshot', no_copy)
    assert gate.check(root, staged=True) == []


@pytest.mark.parametrize('ending', ['lf', 'crlf'])
def test_staged_cache_never_uses_unstaged_code(local_repo, ending):
    root = local_repo
    git(root, 'config', 'core.autocrlf', 'true' if ending == 'crlf' else 'false')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'app.py').write_text('value = 1\n')
    (root / 'test_app.py').write_text('from app import value\nassert value == 1\n')
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, 'test_app.py'])))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    commit_all(root)
    (root / 'app.py').write_text('value = 0\n')
    store.add_note(root, 'TASK-1', 'Staged broken code')
    git(root, 'add', 'app.py', 'tasks')
    (root / 'app.py').write_bytes(b'value = 1\r\n' if ending == 'crlf' else b'value = 1\n')
    result = cli(root, 'check', '--staged')
    assert result.returncode == 1, result.stdout
    assert 'AssertionError' in result.stdout + result.stderr


@pytest.mark.parametrize('change', ['delete', 'mode', 'content'])
def test_recorded_squash_paths_keep_deletions_modes_and_content(local_repo, tmp_path, change):
    root = local_repo
    (root / 'remove.py').write_text('removed = True\n')
    (root / 'run.sh').write_text('#!/bin/sh\nexit 0\n')
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, '-c', 'pass'])))
    base = commit_all(root)
    git(root, 'checkout', '-qb', 'feature')
    (root / 'remove.py').unlink()
    (root / 'app.py').write_text('items = [1]\n')
    git(root, 'update-index', '--chmod=+x', 'run.sh')
    if sys.platform != 'win32':
        (root / 'run.sh').chmod(0o755)
    commit_all(root)
    ok(root, 'done', '--no-code-change', '--task', 'TASK-1')
    implementation = store.entries(root, 'TASK-1')[0]
    commit_all(root)
    git(root, 'checkout', '-q', 'main')
    (root / 'other.py').write_text('other = 1\n')
    commit_all(root)
    git(root, 'merge', '-q', '--squash', 'feature')
    commit_all(root)
    git(root, 'branch', '-D', 'feature')
    clone = tmp_path / 'fresh-paths'
    git(tmp_path, 'clone', '--no-local', '--single-branch', '-q', str(root), str(clone))
    assert not store.has_commit(clone, implementation['commit'])
    ok(clone, 'check')
    git(clone, 'config', 'user.name', 'fresh')
    git(clone, 'config', 'user.email', 'fresh@example.invalid')
    store.set_task_field(clone, 'TASK-1', 'status', 'IN_PROGRESS')
    commit_all(clone)
    if change == 'delete':
        (clone / 'remove.py').write_text('removed = True\n')
    elif change == 'mode':
        git(clone, 'update-index', '--chmod=-x', 'run.sh')
        if sys.platform != 'win32':
            (clone / 'run.sh').chmod(0o644)
    else:
        (clone / 'app.py').write_text('items = [2]\n')
    commit_all(clone)
    store.set_task_field(clone, 'TASK-1', 'status', 'DONE')
    assert cli(clone, 'check').returncode == 1


def test_stop_refuses_command_change_on_unfinished_task(local_repo, monkeypatch, capsys):
    root = local_repo
    reviewed_task(root)
    definition = root / 'tasks/TASK-1.md'
    definition.write_text(definition.read_text().replace('test_app.py', '-c pass'))
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    assert hooks.run(root, 'stop') == 2
    assert 'test changed:' in capsys.readouterr().err


@pytest.mark.parametrize('target', [False, True])
def test_needs_review_always_keeps_pending_status(local_repo, target):
    root = local_repo
    fill_sections(task(root, ships='none', test=store.join_command([PYTHON, '-c', 'pass'])))
    base = commit_all(root)
    if target:
        git(root, 'update-ref', 'refs/remotes/origin/main', base)
    output = ok(root, 'done', '--task', 'TASK-1', '--no-code-change', '--needs-review')
    assert store.record(root, 'TASK-1')['status'] == 'DONE', output
    assert store.entries(root, 'TASK-1')[-1]['pending_review'] is True
    assert 'pending review: the pull request needs workorder-allow:TASK-1' in output


def test_recorded_path_merge_cache_belongs_to_each_receipt(local_repo, tmp_path):
    import shutil
    root = local_repo
    for n in (1, 2):
        fill_sections(task(root, f'TASK-{n}', ships='repo', test=store.join_command([PYTHON, '-c', 'pass'])))
    commit_all(root)
    git(root, 'checkout', '-qb', 'feature')
    (root / 'app.py').write_text('items = [1]\n')
    commit_all(root)
    first = store.receipt(root, 'TASK-1', 'implemented')
    second = store.receipt(root, 'TASK-2', 'implemented')
    commit_all(root)
    git(root, 'checkout', '-q', 'main')
    (root / 'other.py').write_text('other = 1\n')
    commit_all(root)
    git(root, 'merge', '-q', '--squash', 'feature')
    shutil.rmtree(root / '.workorder/receipts/TASK-2')
    commit_all(root)
    git(root, 'branch', '-D', 'feature')
    clone = tmp_path / 'cache-owner'
    git(tmp_path, 'clone', '--no-local', '--single-branch', '-q', str(root), str(clone))
    assert not store.has_commit(clone, first['commit'])
    target = store.head(clone)
    matched = gate.merge_match(clone, first, target)
    assert matched and matched['method'] == 'recorded paths'
    assert gate.merge_match(clone, second, target) is None
    # A different hash for the same task also must not borrow a landing.
    later = {**first, 'at': '2026-11-15T23:59:59+00:00'}
    later['hash'] = store.digest(store.packed({k: v for k, v in later.items() if k != 'hash'}).encode())
    assert gate.merge_match(clone, later, target) is None


def test_staged_checkout_uses_staged_line_ending_rules(local_repo, tmp_path):
    root = local_repo
    (root / '.gitattributes').write_text('*.dat text eol=crlf\n')
    (root / 'sample.dat').write_bytes(b'first\nsecond\n')
    commit_all(root)
    # The staged checkout must not use this unstaged attribute change.
    (root / '.gitattributes').write_text('*.dat text eol=lf\n')
    destination = tmp_path / 'staged-endings'
    gate._test_snapshot(root, destination, staged=True)
    assert (destination / 'sample.dat').read_bytes() == b'first\r\nsecond\r\n'
