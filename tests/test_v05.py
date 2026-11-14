"""Round-seven regressions. All repositories and refs are local."""
import json
import shutil
import subprocess

import pytest

from workorder import gate, store
from workorder.cli import main
from test_workorder import PYTHON, cli, git, local_repo, ok, task
from test_v04 import feature, move_main, raw_receipt


def evidence(root, tid='TASK-1'):
    first = raw_receipt(root, task=tid)
    tested = raw_receipt(root, 'test', task=tid, parents=[first['hash']], at=1)
    for item in (first, tested):
        path = root / '.workorder/receipts' / tid / store.receipt_name(item)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(store.packed(item) + '\n', encoding='utf-8')


def commit_all(root, message='Record'):
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', message)
    return store.head(root)


def finished(root, tid='TASK-1', status='DONE', test=None):
    task(root, tid, ships='none', status=status, test=test or f'{PYTHON} -c pass')
    evidence(root, tid)
    return commit_all(root)


def test_git_subprocess_budget_for_finished_tasks(local_repo, monkeypatch):
    # Unique code/claim commits per task, two valid receipts each. Count real
    # subprocesses including binary cat-file calls, not just the git wrapper.
    root = local_repo
    counts = []
    original = subprocess.run
    for stop in (10, 30):
        start = 1 if stop == 10 else 11
        for n in range(start, stop + 1):
            (root / 'app.py').write_text(f'items = [{n}]\n')
            commit_all(root, f'Code {n}')
            finished(root, f'TASK-{n}')
        calls = []
        def counted(args, *a, **kw):
            if args[0] == 'git':
                calls.append(args)
            return original(args, *a, **kw)
        with monkeypatch.context() as patch:
            patch.chdir(root)
            patch.setattr(subprocess, 'run', counted)
            store._PRESENT.clear()
            store._DIGESTS.clear()
            assert main(['check']) == 0
        counts.append(len(calls))
    assert counts[1] <= 8 * 30 + 30, counts  # at most 8/task plus 30 shared calls
    assert counts[1] <= 3 * counts[0] + 10, counts


@pytest.mark.parametrize('method', ['squash', 'rebase'])
@pytest.mark.parametrize('checkout', ['new', 'original', 'detached'])
def test_feature_checkout_recognises_target_merge(local_repo, method, checkout):
    root = local_repo
    feature(root, multi=True)
    move_main(root)
    if method == 'squash':
        git(root, 'merge', '-q', '--squash', 'feature')
        git(root, 'commit', '-qm', 'Squashed')
    else:
        git(root, 'checkout', '-q', 'feature')
        git(root, 'rebase', '-q', 'main')
        git(root, 'checkout', '-q', 'main')
        git(root, 'merge', '-q', '--ff-only', 'feature')
    if checkout == 'new':
        git(root, 'checkout', '-q', '-b', 'feat/next')
    elif checkout == 'original':
        git(root, 'checkout', '-q', 'feature')
    else:
        git(root, 'checkout', '-q', '--detach', 'feature')
    ok(root, 'check')
    ok(root, 'check', 'TASK-1')
    assert 'Evidence: CLOSED' in ok(root, 'show', '--task', 'TASK-1')
    assert 'TASK-1' not in ok(root, 'ready')


@pytest.mark.parametrize('update', ['rebase', 'merge'])
def test_finished_branch_rebased_over_readme_keeps_claim(local_repo, update):
    root = local_repo
    feature(root, ships='none')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('test: "any"', 'test: ' + json.dumps(f'{PYTHON} -c pass')))
    git(root, 'commit', '-qam', 'Declare the recorded test')
    git(root, 'checkout', '-q', 'main')
    (root / 'README.md').write_text('An unrelated explanation.\n')
    commit_all(root, 'Docs')
    git(root, 'update-ref', 'refs/remotes/origin/main', 'main')
    git(root, 'checkout', '-q', 'feature')
    if update == 'rebase':
        git(root, 'rebase', '-q', 'main')
    else:
        git(root, 'merge', '-q', '--no-edit', 'main')
    result = ok(root, 'check', '--base', 'origin/main', '--run-tests', '--allow', 'TASK-1')
    assert 'its test passed when this check ran it' in result


def test_changed_patch_is_not_a_rebased_claim(local_repo):
    root = local_repo
    feature(root, ships='none')
    move_main(root)
    git(root, 'checkout', '-q', 'feature')
    git(root, 'rebase', '-q', 'main')
    (root / 'app.py').write_text("items = ['different']\n")
    git(root, 'commit', '-qam', 'Rewrite claim code', '--amend')
    refused = cli(root, 'check')
    assert refused.returncode == 1 and 'on code other than the implemented commit' in refused.stdout


def test_unpushed_squash_advises_pushing_main(local_repo):
    root = local_repo
    feature(root)
    move_main(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'main')
    git(root, 'merge', '-q', '--squash', 'feature')
    git(root, 'commit', '-qm', 'Local squash')
    for output in (cli(root, 'check', 'TASK-1').stdout, ok(root, 'show', '--task', 'TASK-1')):
        assert 'push main' in output
        assert 'renew --task' not in output
    assert cli(root, 'check').returncode == 1


@pytest.mark.parametrize('mode,count,claimed', [([], 3, 2), (['TASK-1'], 1, 1), (['--staged'], 1, 1), (['--base'], 1, 1)])
def test_check_reports_scope(local_repo, mode, count, claimed):
    root = local_repo
    finished(root)
    finished(root, 'TASK-2', status='CLOSED')
    task(root, 'TASK-3', ships='none')
    base = commit_all(root)
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text() + '\nA progress note.\n')
    git(root, 'add', 'tasks')
    args = [*mode, base] if mode == ['--base'] else mode
    output = ok(root, 'check', *args)
    assert output.splitlines()[-1] == f'checked {count} task{"" if count == 1 else "s"} ({claimed} claimed DONE or CLOSED)'
    assert 'ran ' not in output


@pytest.mark.parametrize('kind,expected', [('pass', 1), ('fail', 1), ('any', 0), ('unstarted', 0), ('empty', 0)])
def test_check_reports_actual_tests_run(local_repo, kind, expected):
    root = local_repo
    if kind == 'fail':
        command = f'{PYTHON} -c "raise SystemExit(4)"'
    elif kind == 'unstarted':
        command = 'workorder-missing-test-executable'
    elif kind == 'any':
        command = 'any'
    else:
        command = f'{PYTHON} -c pass'
    task(root, ships='none', test=command)
    base = commit_all(root)
    if kind != 'empty':
        task(root, ships='none', status='DONE', test=command)
        first = raw_receipt(root)
        tested = raw_receipt(root, 'test', parents=[first['hash']], at=1,
                             command=store.split_command(command) if command != 'any' else [PYTHON, '-c', 'pass'])
        for item in (first, tested):
            store.write_receipt(root, 'TASK-1', item)
        commit_all(root)
    result = cli(root, 'check', '--base', base, '--run-tests')
    assert result.returncode == (0 if kind in {'pass', 'empty'} else 1)
    assert f'; ran {expected} declared test{"" if expected == 1 else "s"}' in result.stdout
    assert ('no declared test ran in this check' in result.stdout) == (expected == 0)


@pytest.mark.parametrize('explicit', [False, True])
@pytest.mark.parametrize('change', ['edit', 'delete', 'rename', 'add'])
def test_base_guards_test_files(local_repo, explicit, change):
    root = local_repo
    folder = root / 'tests'
    folder.mkdir()
    probe = folder / 'test example.py'
    probe.write_text('assert 1 == 1\nassert 2 == 2\n')
    path = task(root, ships='none', test='python -m pytest "tests/test example.py"' if not explicit else 'any')
    if explicit:
        path.write_text(path.read_text().replace('test: "any"', 'test: "any"\ntest_paths: ["tests"]'))
    base = commit_all(root)
    if change == 'edit':
        probe.write_text('assert True\nassert 2 == 2\n')
    elif change == 'delete':
        probe.unlink()
    elif change == 'rename':
        probe.rename(folder / 'renamed.py')
    else:
        probe.write_text(probe.read_text() + 'assert 3 == 3\n')
    commit_all(root)
    result = cli(root, 'check', '--base', base)
    assert result.returncode == (0 if change == 'add' else 1), result.stdout + result.stderr
    if change != 'add':
        lost = 1 if change == 'edit' else 2
        assert f'TASK-1: its test files lost or changed {lost} lines since {base}' in result.stdout
        assert 'tests/test example.py' in result.stdout
        assert 'a reviewer accepts it with --allow TASK-1' in result.stdout
        ok(root, 'check', '--base', base, '--allow', 'TASK-1')
    assert 'checked 1 task ' in result.stdout


def test_test_file_change_selects_dependents(local_repo):
    root = local_repo
    (root / 'test_app.py').write_text('assert True\n')
    task(root, ships='none', test='python test_app.py')
    task(root, 'TASK-2', ships='none', status='DONE', waits=['task:TASK-1'])
    base = commit_all(root)
    (root / 'test_app.py').write_text('assert True\nassert 1\n')
    commit_all(root)
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1 and 'TASK-2 says DONE' in result.stdout
    assert 'checked 2 tasks' in result.stdout


@pytest.mark.parametrize('status', ['DONE', 'CLOSED'])
def test_new_finished_definition_needs_allow(local_repo, status):
    root = local_repo
    base = store.head(root)
    finished(root, status=status)
    refused = cli(root, 'check', '--base', base)
    assert refused.returncode == 1
    assert (f'TASK-1 was created and claimed {status} in the same change; '
            'a reviewer accepts its definition with --allow TASK-1') in refused.stdout
    ok(root, 'check', '--base', base, '--allow', 'TASK-1')


@pytest.mark.parametrize('status', ['DONE', 'CLOSED', 'CANCELLED'])
@pytest.mark.parametrize('committed', [False, True])
def test_allow_removal_of_finished_task_and_all_receipts(local_repo, status, committed):
    root = local_repo
    base = finished(root, status=status)
    (root / 'tasks/TASK-1.md').unlink()
    shutil.rmtree(root / '.workorder')
    if committed:
        commit_all(root)
    refused = cli(root, 'check', '--base', base)
    assert refused.returncode == 1
    ok(root, 'check', '--base', base, '--allow', 'TASK-1')


@pytest.mark.parametrize('partial', ['task-only', 'receipts-only', 'unfinished'])
def test_allow_does_not_excuse_partial_or_unfinished_removal(local_repo, partial):
    root = local_repo
    base = finished(root, status='IN_PROGRESS' if partial == 'unfinished' else 'DONE')
    if partial != 'receipts-only':
        (root / 'tasks/TASK-1.md').unlink()
    if partial != 'task-only':
        shutil.rmtree(root / '.workorder')
    commit_all(root)
    assert cli(root, 'check', '--base', base, '--allow', 'TASK-1').returncode == 1


def test_removed_cancelled_task_without_receipts_needs_allow(local_repo):
    root = local_repo
    task(root, ships='none', status='CANCELLED')
    base = commit_all(root)
    (root / 'tasks/TASK-1.md').unlink()
    commit_all(root)
    assert cli(root, 'check', '--base', base).returncode == 1
    ok(root, 'check', '--base', base, '--allow', 'TASK-1')


@pytest.mark.parametrize('cycle', ['self', 'pair', 'overlap'])
def test_cycles_report_participants_and_keep_other_tasks(local_repo, cycle):
    root = local_repo
    task(root, ships='none', waits=['task:TASK-1' if cycle == 'self' else 'task:TASK-2'])
    task(root, 'TASK-2', ships='none', waits=['task:TASK-1'] if cycle != 'self' else [])
    if cycle == 'overlap':
        task(root, 'TASK-2', ships='none', waits=['task:TASK-1', 'task:TASK-5'])
        task(root, 'TASK-5', ships='none', waits=['task:TASK-2'])
    task(root, 'TASK-3', ships='none')
    task(root, 'TASK-4', ships='none', status='DONE')
    result = cli(root, 'check')
    assert result.returncode == 1 and 'TASK-4 says DONE' in result.stdout
    assert 'TASK-1: dependency cycle' in result.stdout
    if cycle != 'self':
        assert 'TASK-2: dependency cycle' in result.stdout
    if cycle == 'overlap':
        assert 'TASK-5: dependency cycle' in result.stdout
    listed = ok(root, 'ready')
    assert 'TASK-3 free' in listed and 'TASK-4 says DONE' in listed
    assert 'TASK-1' in listed and 'dependency cycle' in listed


def test_squashed_done_without_delivery_stays_done_on_a_feature_checkout(local_repo):
    root = local_repo
    feature(root, ships='none', multi=True)
    move_main(root)
    git(root, 'merge', '-q', '--squash', 'feature')
    git(root, 'commit', '-qm', 'Squash completed work')
    git(root, 'checkout', '-q', '-b', 'feat/next')
    ok(root, 'check')
    assert 'Evidence: CLOSED' in ok(root, 'show', '--task', 'TASK-1')
    assert 'TASK-1' not in ok(root, 'ready')


def test_ancestry_alone_does_not_excuse_stale_claim_on_main(local_repo):
    root = local_repo
    task(root, ships='repo')
    evidence(root)
    commit_all(root)
    (root / 'app.py').write_text("items = ['untested']\n")
    task(root, ships='repo', status='DONE')
    commit_all(root)
    result = cli(root, 'check')
    assert result.returncode == 1 and 'on code other than the implemented commit' in result.stdout


def test_binary_test_additions_pass_but_edits_need_allow(local_repo):
    root = local_repo
    folder = root / 'tests'
    folder.mkdir()
    path = task(root, ships='none', test='any')
    path.write_text(path.read_text().replace('test: "any"', 'test: "any"\ntest_paths: ["tests"]'))
    base = commit_all(root)
    binary = folder / 'fixture.bin'
    binary.write_bytes(b'\0original\xff')
    commit_all(root)
    ok(root, 'check', '--base', base)
    base = store.head(root)
    binary.write_bytes(b'\0changed\xff')
    commit_all(root)
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1 and 'changed binary content' in result.stdout
    ok(root, 'check', '--base', base, '--allow', 'TASK-1')


def test_empty_test_rename_needs_allow(local_repo):
    root = local_repo
    test = root / 'test_empty.py'
    test.touch()
    task(root, ships='none', test='python test_empty.py')
    base = commit_all(root)
    test.rename(root / 'test_renamed.py')
    commit_all(root)
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1 and 'lost or changed 0 lines' in result.stdout
    ok(root, 'check', '--base', base, '--allow', 'TASK-1')


def test_base_test_paths_still_guard_files_after_paths_removed(local_repo):
    root = local_repo
    probe = root / 'test_app.py'
    probe.write_text('assert True\n')
    path = task(root, ships='none', test='any')
    path.write_text(path.read_text().replace('test: "any"', 'test: "any"\ntest_paths: [test_app.py]'))
    base = commit_all(root)
    path.write_text(path.read_text().replace('[test_app.py]', '[]'))
    probe.write_text('pass\n')
    commit_all(root)
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1 and 'test_paths changed' in result.stdout
    assert 'lost or changed 1 lines' in result.stdout
    ok(root, 'check', '--base', base, '--allow', 'TASK-1')


@pytest.mark.parametrize('explicit', [False, True])
def test_test_paths_normalise_parent_components(local_repo, explicit):
    root = local_repo
    folder = root / 'tests'
    folder.mkdir()
    probe = folder / 'test_app.py'
    probe.write_text('assert True\n')
    path = task(root, ships='none', test='any' if explicit else 'python tests/../tests/test_app.py')
    if explicit:
        path.write_text(path.read_text().replace('test: "any"', 'test: "any"\ntest_paths: ["tests/../tests"]'))
    base = commit_all(root)
    probe.write_text('pass\n')
    commit_all(root)
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1 and 'lost or changed 1 lines' in result.stdout
    ok(root, 'check', '--base', base, '--allow', 'TASK-1')


def test_old_squash_does_not_excuse_reopened_claim_on_new_code(local_repo):
    root = local_repo
    feature(root)
    move_main(root)
    git(root, 'merge', '-q', '--squash', 'feature')
    git(root, 'commit', '-qm', 'Squash')
    git(root, 'checkout', '-q', '-b', 'feat/followup')
    task(root, ships='repo', status='IN_PROGRESS')
    commit_all(root, 'Reopen')
    (root / 'app.py').write_text("items = ['untested replacement']\n")
    task(root, ships='repo', status='CLOSED')
    commit_all(root, 'Reclaim without testing')
    refused = cli(root, 'check')
    assert refused.returncode == 1 and 'on code other than the implemented commit' in refused.stdout
    assert 'says CLOSED, which the evidence does not support' in ok(root, 'ready')
