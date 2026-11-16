"""Each Workorder test subprocess owns and cleans up its pytest cache."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import tempfile

import pytest

from workorder import store
from test_workorder import PYTHON, git, local_repo
from test_v05 import commit_all


@pytest.mark.parametrize('timeout', [None, 30])
def test_pytest_does_not_write_checkout_cache(tmp_path, timeout):
    (tmp_path / 'test_value.py').write_text('def test_value():\n    assert 2 + 2 == 4\n')
    run = store.run_test([PYTHON, '-m', 'pytest', '-q'], tmp_path, timeout)
    assert run.returncode == 0, run.stdout + run.stderr
    assert not (tmp_path / '.pytest_cache').exists()


def test_user_pytest_addopts_still_selects_tests(tmp_path, monkeypatch):
    monkeypatch.setenv('PYTEST_ADDOPTS', '-k selected')
    (tmp_path / 'test_value.py').write_text(
        'def test_selected():\n    assert 2 + 2 == 4\n'
        'def test_excluded():\n    assert False\n')
    run = store.run_test([PYTHON, '-m', 'pytest', '-q'], tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    assert '1 passed, 1 deselected' in run.stdout
    assert os.environ['PYTEST_ADDOPTS'] == '-k selected'
    assert not (tmp_path / '.pytest_cache').exists()


def test_cache_path_round_trips_spaces_and_backslashes(tmp_path, monkeypatch):
    parent = tmp_path / ('cache folder' if os.name == 'nt' else 'cache \\folder')
    parent.mkdir()
    monkeypatch.setattr(tempfile, 'tempdir', str(parent))
    (tmp_path / 'test_cache.py').write_text(
        'import json, os\nfrom pathlib import Path\n'
        'def test_cache(request):\n'
        '    path = Path(request.config.getini("cache_dir")).resolve()\n'
        '    path.mkdir(parents=True, exist_ok=True)\n'
        '    print("CACHE=" + json.dumps([str(path), os.environ.get("PYTEST_ADDOPTS", "")]))\n'
        '    assert path.is_dir()\n')
    run = store.run_test([PYTHON, '-m', 'pytest', '-q', '-s'], tmp_path)
    assert run.returncode == 0, run.stdout + run.stderr
    cache, addopts = json.loads(next(line[6:] for line in run.stdout.splitlines() if line.startswith('CACHE=')))
    assert ' ' in cache and '\\' in cache, (cache, addopts)
    assert shlex.split(addopts)[-2:] == ['-o', 'cache_dir=' + cache]
    assert Path(cache).parent == parent
    assert not Path(cache).exists()


@pytest.mark.parametrize('outcome', ['failure', 'timeout'])
def test_pytest_cache_is_removed_after_unsuccessful_run(tmp_path, outcome):
    (tmp_path / 'test_cache.py').write_text(
        'from pathlib import Path\nimport time\n'
        'def test_cache(request):\n'
        '    cache = Path(request.config.getini("cache_dir")).resolve()\n'
        '    cache.mkdir(parents=True, exist_ok=True)\n'
        '    print("CACHE=" + str(cache), flush=True)\n'
        + ('    time.sleep(20)\n' if outcome == 'timeout' else '    assert False\n'))
    run = store.run_test([PYTHON, '-m', 'pytest', '-q', '-s'], tmp_path,
                         5 if outcome == 'timeout' else None)
    assert run.returncode == (124 if outcome == 'timeout' else 1), run.stdout + run.stderr
    cache = Path(next(line[6:] for line in run.stdout.splitlines() if line.startswith('CACHE=')))
    assert not cache.exists()


def test_concurrent_pytest_runs_have_distinct_live_caches(tmp_path, monkeypatch):
    rendezvous = tmp_path / 'rendezvous'
    rendezvous.mkdir()
    monkeypatch.setenv('RENDEZVOUS', str(rendezvous))
    (tmp_path / 'test_cache.py').write_text(
        'import json, os, time\nfrom pathlib import Path\n'
        'def test_cache(request):\n'
        '    cache = Path(request.config.getini("cache_dir")).resolve()\n'
        '    cache.mkdir(parents=True, exist_ok=True)\n'
        '    ready = Path(os.environ["RENDEZVOUS"])\n'
        '    (ready / (str(os.getpid()) + ".json")).write_text(json.dumps(str(cache)))\n'
        '    deadline = time.monotonic() + 20\n'
        '    while len(list(ready.glob("*.json"))) != 2 and time.monotonic() < deadline:\n'
        '        time.sleep(0.01)\n'
        '    assert len(list(ready.glob("*.json"))) == 2\n'
        '    assert cache.is_dir()\n')
    with ThreadPoolExecutor(max_workers=2) as pool:
        runs = list(pool.map(lambda _: store.run_test([PYTHON, '-m', 'pytest', '-q'], tmp_path, 30), range(2)))
    for run in runs:
        assert run.returncode == 0, run.stdout + run.stderr
    caches = [Path(json.loads(path.read_text())) for path in rendezvous.glob('*.json')]
    assert len(set(caches)) == 2, caches
    assert all(not path.exists() for path in caches)
    assert not (tmp_path / '.pytest_cache').exists()


def test_pytest_temporary_cache_does_not_change_result_cache_key(local_repo):
    counter = local_repo.parent / 'runs.txt'
    (local_repo / 'test_count.py').write_text(
        'from pathlib import Path\ndef test_count():\n'
        f'    counter = Path({str(counter)!r})\n'
        '    counter.write_text(counter.read_text() + "run\\n" if counter.exists() else "run\\n")\n'
        '    assert counter.is_file()\n')
    (local_repo / '.gitignore').write_text('__pycache__/\n')
    commit_all(local_repo)
    command = [PYTHON, '-m', 'pytest', '-q']
    first = store.cached_test(command, local_repo)
    second = store.cached_test(command, local_repo)
    assert first.returncode == second.returncode == 0
    assert not first.cached and second.cached
    assert counter.read_text() == 'run\n'
    assert not (local_repo / '.pytest_cache').exists()
    assert git(local_repo, 'status', '--porcelain') == ''
