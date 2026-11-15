"""Round-9 command regressions; repositories and refs are local fixtures."""

import json
import os
import subprocess

import pytest

from workorder import store
from test_workorder import PYTHON, cli, git, local_repo, ok, task, unmerged_feature
from test_v05 import commit_all


PASS = f'{PYTHON} -c pass'
SLEEP = f'{PYTHON} -c "import time; time.sleep(0.4)"'


def filled_task(root, **kwargs):
    path = task(root, ships='none', test=PASS, **kwargs)
    path.write_text(path.read_text() + '\n## Scope\n\nAdd the calculation.\n\n## Acceptance\n\n- It adds correctly.\n')
    return path


def field(path, key, value):
    text = path.read_text()
    path.write_text(text.replace('\n---\n', f'\n{key}: {value}\n---\n', 1))


@pytest.mark.parametrize('section', ['Scope', 'Acceptance'])
@pytest.mark.parametrize('body', ['', '<!-- template -->', '<!-- first line\nsecond line -->'])
def test_done_refuses_empty_or_placeholder_sections(local_repo, section, body):
    path = filled_task(local_repo)
    old = 'Add the calculation.' if section == 'Scope' else '- It adds correctly.'
    path.write_text(path.read_text().replace(old, body))
    result = cli(local_repo, 'done', '--task', 'TASK-1', '--note', 'Still defining the work')
    assert result.returncode == 1
    assert 'fill in Scope and Acceptance first' in result.stdout
    assert store.record(local_repo, 'TASK-1')['status'] == 'IN_PROGRESS'
    assert ': Still defining the work' not in path.read_text()
    assert not store.entries(local_repo, 'TASK-1')


@pytest.mark.parametrize('quote', ['"', "'"])
def test_done_preserves_existing_status_quotes(local_repo, quote):
    path = filled_task(local_repo)
    path.write_bytes(path.read_bytes().replace(b'status: "IN_PROGRESS"', f'status: {quote}IN_PROGRESS{quote}'.encode()))
    before = path.read_bytes()
    output = ok(local_repo, 'done', '--no-code-change', '--task', 'TASK-1')
    assert path.read_bytes() == before.replace(f'status: {quote}IN_PROGRESS{quote}'.encode(), f'status: {quote}DONE{quote}'.encode())
    assert 'its declared test passed on this code' in output
    assert 'claim supported' not in output


def test_done_reports_merge_debt_and_missing_clone_hook(local_repo):
    path = filled_task(local_repo)
    path.write_text(path.read_text().replace('ships: "none"', 'ships: "repo"'))
    commit_all(local_repo)
    unmerged_feature(local_repo)
    output = ok(local_repo, 'done', '--no-code-change', '--task', 'TASK-1')
    assert ('TASK-1 DONE: its declared test passed on this code; still owes: merge of the tested code') in output
    assert 'Warning: pre-commit hook is missing in this clone; run workorder init' in output


def test_done_refuses_ref_test_failure_before_writing_done(local_repo):
    root = local_repo
    path = filled_task(root)
    probe = root / 'probe.py'
    probe.write_text('from pathlib import Path\nassert Path("app.py").read_text() == "items = []\\n"\n')
    path.write_text(path.read_text().replace(json.dumps(PASS), json.dumps(f'{PYTHON} probe.py')))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-q', '-b', 'feature')
    (root / 'app.py').write_text('items = ["broken"]\n')
    probe.write_text('assert True\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    output = result.stdout
    assert result.returncode == 1
    assert 'TASK-1: its tests from origin/main fail on this code;' in output
    assert 'the label workorder-allow:TASK-1 (or an approving review)' in output
    assert 'TASK-1 DONE:' not in output
    assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'


def test_check_and_show_use_claimed_and_evidence(local_repo):
    filled_task(local_repo)
    ok(local_repo, 'done', '--no-code-change', '--task', 'TASK-1')
    for args in [('check', 'TASK-1'), ('show', '--task', 'TASK-1')]:
        output = ok(local_repo, *args)
        assert 'Claimed:  DONE' in output and 'Evidence: recorded receipts support the claim (not re-run here; `check --run-tests` re-runs it)' in output
        assert 'computed CLOSED' not in output and 'Computed:' not in output
    assert 'Result: the declared test passed on this code' in ok(local_repo, 'check', 'TASK-1')


@pytest.mark.parametrize('old', [None, '"old"', "'old'", 'old', '>\n  old folded\n  value', '|-\n  old literal\n  value'])
@pytest.mark.parametrize('note', [None, 'Implemented the calculation'])
def test_note_sets_next_with_optional_progress(local_repo, old, note):
    path = filled_task(local_repo)
    if old is not None:
        field(path, 'next', old)
    next_text = 'Review "edge cases" and Alex\'s comments'
    args = ['note', '--task', 'TASK-1', '--next', next_text] + ([note] if note else [])
    output = ok(local_repo, *args)
    assert store.record(local_repo, 'TASK-1')['next'] == next_text
    assert next_text in output
    assert ('- Implemented' not in path.read_text())
    if note:
        assert ': Implemented the calculation' in path.read_text()
    else:
        assert '## Progress' not in path.read_text()
    assert 'Add the calculation.' in path.read_text() and '- It adds correctly.' in path.read_text()


def test_note_next_preserves_bom_crlf_and_can_clear_field(local_repo):
    path = filled_task(local_repo)
    field(path, 'next', '"old"')
    before = b'\xef\xbb\xbf' + path.read_text().replace('\n', '\r\n').encode('utf-8')
    path.write_bytes(before)
    ok(local_repo, 'note', '--task', 'TASK-1', '--next', '')
    assert path.read_bytes() == before.replace(b'next: "old"', b'next: ""')
    assert store.record(local_repo, 'TASK-1')['next'] == ''


@pytest.mark.parametrize('action', ['done', 'receipt', 'renew', 'check'])
@pytest.mark.parametrize('source', ['option', 'field'])
def test_timeout_is_a_failed_run_with_exit_124(local_repo, action, source):
    root = local_repo
    path = filled_task(root)
    path.write_text(path.read_text().replace(json.dumps(PASS), json.dumps(SLEEP)))
    if source == 'field':
        field(path, 'test_timeout', '0.03')
    base = commit_all(root)
    if action == 'check':
        # Supply pre-existing evidence without spending the timeout in the fixture.
        store.receipt(root, 'TASK-1', 'implemented')
        store.append(root, 'TASK-1', dict(kind='test', commit=store.head(root),
                     code=store.code_digest(root, store.head(root)),
                     command=store.split_command(SLEEP), exit_code=0, valid=True))
        path.write_text(path.read_text().replace('"IN_PROGRESS"', '"DONE"'))
        args = ['check', '--base', base, '--run-tests']
    else:
        args = ['receipt', 'test'] if action == 'receipt' else [action]
        if action == 'done':
            args.append('--no-code-change')
        args += ['--task', 'TASK-1']
    if source == 'option':
        args += ['--timeout', '0.03']
    result = cli(root, *args)
    assert result.returncode == 1, result.stdout + result.stderr
    assert 'timed out' in result.stdout + result.stderr
    assert '124' in result.stdout + result.stderr
    if action == 'done':
        assert store.record(root, 'TASK-1')['status'] == 'IN_PROGRESS'
        assert not store.entries(root, 'TASK-1')
    elif action != 'check':
        receipt = store.entries(root, 'TASK-1')[-1]
        assert receipt['kind'] == 'test' and receipt['exit_code'] == 124 and not receipt['valid']
        assert 'timed out' in ok(root, 'show', '--task', 'TASK-1')


@pytest.mark.parametrize('action', ['done', 'receipt', 'renew', 'check'])
def test_timeout_option_overrides_task_field(local_repo, action):
    root = local_repo
    path = filled_task(root)
    path.write_text(path.read_text().replace(json.dumps(PASS), json.dumps(f'{PYTHON} -c "import time; time.sleep(0.06)"')))
    field(path, 'test_timeout', '0.001')
    base = commit_all(root)
    if action == 'check':
        store.receipt(root, 'TASK-1', 'implemented')
        store.receipt(root, 'TASK-1', 'test', store.split_command(store.record(root, 'TASK-1')['test']), timeout=2, no_code_change=True)
        path.write_text(path.read_text().replace('"IN_PROGRESS"', '"DONE"'))
        args = ['check', '--base', base, '--run-tests']
    else:
        args = ['receipt', 'test'] if action == 'receipt' else [action]
        if action == 'done':
            args.append('--no-code-change')
        args += ['--task', 'TASK-1']
        if action == 'receipt':
            store.receipt(root, 'TASK-1', 'implemented')
    output = ok(root, *args, '--timeout', '2')
    assert 'timed out' not in output


@pytest.mark.parametrize('value', ['0', '-1', 'nan', 'inf', 'false', '[]'])
def test_invalid_task_timeout_has_specific_error(local_repo, value):
    path = filled_task(local_repo)
    field(path, 'test_timeout', value)
    result = cli(local_repo, 'show', '--task', 'TASK-1')
    assert result.returncode == 1
    assert 'test_timeout must be a positive number of seconds' in result.stderr


def test_note_next_multiline_replaces_single_quoted_value(local_repo):
    path = filled_task(local_repo)
    field(path, 'next', "'old'")
    ok(local_repo, 'note', '--task', 'TASK-1', '--next', 'Review changes\nThen merge')
    assert store.record(local_repo, 'TASK-1')['next'] == 'Review changes\nThen merge'


@pytest.mark.parametrize('action', ['done', 'receipt', 'renew', 'check'])
def test_invalid_timeout_option_explains_seconds(local_repo, action):
    filled_task(local_repo)
    args = ['receipt', 'test'] if action == 'receipt' else [action]
    args += ['--base', 'HEAD', '--run-tests'] if action == 'check' else ['--task', 'TASK-1']
    result = cli(local_repo, *args, '--timeout', 'nan')
    assert result.returncode != 0
    assert 'must be a positive number of seconds' in result.stdout + result.stderr


@pytest.mark.skipif(os.name == 'nt', reason='POSIX child process group cleanup')
def test_timeout_kills_child_test_processes(local_repo):
    root = local_repo
    script = root / 'slow.py'
    script.write_text('import subprocess, sys, time\n'
                      'subprocess.Popen([sys.executable, "-c", "import time; time.sleep(10)" ])\n'
                      'print("started child", flush=True)\n'
                      'time.sleep(10)\n')
    path = filled_task(root)
    path.write_text(path.read_text().replace(PASS, f'{PYTHON} slow.py'))
    commit_all(root)
    result = subprocess.run([PYTHON, '-m', 'workorder', 'receipt', 'test', '--task', 'TASK-1',
                             '--timeout', '0.3'], cwd=root,
                            env={**os.environ, 'PYTHONPATH': str(__import__('pathlib').Path(__file__).resolve().parents[1])},
                            capture_output=True, text=True, timeout=3)
    assert result.returncode == 1
    assert 'started child' in result.stdout and 'exit 124' in result.stdout + result.stderr
    assert store.entries(root, 'TASK-1')[-1]['exit_code'] == 124
