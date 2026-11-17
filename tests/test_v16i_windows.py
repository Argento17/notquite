"""Git for Windows helpers and legacy consoles must preserve read/CLI contracts."""
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from workorder import store
from test_workorder import PYTHON, PACKAGE, git, local_repo, task
from test_v16f_git_cache import git_spawns


@pytest.fixture
def driver_repo(local_repo, monkeypatch):
    monkeypatch.delenv('WORKORDER_GIT_CACHE', raising=False)
    root = local_repo
    git(root, 'config', 'diff.astextplain.textconv', 'missing-astextplain')
    (root / '.gitattributes').write_text('*.pdf diff=astextplain\n', encoding='utf-8')
    return root


def test_unused_windows_diff_driver_keeps_reads_and_batch(driver_repo, git_spawns):
    root = driver_repo
    query = ('diff', '--name-only', 'HEAD')
    with store.read_session():
        assert store.git(root, *query).stdout == ''
        assert store.git(root, *query).stdout == ''
        assert store.blob_bytes(root, 'HEAD:app.py') == b'items = []\n'
        assert store.blob_bytes(root, 'HEAD:app.py') == b'items = []\n'
        assert store._READS.get().batches
    assert git_spawns.count(query) == 1
    assert git_spawns.count(('cat-file', '--batch')) == 1


@pytest.mark.parametrize('kind', ['textconv', 'command', 'external', 'environment'])
@pytest.mark.parametrize('cache', ['1', '0'])
def test_active_diff_helpers_are_fresh_but_raw_reads_reuse(driver_repo, git_spawns, monkeypatch, kind, cache):
    root = driver_repo
    monkeypatch.setenv('WORKORDER_GIT_CACHE', cache)
    dependency = root.parent / 'driver-output'
    dependency.write_text('first\n', encoding='utf-8')
    code = f'import sys; from pathlib import Path; sys.stdout.buffer.write(Path({str(dependency)!r}).read_bytes())'
    command = shlex.join([PYTHON, '-c', code])
    if kind == 'environment':
        monkeypatch.setenv('GIT_EXTERNAL_DIFF', command)
    else:
        git(root, 'config', 'diff.external' if kind == 'external' else f'diff.astextplain.{kind}', command)
    (root / 'app.py').write_text('changed\n', encoding='utf-8')
    (root / '.gitattributes').write_text('app.py diff=astextplain\n', encoding='utf-8')
    # Textconv's output must differ between the two sides to be visible.
    if kind == 'textconv':
        code += '; sys.stdout.buffer.write(Path(sys.argv[1]).read_bytes())'
        git(root, 'config', 'diff.astextplain.textconv', shlex.join([PYTHON, '-c', code]))
    query = ('diff', 'HEAD', '--', 'app.py')
    raw = ('diff', '--no-textconv', '--no-ext-diff', 'HEAD', '--', 'app.py')

    @store.cached_read
    def patch(root):
        return store.git(root, *query).stdout

    with store.read_session():
        first = patch(root)
        assert 'first' in first
        expected = store.git(root, *raw).stdout
        assert '-items = []' in expected and '+changed' in expected
        assert store.git(root, *raw).stdout == expected
        assert git_spawns.count(raw) == (1 if cache == '1' else 2)
        dependency.write_text('other\n', encoding='utf-8')
        assert 'other' in patch(root)
        assert git_spawns.count(query) == 2
        assert store.git(root, *raw).stdout == expected


def test_diff_driver_does_not_affect_unselected_path(driver_repo, git_spawns):
    root = driver_repo
    (root / 'app.py').write_text('changed\n', encoding='utf-8')
    (root / 'other.pdf').write_text('pdf\n', encoding='utf-8')
    git(root, 'add', '--intent-to-add', 'other.pdf')
    query = ('diff', 'HEAD', '--', 'app.py')
    with store.read_session():
        first = store.git(root, *query).stdout
        assert '+changed' in first
        assert store.git(root, *query).stdout == first
    assert git_spawns.count(query) == 1


@pytest.mark.parametrize('action', ['show', 'refusal'])
def test_cli_escapes_unencodable_console_text(local_repo, action):
    root = local_repo
    path = task(root)
    text = path.read_text(encoding='utf-8').replace('"Add a list filter"', json.dumps('caf\u00e9 \u0085 \u2028 \u2603'))
    path.write_text(text, encoding='utf-8')
    args = ['show', '--task', 'TASK-1']
    if action == 'refusal':
        args = ['check', '--base', '\u2603']
    run = subprocess.run([PYTHON, '-m', 'workorder', *args], cwd=root,
                         env={**os.environ, 'PYTHONPATH': str(PACKAGE), 'PYTHONIOENCODING': 'cp1252'},
                         capture_output=True)
    output = (run.stdout + run.stderr).decode('cp1252')
    assert run.returncode == (0 if action == 'show' else 1), output
    assert 'UnicodeEncodeError' not in output
    assert '\\u2603' in output


def test_test_output_escapes_unencodable_ids(monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding='cp1252')
    monkeypatch.setattr(sys, 'stdout', stream)
    store.print_test_output('FAILED tests/test_x.py::test_x[\u0085\u2028\u2603]\n', failed=True)
    stream.flush()
    assert b'[\\x85\\u2028\\u2603]' in raw.getvalue()


@pytest.mark.parametrize('test_fails', [False, True])
def test_final_import_report_sharing_violation_retries_without_changing_exit(local_repo, tmp_path, monkeypatch, test_fails):
    from workorder import python_imports
    from test_v16g_import_guard import comparison
    destination = comparison(local_repo, tmp_path)
    if test_fails:
        (destination / 'tests/test_calc.py').write_text('def test_failure():\n    assert False\n', encoding='utf-8')
    original_finish = python_imports._ImportGuard.finish
    original_read = Path.read_text
    finishing = False
    injected = []

    def finish(guard, run):
        nonlocal finishing
        finishing = True
        try:
            return original_finish(guard, run)
        finally:
            finishing = False

    def read(path, *args, **kwargs):
        content = original_read(path, *args, **kwargs)
        if finishing and path.name == 'report.json' and not injected:
            injected.append(path)
            raise PermissionError('the final report is briefly locked')
        return content

    monkeypatch.setattr(python_imports._ImportGuard, 'finish', finish)
    monkeypatch.setattr(Path, 'read_text', read)
    run = store.cached_test([PYTHON, '-m', 'pytest', '-q', 'tests/test_calc.py'],
                            destination, cache_root=local_repo, fresh=True)
    assert injected
    assert run.returncode == (1 if test_fails else 0), run.stdout + run.stderr + str(run.returncode)
    assert not run.cached
