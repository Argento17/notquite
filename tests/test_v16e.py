"""Version-independent import configuration and reusable dependency bytecode."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path, PureWindowsPath
import subprocess
from types import SimpleNamespace
import time
import venv

import pytest

from workorder import gate, store
from test_workorder import PYTHON, git, local_repo
from test_environment import TEST_SITE
from test_v05 import commit_all
from test_v15c_imports import editable, setup_task
from test_v16b_imports import TEST_VALUE
from test_v16d_bytecode import command, edit, sources


@pytest.mark.parametrize('flag', ['-I', '-E'])
@pytest.mark.parametrize('selection', ['override', 'addopts'])
def test_probe_uses_pytest_config_overrides(local_repo, tmp_path, monkeypatch, flag, selection):
    root = local_repo
    python = editable(root, tmp_path)
    (root / 'pytest.ini').write_text('[pytest]\npythonpath = missing\n')
    (root / 'settings').mkdir()
    (root / 'settings/chosen.ini').write_text('[pytest]\npythonpath = ../src ../missing\n')
    # These options must be accepted without executing repository conftests.
    (root / 'conftest.py').write_text('raise RuntimeError("probe loaded conftest")\n')
    base, words = setup_task(root, python, TEST_VALUE)
    words.insert(1, flag)
    if selection == 'override':
        words += ['-o', 'pythonpath=src missing']
    else:
        monkeypatch.setenv('PYTEST_ADDOPTS', '-c settings/chosen.ini')
    from workorder.python_imports import check, environment
    copy = tmp_path / 'copy'
    gate._ref_test_snapshot(root, copy, base)
    failure = check(copy, root, words, environment(copy, os.environ.copy()))
    assert failure is None, failure


def reporting_test(root, report):
    sources(root)
    with (root / 'tests/test_a.py').open('a') as stream:
        stream.write('\ndef teardown_module():\n'
                     '    import a, json, sys\n    from pathlib import Path\n'
                     f'    Path({str(report)!r}).write_text(json.dumps([\n'
                     '        sys.pycache_prefix, json.__cached__, a.__cached__, __cached__]))\n')


@pytest.mark.parametrize('runner', ['', '-I', '-E', 'pytest'])
@pytest.mark.parametrize('kind', ['code', 'test'])
def test_repeated_runs_refresh_checkout_and_reuse_dependencies(local_repo, runner, kind):
    root = local_repo
    report = root.parent / 'cache.json'
    reporting_test(root, report)
    words = command(runner) + ['--assert=plain']
    first = store.run_test(words, root)
    assert first.returncode == 0, first.stdout + first.stderr
    prefix, dependency, module, test = map(Path, json.loads(report.read_text()))
    assert dependency.is_file()
    assert prefix.is_relative_to(root / '.git')
    assert not module.exists() and not test.exists()
    # A deterministic timestamp proves the second run reads the existing pyc.
    os.utime(dependency, ns=(1_000_000_000, 1_000_000_000))
    edit(root, kind)
    second = store.run_test(words, root)
    assert second.returncode == 1, second.stdout + second.stderr
    assert '1 failed' in second.stdout
    assert json.loads(report.read_text())[:2] == [str(prefix), str(dependency)]
    assert dependency.stat().st_mtime_ns == 1_000_000_000
    assert not module.exists() and not test.exists()
    assert not list((root / 'tests').rglob('__pycache__'))
    assert not (root / '__pycache__').exists()


@pytest.mark.parametrize('comparison', ['base', 'stub'])
def test_comparison_reuses_owner_dependencies_and_removes_checkout_cache(local_repo, comparison):
    root = local_repo
    report = root.parent / 'cache.json'
    reporting_test(root, report)
    revision = commit_all(root)
    words = command('-I')
    first = store.run_test(words, root)
    assert first.returncode == 0, first.stdout + first.stderr
    prefix, dependency, *_ = json.loads(report.read_text())
    assert Path(dependency).is_file()
    os.utime(dependency, ns=(1_000_000_000, 1_000_000_000))
    copy = root.parent / 'comparison'
    if comparison == 'base':
        code, output = gate.run_ref_test(root, copy, revision, words, None, None)
    else:
        code, output = gate.run_snapshot_test(root, copy, words, None, None,
            overlays={'a.py': (b'def a():\n    raise RuntimeError("stub")\n', None)})
    assert code == (0 if comparison == 'base' else 1), output
    observed = json.loads(report.read_text())
    assert observed[:2] == [prefix, dependency]
    assert Path(dependency).stat().st_mtime_ns == 1_000_000_000
    assert all(not Path(path).exists() for path in observed[2:])
    assert not list(copy.rglob('__pycache__'))


def test_concurrent_runs_keep_checkout_bytecode_separate(local_repo):
    root = local_repo
    (root / 'a.py').write_text('value = 1\n')
    ready, release = root.parent / 'ready', root.parent / 'release'
    source = ('import a, json, sys, time\nfrom pathlib import Path\n'
              f'Path({str(ready)!r}).write_text(json.dumps([sys.pycache_prefix, a.__cached__]))\n'
              f'while not Path({str(release)!r}).exists(): time.sleep(0.01)\n'
              'assert Path(a.__cached__).is_file()\nassert a.value == 1\n')
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(store.run_test, [PYTHON, '-c', source], root, 15)
        try:
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert ready.exists()
            from test_v16d_bytecode import rewrite
            rewrite(root / 'a.py', 'value = 1', 'value = 2')
            second = store.run_test([PYTHON, '-c',
                'import a, json, sys; assert a.value == 2; print(json.dumps(sys.pycache_prefix))'], root, 5)
            assert second.returncode == 0, second.stdout + second.stderr
            prefix, cached = json.loads(ready.read_text())
            assert prefix != json.loads(second.stdout)
            assert Path(cached).is_file()  # The second run did not clear the first's cache.
        finally:
            release.touch()
        result = first.result()
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(prefix).is_relative_to(root / '.git')
    assert Path(prefix).is_dir() and not Path(cached).exists()


@pytest.mark.parametrize('outcome', ['fail', 'timeout', 'spawn_error'])
def test_repository_cache_released_after_unsuccessful_run(local_repo, outcome):
    root = local_repo
    (root / 'a.py').write_text('value = 1\n')
    source = ('import a, json, sys\n'
              'print(json.dumps([sys.pycache_prefix, json.__cached__, a.__cached__]), flush=True)\n')
    words = [PYTHON, '-I', '-c', 'import sys; sys.path.insert(0, "."); ' + source]
    first = store.run_test(words, root)
    assert first.returncode == 0, first.stdout + first.stderr
    prefix, dependency, cached = map(Path, json.loads(first.stdout))
    assert dependency.exists()
    os.utime(dependency, ns=(1_000_000_000, 1_000_000_000))
    if outcome == 'spawn_error':
        with pytest.raises(OSError):
            store.run_test([str(root / 'missing-command')], root)
    else:
        ending = '\nraise SystemExit(7)' if outcome == 'fail' else '\nwhile True: pass'
        failed = store.run_test([*words[:-1], words[-1] + ending], root, 2)
        assert failed.returncode == (7 if outcome == 'fail' else 124), failed.stderr
        assert json.loads(failed.stdout)[0] == str(prefix)
    assert not cached.exists()
    again = store.run_test(words, root)
    assert again.returncode == 0, again.stdout + again.stderr
    assert json.loads(again.stdout)[0] == str(prefix)
    assert dependency.stat().st_mtime_ns == 1_000_000_000


def test_unwritable_repository_cache_falls_back_to_current_source(local_repo):
    root = local_repo
    (root / '.git/workorder-bytecode').write_text('not a directory')
    sources(root)
    words = command('-I')
    from test_v16d_bytecode import warm
    caches = warm(root, words)
    edit(root, 'code')
    run = store.run_test(words, root)
    assert run.returncode == 1, run.stdout + run.stderr
    assert '1 failed' in run.stdout
    assert {path: path.read_bytes() for path in root.rglob('*.pyc')} == caches


def test_unc_checkout_bytecode_is_removed(local_repo):
    from workorder.python_imports import _bytecode_directory
    # Exercise actual cleanup on this host with Windows UNC path semantics.
    checkout = PureWindowsPath('//server/share/project')
    root = SimpleNamespace(absolute=lambda: checkout, resolve=lambda: checkout)
    with _bytecode_directory(root, local_repo) as prefix:
        cached = Path(prefix) / 'server/share/project/module.cpython-312.pyc'
        cached.parent.mkdir(parents=True)
        cached.write_bytes(b'old bytecode')
    assert not cached.exists()
    # Also discard leftovers from an interrupted prior run before execution.
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'old bytecode')
    with _bytecode_directory(root, local_repo):
        assert not cached.exists()


def test_repository_virtualenv_dependency_stays_cached(local_repo):
    root = local_repo
    dependency = root / '.venv/lib/python3.12/site-packages/dependency.py'
    dependency.parent.mkdir(parents=True)
    dependency.write_text('value = 42\n')
    (root / '.venv/pyvenv.cfg').write_text('home = /usr/bin\n')
    (root / '.gitignore').write_text('.venv/\n')
    words = [PYTHON, '-c',
             f'import sys; sys.path.insert(0, {str(dependency.parent)!r}); '
             'import dependency; assert dependency.value == 42; print(dependency.__cached__)']
    first = store.run_test(words, root)
    assert first.returncode == 0, first.stdout + first.stderr
    cached = Path(first.stdout.strip())
    assert cached.exists()
    os.utime(cached, ns=(1_000_000_000, 1_000_000_000))
    second = store.run_test(words, root)
    assert second.returncode == 0, second.stdout + second.stderr
    assert second.stdout == first.stdout
    assert cached.stat().st_mtime_ns == 1_000_000_000


@pytest.mark.parametrize('comparison', ['base', 'snapshot'])
def test_comparison_reuses_repository_virtualenv_dependency(local_repo, comparison):
    root = local_repo
    environment = root / '.venv'
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = Path(subprocess.check_output([str(python), '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    (site / 'test_tools.pth').write_text(str(TEST_SITE) + '\n')
    (site / 'dependency.py').write_text('value = 42\n')
    (root / '.gitignore').write_text('.venv/\n__pycache__/\n')
    report = root.parent / 'dependency-cache'
    sources(root)
    with (root / 'tests/test_a.py').open('a') as stream:
        stream.write('\ndef test_dependency():\n    import dependency\n    from pathlib import Path\n'
                     '    assert dependency.value == 42\n'
                     f'    Path({str(report)!r}).write_text(dependency.__cached__)\n')
    revision = commit_all(root)
    words = [str(python), '-I', '-m', 'pytest', '-q', 'tests/test_a.py']
    first = store.run_test(words, root)
    assert first.returncode == 0, first.stdout + first.stderr
    cached = Path(report.read_text())
    assert cached.exists()
    os.utime(cached, ns=(1_000_000_000, 1_000_000_000))
    copy = root.parent / 'copy'
    if comparison == 'base':
        code, output = gate.run_ref_test(root, copy, revision, words, None, None)
    else:
        code, output = gate.run_snapshot_test(root, copy, words, None, None)
    assert code == 0, output
    assert report.read_text() == str(cached)
    assert cached.stat().st_mtime_ns == 1_000_000_000
    assert not list(copy.rglob('__pycache__'))


@pytest.mark.parametrize('tracked', [False, True])
def test_virtualenv_layout_does_not_hide_project_source(local_repo, tracked):
    root = local_repo
    project = root / '.venv/lib/python3.12/site-packages' if tracked else root / '.venv/scripts'
    project.mkdir(parents=True)
    (root / '.venv/pyvenv.cfg').write_text('home = /usr/bin\n')
    (root / '.gitignore').write_text('.venv/\n')
    module = project / 'project.py'
    module.write_text('value = 1\n')
    if tracked:
        git(root, 'add', '-f', str(module))
    words = [PYTHON, '-c', f'import sys; sys.path.insert(0, {str(project)!r}); '
             'import project; assert project.value == 1']
    first = store.run_test(words, root)
    assert first.returncode == 0, first.stdout + first.stderr
    from test_v16d_bytecode import rewrite
    rewrite(module, 'value = 1', 'value = 2')
    second = store.run_test(words, root)
    assert second.returncode == 1, second.stdout + second.stderr
    assert 'AssertionError' in second.stderr


def test_comparison_retains_tracked_virtualenv_source(local_repo):
    root = local_repo
    environment = root / '.venv'
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = Path(subprocess.check_output([str(python), '-c',
        'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip())
    (site / 'test_tools.pth').write_text(str(TEST_SITE) + '\n')
    module = site / 'project.py'
    module.write_text('value = 42\n')
    (root / '.gitignore').write_text('.venv/\n__pycache__/\n')
    sources(root)
    (root / 'tests/test_a.py').write_text('import project\n'
        'def test_project():\n    assert project.value == 42\n')
    git(root, 'add', '-f', str(module))
    revision = commit_all(root)
    module.write_text('value = 99\n')
    words = [str(python), '-I', '-m', 'pytest', '-q', 'tests/test_a.py']
    current = store.run_test(words, root)
    assert current.returncode == 1, current.stdout + current.stderr
    code, output = gate.run_ref_test(root, root.parent / 'copy', revision, words, None, None)
    assert code == 0, output
    assert '1 passed' in output
