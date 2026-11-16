"""Evidence must execute current source even when timestamp bytecode looks valid."""

import json
import os
from pathlib import Path
import subprocess
from concurrent.futures import ThreadPoolExecutor

import pytest

from workorder import gate, pytest_selection, store
from test_workorder import PYTHON, cli, git, local_repo, ok
from test_v05 import commit_all
from test_v15d import stop
from test_v15j import A, TEST_A, change_b, setup


def command(runner):
    return ([runner] if runner == 'pytest' else [PYTHON, *([runner] if runner else []), '-m', 'pytest']) + [
        '-q', 'tests/test_a.py']


def rewrite(path, old, new):
    before = path.stat()
    content = path.read_text()
    assert old in content and len(old) == len(new)
    path.write_text(content.replace(old, new))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    assert path.stat().st_size == before.st_size
    assert path.stat().st_mtime_ns == before.st_mtime_ns


def warm(root, words):
    # Simulate bytecode left by an ordinary developer pytest run. No sleeps or
    # clock assumptions: rewrite() restores the exact timestamp stored in it.
    env = os.environ.copy()
    env.pop('PYTHONPYCACHEPREFIX', None)
    env.pop('PYTHONDONTWRITEBYTECODE', None)
    run = subprocess.run(words, cwd=root, env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout + run.stderr
    caches = {path: path.read_bytes() for path in root.rglob('*.pyc')}
    assert any(path.name.startswith('a.') for path in caches)
    assert any(path.name.startswith('test_a.') for path in caches)
    return caches


def edit(root, kind):
    if kind == 'code':
        rewrite(root / 'a.py', 'return 1', 'return 0')
    else:
        rewrite(root / 'tests/test_a.py', 'a() == 1', 'a() == 0')


def sources(root):
    (root / 'tests').mkdir(exist_ok=True)
    (root / 'a.py').write_text(A)
    (root / 'tests/test_a.py').write_text(TEST_A)
    (root / 'pytest.ini').write_text('[pytest]\npythonpath = .\n')


@pytest.mark.parametrize('runner', ['', '-I', '-E', 'pytest'])
@pytest.mark.parametrize('kind', ['code', 'test'])
@pytest.mark.parametrize('timeout', [None, 30])
def test_evidence_ignores_same_size_same_mtime_bytecode(tmp_path, runner, kind, timeout):
    sources(tmp_path)
    words = command(runner)
    if kind == 'test' and runner in {'', '-E'}:
        words.append('--assert=plain')
    caches = warm(tmp_path, words)
    edit(tmp_path, kind)
    run = store.run_test(words, tmp_path, timeout)
    assert run.returncode == 1, run.stdout + run.stderr
    assert '1 failed' in run.stdout
    assert run.args == words
    assert {path: path.read_bytes() for path in tmp_path.rglob('*.pyc')} == caches


@pytest.mark.parametrize('runner', ['-I', 'pytest'])
@pytest.mark.parametrize('kind', ['code', 'test'])
@pytest.mark.parametrize('action', ['done', 'precommit', 'check', 'stop'])
def test_public_checks_refuse_stale_pass(local_repo, monkeypatch, runner, kind, action):
    root = local_repo
    setup(root)
    (root / 'pytest.ini').write_text('[pytest]\npythonpath = .\n')
    words = command(runner)
    if kind == 'test':
        # Recent pytest hashes rewritten tests. Plain mode still exercises
        # Python's timestamp cache deterministically on every pytest version.
        words.append('--assert=plain')
    path = root / 'tasks/TASK-1.md'
    path.write_text(path.read_text().replace(json.dumps('python -m pytest -q tests/test_a.py'),
                                             json.dumps(store.join_command(words))))
    log = root.parent / 'bytecode-runs.jsonl'
    (root / 'tests/test_a.py').write_text(TEST_A +
        '\ndef teardown_module():\n    import json, sys\n    from pathlib import Path\n'
        f'    with Path({str(log)!r}).open("a") as stream:\n'
        '        stream.write(json.dumps(sys.pycache_prefix) + "\\n")\n')
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    ok(root, 'done', '--task', 'TASK-1', '--no-code-change')
    assert store.entries(root, 'TASK-1')[-1]['command'] == words
    commit_all(root)
    change_b(root)
    warm(root, words)
    log.unlink()
    edit(root, kind)
    # The deliberate size/mtime collision can also fool Windows Git's stat
    # cache. Force hashing so the staged/committed copy contains the edit,
    # without invalidating the stale Python bytecode we are testing.
    git(root, 'add', '--renormalize', '--', 'a.py' if kind == 'code' else 'tests/test_a.py')
    # Keep the task's source edit committed for done/PR; hooks get the same
    # bytes staged. CI disables Workorder's result reuse, not Python bytecode.
    monkeypatch.setenv('CI', 'true')
    monkeypatch.setenv('GITHUB_ACTIONS', 'true')
    if action == 'precommit':
        git(root, 'add', '-A')
        result = subprocess.run(['sh', str(root / '.git/hooks/pre-commit')], cwd=root,
                                capture_output=True, text=True)
    else:
        commit_all(root)
        result = (cli(root, 'done', '--task', 'TASK-2', '--needs-review') if action == 'done'
                  else stop(root) if action == 'stop'
                  else cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests'))
    output = result.stdout + result.stderr
    assert result.returncode == (2 if action == 'stop' else 1), output
    assert 'FAILED tests/test_a.py::test_a' in output, output
    if action == 'done':
        assert "regression: TASK-1's test fails on this code" in output
        assert store.entries(root, 'TASK-2') == []
    prefixes = [json.loads(line) for line in log.read_text().splitlines()]
    assert prefixes and all(prefixes), prefixes
    assert all(Path(prefix).is_relative_to(root / '.git') for prefix in prefixes)
    assert all(Path(prefix).is_dir() for prefix in prefixes)


@pytest.mark.parametrize('runner', ['-I', 'pytest'])
@pytest.mark.parametrize('kind', ['code', 'test'])
def test_project_test_refuses_stale_pass(local_repo, runner, kind):
    root = local_repo
    sources(root)
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    words = command(runner)
    if kind == 'test':
        words.append('--assert=plain')
    log = root.parent / 'project-prefix.json'
    with (root / 'tests/test_a.py').open('a') as stream:
        stream.write('\ndef teardown_module():\n    import json, sys\n    from pathlib import Path\n'
                     f'    Path({str(log)!r}).write_text(json.dumps(sys.pycache_prefix))\n')
    ok(root, 'init', '--project-test', store.join_command(words))
    commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', 'HEAD')
    git(root, 'switch', '-c', 'feature')
    ok(root, 'check', '--base', 'origin/main', '--run-tests')
    warm(root, words)
    edit(root, kind)
    # Bypass Git's stat shortcut while retaining the source timestamp that
    # makes the warmed Python bytecode appear current.
    git(root, 'add', '--renormalize', '--', 'a.py' if kind == 'code' else 'tests/test_a.py')
    commit_all(root)
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests')
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert 'the project test fails on this code: tests/test_a.py::test_a' in output
    prefix = json.loads(log.read_text())
    assert prefix and Path(prefix).is_relative_to(root / '.git')
    assert Path(prefix).is_dir()


@pytest.mark.parametrize('outcome', ['pass', 'fail', 'timeout', 'spawn_error'])
def test_bytecode_cache_lifetime(tmp_path, monkeypatch, outcome):
    # A dedicated parent also checks cleanup when Popen/run cannot start.
    parent = tmp_path / 'cache parent'
    parent.mkdir()
    monkeypatch.setattr(store.tempfile, 'tempdir', str(parent))
    inherited = tmp_path / 'inherited'
    monkeypatch.setenv('PYTHONPYCACHEPREFIX', str(inherited))
    if outcome == 'spawn_error':
        with pytest.raises(OSError):
            store.run_test([str(tmp_path / 'missing-command')], tmp_path)
    else:
        source = ('import json, os, sys\nfrom pathlib import Path\n'
                  'print("PREFIX=" + json.dumps(sys.pycache_prefix), flush=True)\n'
                  'assert sys.pycache_prefix == os.environ["PYTHONPYCACHEPREFIX"]\n'
                  'assert Path(sys.pycache_prefix).is_dir()\n')
        source += ('raise SystemExit(3)\n' if outcome == 'fail' else
                   'while True: pass\n' if outcome == 'timeout' else '')
        words = [PYTHON, '-I', '-X', 'pycache_prefix=' + str(inherited),
                 '-Xpycache_prefix=' + str(inherited), '-c', source]
        run = store.run_test(words, tmp_path, 2 if outcome == 'timeout' else None)
        assert run.returncode == {'pass': 0, 'fail': 3, 'timeout': 124}[outcome], run.stderr
        prefix = Path(json.loads(next(line[7:] for line in run.stdout.splitlines() if line.startswith('PREFIX='))))
        assert prefix != inherited
        assert not prefix.exists()
        assert run.args == words
    assert list(parent.iterdir()) == []
    assert os.environ['PYTHONPYCACHEPREFIX'] == str(inherited)


@pytest.mark.parametrize('comparison', ['base', 'stub'])
def test_comparisons_compile_in_private_cache(local_repo, comparison):
    root = local_repo
    sources(root)
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    (root / 'tests/test_a.py').write_text(TEST_A +
        '\ndef test_cache():\n    import sys\n    from pathlib import Path\n'
        '    assert sys.pycache_prefix is not None\n'
        '    assert not list(Path.cwd().rglob("__pycache__"))\n'
        '    print("PREFIX=" + sys.pycache_prefix)\n')
    revision = commit_all(root)
    selected = ['tests/test_a.py::test_a', 'tests/test_a.py::test_cache']
    words = pytest_selection.command(command('-I') + ['-s'], selected)
    # The import probe loads this plugin before the test command. It must not
    # leave bytecode in the comparison tree either.
    overlays = pytest_selection.overlay(selected)
    destination = root.parent / 'comparison'
    if comparison == 'base':
        code, output = gate.run_ref_test(root, destination, revision, words, None, None, overlays)
    else:
        overlays['a.py'] = (b'def a():\n    raise RuntimeError("stub")\n', None)
        code, output = gate.run_snapshot_test(root, destination, words, None, None,
                                              overlays=overlays)
    assert code == (0 if comparison == 'base' else 1), output
    assert ('1 failed, 1 passed' if comparison == 'stub' else '2 passed') in output
    prefix = next(line.partition('PREFIX=')[2] for line in output.splitlines() if 'PREFIX=' in line)
    assert Path(prefix).is_relative_to(root / '.git')
    assert not Path(prefix).joinpath(*destination.resolve().parts[1:]).exists()


def test_concurrent_runs_get_distinct_bytecode_caches(tmp_path):
    words = [PYTHON, '-c', 'import sys; print(sys.pycache_prefix)']
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: store.run_test(words, tmp_path), range(2)))
    prefixes = [run.stdout.strip() for run in runs]
    assert all(run.returncode == 0 for run in runs)
    assert len(set(prefixes)) == 2 and 'None' not in prefixes
    assert all(not Path(prefix).exists() for prefix in prefixes)


@pytest.mark.parametrize('comparison', [False, True])
def test_result_cache_does_not_reuse_pre_isolation_pass(local_repo, comparison):
    root = local_repo
    words = ['sh', '-c', 'exit 7'] if comparison else [PYTHON, '-c', 'raise SystemExit(7)']
    revision = store.head(root)
    if comparison:
        settings = store.git(root, 'config', '--list').stdout
        key = 'ref-' + store.digest(store.packed(['ref-v3', revision, words, None, settings, []]).encode('ascii'))
    else:
        fingerprint = store._test_state(root)[1]
        key = store.digest(store.packed([3, fingerprint, words, None]).encode('ascii'))
    legacy = root / '.git/workorder-tests' / (key + '.json')
    legacy.parent.mkdir(parents=True)
    legacy.write_text(json.dumps(dict(returncode=0, stdout='', stderr='', at='12:00')))
    if comparison:
        with store.test_reuse():
            code, output = gate.run_ref_test(root, root.parent / 'base', revision, words, None, None)
        assert code == 7, output
    else:
        run = store.cached_test(words, root)
        assert run.returncode == 7 and not run.cached
