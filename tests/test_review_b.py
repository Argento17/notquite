"""Independent 0.4 review reproductions, using only disposable local Git repos."""
import sys
import os
import shutil
import subprocess
import pytest

from workorder import gate, store
from test_workorder import PYTHON, cli, clones, git, local_repo, ok, task
from test_v04 import feature, move_main
from test_v05 import commit_all, finished


def land(root, method, early=False, status='CLOSED'):
    base = feature(root, multi=True)
    if not early:
        task(root, ships='repo', status='IN_PROGRESS')
        commit_all(root, 'Leave acceptance open')
    elif status != 'CLOSED':
        task(root, ships='repo', status=status)
        commit_all(root, 'Claim completion before landing')
    if method == 'same code':
        git(root, 'checkout', '-q', 'main')
    else:
        move_main(root)
    if method == 'ancestry':
        git(root, 'merge', '-q', '--no-ff', '--no-edit', 'feature')
    elif method == 'rebase':
        git(root, 'checkout', '-q', 'feature')
        git(root, 'rebase', '-q', 'main')
        git(root, 'checkout', '-q', 'main')
        git(root, 'merge', '-q', '--ff-only', 'feature')
    else:
        git(root, 'merge', '-q', '--squash', 'feature')
        commit_all(root, 'Squash feature')
    assert f'by {method}' in ok(root, 'show', '--task', 'TASK-1')
    return base


@pytest.mark.parametrize('method', ['ancestry', 'same code', 'squash', 'rebase'])
@pytest.mark.parametrize('status', ['DONE', 'CLOSED'])
@pytest.mark.parametrize('change', ['revert', 'delete', 'rename'])
def test_late_claim_refuses_changed_implementation_paths(local_repo, method, status, change):
    root = local_repo
    land(root, method)
    if change == 'revert':
        (root / 'app.py').write_text('items = []\n')
    elif change == 'delete':
        (root / 'more.py').unlink()
    else:
        (root / 'app.py').rename(root / 'renamed.py')
    task(root, ships='repo', status=status)
    commit_all(root, 'Undo tested code and claim finished')
    result = cli(root, 'check')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'on code other than the implemented commit' in result.stdout
    assert 'renew --task TASK-1' in cli(root, 'check', 'TASK-1').stdout


@pytest.mark.parametrize('method', ['ancestry', 'same code', 'squash', 'rebase'])
def test_late_claim_allows_changes_outside_implementation_paths(local_repo, method):
    root = local_repo
    land(root, method)
    (root / 'other.py').write_text('unrelated = 2\n')
    task(root, ships='repo', status='CLOSED')
    commit_all(root, 'Claim after unrelated work')
    ok(root, 'check')
    ok(root, 'check', 'TASK-1')


@pytest.mark.parametrize('method', ['ancestry', 'same code', 'squash', 'rebase'])
def test_early_claim_survives_later_implementation_revert(local_repo, method):
    root = local_repo
    land(root, method, early=True)
    (root / 'app.py').write_text('items = []\n')
    (root / 'more.py').unlink()
    commit_all(root, 'Later work does not reopen a historical claim')
    ok(root, 'check')
    ok(root, 'check', 'TASK-1')


@pytest.mark.parametrize('method', ['ancestry', 'same code', 'squash', 'rebase'])
@pytest.mark.parametrize('mode', ['committed', 'working', 'staged'])
def test_closed_is_a_new_claim_after_done_when_tested_files_change(local_repo, method, mode):
    root = local_repo
    land(root, method, early=True, status='DONE')
    (root / 'app.py').write_text('items = []\n')
    if mode != 'committed':
        commit_all(root, 'Revert tested code')
    task(root, ships='repo', status='CLOSED')
    if mode == 'committed':
        commit_all(root, 'Close on reverted code')
    elif mode == 'staged':
        git(root, 'add', 'tasks')
    result = cli(root, 'check', *(['--staged'] if mode == 'staged' else []))
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'implemented commit' in result.stdout


def test_claim_valid_before_ancestry_merge_stays_valid_after_landing(local_repo):
    root = local_repo
    feature(root, ships='none', multi=True)
    task(root, ships='none', status='IN_PROGRESS')
    commit_all(root, 'Acceptance still open')
    move_main(root)
    git(root, 'checkout', '-q', 'feature')
    git(root, 'merge', '-q', '--no-edit', 'main')
    task(root, ships='none', status='DONE')
    commit_all(root, 'Claim with equivalent implementation patch')
    ok(root, 'check')
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '-q', '--no-ff', '--no-edit', 'feature')
    ok(root, 'check')


def test_fast_forward_claim_allows_existing_unrelated_file_change(local_repo):
    root = local_repo
    (root / 'unrelated.py').write_text('value = 1\n')
    commit_all(root, 'Pre-existing unrelated code')
    feature(root, multi=True)
    task(root, ships='repo', status='IN_PROGRESS')
    commit_all(root, 'Acceptance still open')
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '-q', '--ff-only', 'feature')
    (root / 'unrelated.py').write_text('value = 2\n')
    task(root, ships='repo', status='CLOSED')
    commit_all(root, 'Claim after unrelated update')
    ok(root, 'check')
    # Changing an earlier feature commit's file must still be protected.
    task(root, ships='repo', status='IN_PROGRESS')
    commit_all(root, 'Reopen')
    (root / 'app.py').write_text('items = []\n')
    task(root, ships='repo', status='CLOSED')
    commit_all(root, 'Claim after reverting first feature commit')
    result = cli(root, 'check')
    assert result.returncode == 1 and 'on code other than' in result.stdout


def test_restoring_implementation_snapshot_does_not_bypass_landing_check(local_repo):
    root = local_repo
    feature(root)
    task(root, ships='repo', status='IN_PROGRESS')
    commit_all(root, 'Acceptance still open')
    git(root, 'checkout', '-q', 'main')
    (root / 'app.py').write_text("items = ['main']\n")
    commit_all(root, 'Concurrent change')
    conflict = subprocess.run(['git', 'merge', '--no-ff', '--no-edit', 'feature'], cwd=root, capture_output=True)
    assert conflict.returncode == 1
    (root / 'app.py').write_text("items = ['main', 'tested']\n")
    commit_all(root, 'Resolve merge')
    (root / 'app.py').write_text("items = ['tested']\n")
    task(root, ships='repo', status='CLOSED')
    commit_all(root, 'Restore implementation snapshot and claim')
    result = cli(root, 'check')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'on code other than' in result.stdout


def test_later_same_code_match_does_not_move_a_valid_claims_landing(local_repo):
    root = local_repo
    land(root, 'same code')
    landing = store.head(root)
    (root / 'unrelated.py').write_text('value = 1\n')
    task(root, ships='repo', status='CLOSED')
    commit_all(root, 'Claim after unrelated work')
    ok(root, 'check')
    (root / 'unrelated.py').unlink()
    commit_all(root, 'Restore same tree later')
    ok(root, 'check')
    match = gate.computed_merge(root, store.record(root, 'TASK-1'), store.entries(root, 'TASK-1')[0])
    assert match['commit'] == landing


@pytest.mark.parametrize('directory', ['.workorder', '.notquite'])
@pytest.mark.parametrize('damage', ['delete', 'modify-restore', 'type-restore'])
def test_base_refuses_receipt_tampering_between_snapshots(local_repo, directory, damage):
    root = local_repo
    task(root, ships='none')
    base = commit_all(root, 'Before evidence')
    finished(root)
    result = cli(root, 'receipt', 'test', '--task', 'TASK-1', '--', PYTHON, '-c', 'raise SystemExit(1)')
    assert result.returncode == 1
    receipt = max((root / '.workorder/receipts/TASK-1').glob('*.json'), key=lambda p: p.name)
    if directory == '.notquite':
        target = root / directory / 'receipts/TASK-1' / receipt.name
        target.parent.mkdir(parents=True)
        receipt.rename(target)
        receipt = target
    commit_all(root, 'Record failed test')
    if damage == 'delete':
        receipt.unlink()
        commit_all(root, 'Hide failed test')
    else:
        original = receipt.read_bytes()
        if damage == 'type-restore':
            receipt.unlink()
            receipt.symlink_to('missing-receipt')
        else:
            receipt.write_text('{}\n')
        commit_all(root, 'Tamper with receipt')
        if damage == 'type-restore':
            receipt.unlink()
        receipt.write_bytes(original)
        # A later pass makes the final snapshot valid, but cannot excuse the tampering.
        commit_all(root, 'Restore receipt')
        ok(root, 'receipt', 'test', '--task', 'TASK-1', '--', PYTHON, '-c', 'pass')
        commit_all(root, 'Passing retry')
    result = cli(root, 'check', '--base', base)
    assert result.returncode == 1, result.stdout + result.stderr
    assert receipt.relative_to(root).as_posix() in result.stdout
    assert 'removed or changed' in result.stdout


def test_non_utf8_text_after_squash_does_not_crash_merge_search(local_repo):
    root = local_repo
    land(root, 'squash', early=True)
    (root / 'text.dat').write_bytes(b'\xff')
    commit_all(root, 'Non-UTF-8 text diff')
    assert 'by squash' in ok(root, 'check', 'TASK-1')


@pytest.mark.skipif(sys.platform == "win32", reason="Windows file names cannot hold non-UTF-8 bytes")
def test_non_utf8_code_path_can_be_fingerprinted(local_repo):
    root = local_repo
    (root / os.fsdecode(b'code-\xff.txt')).write_bytes(b'\xff')
    commit_all(root, 'Non-UTF-8 path and content')
    task(root, ships='none')
    ok(root, 'receipt', 'implemented', '--task', 'TASK-1')
    ok(root, 'receipt', 'test', '--task', 'TASK-1', '--', PYTHON, '-c', 'pass')
    task(root, ships='none', status='DONE')
    commit_all(root, 'Finish')
    ok(root, 'check')


def test_whole_task_retirement_exempts_intermediate_receipt_deletion(local_repo):
    root = local_repo
    base = finished(root)
    failed = cli(root, 'receipt', 'test', '--task', 'TASK-1', '--', PYTHON, '-c', 'raise SystemExit(1)')
    assert failed.returncode == 1
    commit_all(root, 'Record later failure')
    (root / 'tasks/TASK-1.md').unlink()
    shutil.rmtree(root / '.workorder/receipts/TASK-1')
    commit_all(root, 'Remove complete task and receipts')
    assert cli(root, 'check', '--base', base).returncode == 1
    ok(root, 'check', '--base', base, '--allow', 'TASK-1')


def test_origin_head_cannot_make_feature_its_own_merge_target(local_repo):
    root = local_repo
    feature(root)
    git(root, 'update-ref', 'refs/remotes/origin/feature', 'feature')
    git(root, 'symbolic-ref', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/feature')
    result = cli(root, 'check', 'TASK-1')
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'merge of the tested code' in result.stdout


@pytest.mark.parametrize('remote', ['main', 'master'])
def test_remote_targets_precede_local_main(local_repo, remote):
    root = local_repo
    feature(root)
    git(root, 'update-ref', f'refs/remotes/origin/{remote}', 'main')
    git(root, 'checkout', '-q', 'main')
    git(root, 'merge', '-q', '--no-ff', '--no-edit', 'feature')
    assert cli(root, 'check', 'TASK-1').returncode == 1
    git(root, 'update-ref', f'refs/remotes/origin/{remote}', 'main')
    assert f'merged into origin/{remote}' in ok(root, 'check', 'TASK-1')


def test_ancestry_match_keeps_the_landing_commit(local_repo):
    root = local_repo
    land(root, 'ancestry', early=True)
    landing = store.head(root)
    (root / 'app.py').write_text('items = []\n')
    commit_all(root, 'Undo later')
    match = gate.computed_merge(root, store.record(root, 'TASK-1'), store.entries(root, 'TASK-1')[0])
    assert match['commit'] == landing


def test_remote_merge_ref_must_include_branch(local_repo):
    root = local_repo
    feature(root)
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace('ships: "repo"', 'ships: "repo"\nmerge_ref: refs/remotes/origin'))
    git(root, 'update-ref', 'refs/remotes/origin', 'main')
    result = cli(root, 'check', 'TASK-1')
    assert result.returncode == 1
    assert 'merge_ref must be a full branch ref' in result.stdout + result.stderr
    assert 'Traceback' not in result.stderr


@pytest.mark.parametrize('status', ['DONE', 'CLOSED', 'CANCELLED'])
@pytest.mark.parametrize('command', ['claim', 'ready'])
def test_released_upstream_finished_task_is_not_free(clones, status, command):
    one, two = clones
    git(two, 'config', 'user.name', 'agent-b')
    task(two, 'TASK-2', ships='none')
    commit_all(two, 'Open second task')
    git(two, 'push', '-q', 'origin', 'main')
    git(one, 'pull', '-q', '--ff-only')
    ok(two, 'claim', '--task', 'TASK-2')
    ok(one, 'claims')  # The claim tag is already cached in A's clone.
    old_tip = store.head(one)
    finished(two, 'TASK-2', status=status)
    git(two, 'push', '-q', 'origin', 'main')
    ok(two, 'release', '--task', 'TASK-2')
    if command == 'claim':
        result = cli(one, 'claim', '--task', 'TASK-2')
        assert result.returncode == 1, result.stdout + result.stderr
        assert 'origin/main has newer work on TASK-2 (2 receipts, 0 notes, by agent-b); pull first' in result.stderr
        assert len([ref for ref in store.remote_refs(one) if '/claims/TASK-2/' in ref]) == 1
    else:
        output = ok(one, 'ready')
        assert 'TASK-2 finished on origin/main; pull to update' in output
        assert 'TASK-1 free' in output
        assert 'TASK-2 free' not in output
    assert store.head(one) == old_tip
    assert store.record(one, 'TASK-2')['status'] == 'IN_PROGRESS'


def test_ready_offline_ignores_old_upstream_snapshot(clones):
    one, two = clones
    finished(two)
    git(two, 'push', '-q', 'origin', 'main')
    assert 'finished on origin/main' in ok(one, 'ready')
    git(one, 'remote', 'set-url', 'origin', str(one / 'missing.git'))
    output = ok(one, 'ready')
    assert '(claims not shown: origin could not be reached)' in output
    assert 'TASK-1 free' in output


def test_upstream_status_and_uncached_claim_share_one_fetch(clones, monkeypatch):
    one, two = clones
    ok(two, 'claim', '--task', 'TASK-1')
    original = store.git
    fetches = []

    def counted(root, *args, **kwargs):
        if args[0] == 'fetch':
            fetches.append(args)
        return original(root, *args, **kwargs)

    monkeypatch.setattr(store, 'git', counted)
    with pytest.raises(store.Refused, match='held by two'):
        store.claim(one, 'TASK-1')
    assert len(fetches) == 1
    assert '+refs/heads/main:refs/workorder/upstream' in fetches[0]
    assert any(arg.startswith('refs/tags/workorder/claims/TASK-1/') for arg in fetches[0])
