"""Task-local hook checks, decoding errors, and byte-preserving task edits."""

import io
import sys

import pytest

from workorder import gate, hooks, store
from test_workorder import PYTHON, cli, clones, git, local_repo, ok, task
from test_v05 import commit_all
from test_v07b_setup import local_claim, refuse_network


def stop(root, monkeypatch):
    monkeypatch.setattr(sys, 'stdin', io.StringIO('{}'))
    refuse_network(monkeypatch)
    return hooks.run(root, 'stop')


@pytest.mark.parametrize('other_change', ['none', 'code', 'receipt'])
def test_stop_ignores_unchanged_unheld_task_with_old_false_claim(
        local_repo, monkeypatch, capsys, other_change):
    task(local_repo, status='DONE', ships='none')
    commit_all(local_repo)
    git(local_repo, 'checkout', '-q', '-b', 'feature')
    if other_change == 'code':
        (local_repo / 'app.py').write_bytes(b'changed = True\n')
    elif other_change == 'receipt':
        store.receipt(local_repo, 'TASK-1', 'implemented')
    assert stop(local_repo, monkeypatch) == 0
    assert capsys.readouterr().err == ''


@pytest.mark.parametrize('change', ['working', 'staged', 'committed', 'new'])
def test_stop_blocks_task_file_changes_against_target(local_repo, monkeypatch, capsys, change):
    path = task(local_repo, ships='none')
    if change != 'new':
        commit_all(local_repo)
    git(local_repo, 'checkout', '-q', '-b', 'feature')
    path.write_bytes(path.read_bytes().replace(b'"IN_PROGRESS"', b'"DONE"'))
    if change in {'staged', 'committed'}:
        git(local_repo, 'add', 'tasks')
    if change == 'committed':
        commit_all(local_repo)
    assert stop(local_repo, monkeypatch) == 1
    assert 'NOT QUITE  TASK-1' in capsys.readouterr().err


def test_stop_checks_held_task_even_when_unchanged(local_repo, monkeypatch, capsys):
    task(local_repo, status='DONE', ships='none')
    commit_all(local_repo)
    local_claim(local_repo, 'TASK-1')
    assert stop(local_repo, monkeypatch) == 1
    assert 'NOT QUITE  TASK-1' in capsys.readouterr().err


def test_stop_respects_task_merge_target(local_repo, monkeypatch, capsys):
    path = task(local_repo, status='DONE', ships='none')
    path.write_bytes(path.read_bytes().replace(b'contract:', b'merge_ref: refs/heads/release\ncontract:'))
    commit_all(local_repo)
    git(local_repo, 'branch', 'release')
    git(local_repo, 'checkout', '-q', '-b', 'feature')
    path.write_bytes(path.read_bytes() + b'\nA local task change.\n')
    commit_all(local_repo)
    git(local_repo, 'update-ref', 'refs/heads/main', 'HEAD')
    assert stop(local_repo, monkeypatch) == 1
    assert 'NOT QUITE  TASK-1' in capsys.readouterr().err


def test_session_start_shows_only_held_tasks_without_ready_scan(local_repo, monkeypatch, capsys):
    task(local_repo, 'TASK-1', ships='none')
    task(local_repo, 'TASK-2', ships='none', status='DONE')
    local_claim(local_repo, 'TASK-1')
    local_claim(local_repo, 'TASK-2', token='another-clone')

    def no_ready(*args, **kwargs):
        pytest.fail('SessionStart scanned ready instead of displaying only held tasks')

    monkeypatch.setattr(gate, 'ready', no_ready)
    refuse_network(monkeypatch)
    assert hooks.run(local_repo, 'session-start') == 0
    output = capsys.readouterr().out
    assert output.count('workorder ready') == 1
    assert 'workorder show --task TASK-1' in output
    assert 'TASK-1 IN_PROGRESS (not claimed finished)' in output
    assert 'TASK-2' not in output


@pytest.mark.parametrize('mode', ['working', 'staged', 'committed', 'base'])
def test_check_reports_undecodable_task_only_and_checks_others(local_repo, mode):
    bad = task(local_repo, 'TASK-1', ships='none')
    task(local_repo, 'TASK-2', ships='none', status='DONE')
    base = store.head(local_repo)
    bad.write_bytes(bad.read_bytes() + b'\nInvalid UTF-8: \xff\n')
    if mode in {'staged', 'committed', 'base'}:
        git(local_repo, 'add', 'tasks')
    if mode in {'committed', 'base'}:
        commit_all(local_repo)
    args = ['check'] + (['--staged'] if mode == 'staged' else ['--base', base] if mode == 'base' else [])
    result = cli(local_repo, *args)
    output = result.stdout + result.stderr
    assert result.returncode == 1
    assert 'Traceback' not in output
    assert 'tasks/TASK-1.md' in output and 'UTF-8' in output
    assert 'NOT QUITE  TASK-2' in output
    assert 'TASK-2: tasks/TASK-1.md' not in output


def test_staged_record_batch_keeps_other_tasks_readable(local_repo):
    bad = task(local_repo, 'TASK-1')
    task(local_repo, 'TASK-2')
    bad.write_bytes(bad.read_bytes() + b'\xff')
    git(local_repo, 'add', 'tasks')
    errors = {}
    with store.read_session():
        actual = gate.records(local_repo, staged=True, errors=errors)
    assert set(actual) == {'TASK-2'}
    assert set(errors) == {'TASK-1'}
    assert 'tasks/TASK-1.md' in errors['TASK-1']


def test_bad_historical_blob_does_not_poison_supported_task(local_repo):
    bad = task(local_repo, 'TASK-1', ships='none')
    task(local_repo, 'TASK-2', ships='none', test=f'{PYTHON} -c pass')
    before = bad.read_bytes()
    bad.write_bytes(before + b'\xff')
    commit_all(local_repo)
    bad.write_bytes(before)
    store.receipt(local_repo, 'TASK-2', 'implemented')
    store.receipt(local_repo, 'TASK-2', 'test', [PYTHON, '-c', 'pass'])
    store.set_task_field(local_repo, 'TASK-2', 'status', 'DONE')
    result = cli(local_repo, 'check')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('status', ['DONE', 'CLOSED'])
def test_ready_same_upstream_status_reports_local_evidence_not_pull(clones, status):
    one, _ = clones
    task(one, status=status, ships='none')
    commit_all(one)
    git(one, 'push', '-q', 'origin', 'main')
    output = ok(one, 'ready')
    assert 'pull to update' not in output
    assert f'TASK-1 says {status}, which the evidence does not support' in output


@pytest.mark.parametrize('newline', [b'\n', b'\r\n'])
@pytest.mark.parametrize('bom', [b'', b'\xef\xbb\xbf'])
@pytest.mark.parametrize('progress', [False, True])
def test_note_with_next_preserves_existing_task_bytes(local_repo, newline, bom, progress):
    path = task(local_repo, ships='none')
    content = (
        b'---\nid: "TASK-1"\ntitle: "Keep bytes"\nowner: "alex"\n'
        b'status: "IN_PROGRESS"\nships: "none"\nwaits_on: []\ncontract: "none"\n'
        b'next: "old"\n---\n\n# TASK-1\n\n## Scope\n\nKeep this text.\n')
    if progress:
        content += b'\n## Progress\n\n- 2026-11-14: Existing note.\n\n## Acceptance\n\n- Keep this too.\n'
    original = bom + content.replace(b'\n', newline)
    path.write_bytes(original)
    ok(local_repo, 'note', '--task', 'TASK-1', '--next', 'review', 'Added note')
    actual = path.read_bytes()
    marker = ('- ' + store.dt.datetime.now(store.dt.timezone.utc).date().isoformat() + ': Added note').encode()
    replacement = original.replace(b'next: "old"', b'next: "review"')
    expected = (replacement.replace(newline + b'## Acceptance', marker + newline + newline + b'## Acceptance')
                if progress else replacement + newline + b'## Progress' + newline + newline + marker + newline)
    assert actual == expected
    assert b'\r\r\n' not in actual


def test_next_only_uses_byte_constructed_windows_fixture(local_repo):
    path = task(local_repo, ships='none')
    original = b'\xef\xbb\xbf' + path.read_text(encoding='utf-8').replace('\n', '\r\n').encode('utf-8')
    path.write_bytes(original)
    ok(local_repo, 'note', '--task', 'TASK-1', '--next', 'Review')
    assert path.read_bytes() == original.replace(b'\r\n---\r\n', b'\r\nnext: "Review"\r\n---\r\n', 1)


def test_stop_ignores_unrelated_undecodable_task(local_repo, monkeypatch, capsys):
    bad = task(local_repo, 'TASK-1', ships='none', status='DONE')
    bad.write_bytes(bad.read_bytes() + b'\xff')
    commit_all(local_repo)
    task(local_repo, 'TASK-2', ships='none')
    assert stop(local_repo, monkeypatch) == 0
    assert capsys.readouterr().err == ''


def test_stop_does_not_invent_a_claim_for_missing_held_task(local_repo, monkeypatch, capsys):
    local_claim(local_repo, 'TASK-1')
    assert stop(local_repo, monkeypatch) == 0
    assert capsys.readouterr().err == ''


def test_stop_respects_readable_merge_target_of_malformed_task(local_repo, monkeypatch, capsys):
    path = task(local_repo, status='DONE', ships='none')
    path.write_bytes(path.read_bytes().replace(
        b'contract:', b'unknown_field: typo\nmerge_ref: refs/heads/release\ncontract:'))
    commit_all(local_repo)
    git(local_repo, 'checkout', '-q', '-b', 'release')
    path.write_bytes(path.read_bytes() + b'\nRelease-specific task text.\n')
    commit_all(local_repo)
    git(local_repo, 'checkout', '-q', '-b', 'feature')
    assert stop(local_repo, monkeypatch) == 0
    assert capsys.readouterr().err == ''
