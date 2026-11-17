"""Installed source trees must never stand in for a comparison copy."""
import os
from pathlib import Path
import subprocess
import venv

import pytest

from test_environment import TEST_SITE

from workorder import gate, store
from test_workorder import PACKAGE, PYTHON, cli, git, local_repo, task
from test_v05 import commit_all
from test_v06b import fill_sections


# A local, dependency-free PEP 660 backend makes a genuine pip editable install
# without downloading build tools. The .pth points at the original src tree.
BACKEND = '''import pathlib, zipfile

def build_editable(wheel_directory, config_settings=None, metadata_directory=None):
    filename = 'wo_sample-1.0-py3-none-any.whl'
    with zipfile.ZipFile(pathlib.Path(wheel_directory) / filename, 'w') as z:
        z.writestr('wo_sample.pth', str(pathlib.Path('src').resolve()) + '\\n')
        z.writestr('wo_sample-1.0.dist-info/METADATA', 'Metadata-Version: 2.1\\nName: wo-sample\\nVersion: 1.0\\n')
        z.writestr('wo_sample-1.0.dist-info/WHEEL', 'Wheel-Version: 1.0\\nGenerator: local-test\\nRoot-Is-Purelib: true\\nTag: py3-none-any\\n')
        z.writestr('wo_sample-1.0.dist-info/RECORD', '')
    return filename
'''


def editable(root, tmp_path):
    environment = tmp_path / 'environment'
    venv.EnvBuilder(with_pip=True).create(environment)
    python = environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    site = subprocess.check_output([str(python), '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
    (Path(site) / 'test_tools.pth').write_text(str(TEST_SITE) + '\n')
    (root / 'src/wo_sample').mkdir(parents=True)
    (root / 'src/wo_sample/__init__.py').write_text('def value():\n    return 1\n')
    (root / 'build_backend.py').write_text(BACKEND)
    (root / 'pyproject.toml').write_text('[build-system]\nrequires = []\nbuild-backend = "build_backend"\nbackend-path = ["."]\n')
    (root / '.gitignore').write_text('__pycache__/\n.pytest_cache/\n')
    result = subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-build-isolation', '--no-deps', '-e', str(root)],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    return str(python)


def setup_task(root, python, test_text, kind=None):
    (root / 'tests').mkdir(exist_ok=True)
    if test_text:
        (root / 'tests/test_value.py').write_text(test_text)
    command = [python, '-m', 'pytest', '-q', 'tests/test_value.py']
    fill_sections(task(root, ships='none', test=store.join_command(command), kind=kind))
    base = commit_all(root)
    git(root, 'update-ref', 'refs/remotes/origin/main', base)
    git(root, 'checkout', '-qb', 'feature')
    return base, command


def test_editable_src_feature_reaches_done(local_repo, tmp_path):
    root = local_repo
    python = editable(root, tmp_path)
    setup_task(root, python, '')
    (root / 'src/wo_sample/__init__.py').write_text('def value():\n    return 2\n')
    (root / 'tests/test_value.py').write_text('from wo_sample import value\ndef test_value():\n    assert value() == 2\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'TASK-1 DONE:' in result.stdout


def test_broken_clone_cannot_use_editable_original_for_refactor(local_repo, tmp_path):
    root = local_repo
    python = editable(root, tmp_path)
    base, command = setup_task(root, python, 'from wo_sample import value\ndef test_value():\n    assert value() == 1\n')
    clone = tmp_path / 'clone'
    git(tmp_path, 'clone', '-q', str(root), str(clone))
    git(clone, 'config', 'user.name', 'tester')
    git(clone, 'config', 'user.email', 'tester@example.invalid')
    git(clone, 'update-ref', 'refs/remotes/origin/main', base)
    (clone / 'src/wo_sample/__init__.py').write_text('def value():\n    return 99\n')
    commit_all(clone)
    result = cli(clone, 'done', '--task', 'TASK-1', '--refactor')
    assert result.returncode == 1, result.stdout + result.stderr
    assert store.record(clone, 'TASK-1')['status'] != 'DONE'
    assert store.entries(clone, 'TASK-1') == []


@pytest.mark.parametrize('copy_kind', ['snapshot', 'ref'])
def test_copy_probe_refuses_external_package_even_with_cached_result(local_repo, tmp_path, monkeypatch, copy_kind):
    root = local_repo
    python = editable(root, tmp_path)
    base, command = setup_task(root, python, 'from wo_sample import value\ndef test_value():\n    assert value() == 1\n')
    # Seed a real successful cache entry, then change only interpreter startup
    # outside the repository. Import checks must run even on a cache hit.
    with store.test_reuse():
        destination = tmp_path / 'cache-seed'
        if copy_kind == 'ref':
            code, output = gate.run_ref_test(root, destination, base, command, None, None)
        else:
            code, output = gate.run_snapshot_test(root, destination, command, None, None)
    assert code == 0, output
    startup = tmp_path / 'startup'
    startup.mkdir()
    (startup / 'sitecustomize.py').write_text('import sys\nsys.path.insert(0, ' + repr(str(root / 'src')) + ')\n')
    monkeypatch.setenv('PYTHONPATH', str(startup))
    for n in range(2):
        with store.test_reuse():
            destination = tmp_path / f'copy{n}'
            if copy_kind == 'ref':
                code, output = gate.run_ref_test(root, destination, base, command, None, None)
            else:
                code, output = gate.run_snapshot_test(root, destination, command, None, None)
        assert isinstance(code, str) and 'outside the comparison copy' in code, (code, output)
        assert 'wo_sample' in code and str(root / 'src') in code


def test_init_notices_installed_src_package(local_repo, tmp_path):
    python = editable(local_repo, tmp_path)
    result = subprocess.run([python, '-m', 'workorder', 'init'], cwd=local_repo,
                            env=dict(os.environ, PYTHONPATH=str(PACKAGE)), capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'src layout' in result.stdout and 'wo_sample' in result.stdout


def test_outside_import_is_unavailable_and_task_label_accepts_it(local_repo, tmp_path):
    root = local_repo
    python = editable(root, tmp_path)
    (root / 'sitecustomize.py').write_text(
        'import sys\nfrom pathlib import Path\n'
        f'if Path.cwd() != Path({str(root)!r}): sys.path.insert(0, {str(root / "src")!r})\n')
    setup_task(root, python, '')
    (root / 'src/wo_sample/__init__.py').write_text('def value():\n    return 2\n')
    (root / 'tests/test_value.py').write_text('from wo_sample import value\ndef test_value():\n    assert value() == 2\n')
    commit_all(root)
    result = cli(root, 'done', '--task', 'TASK-1')
    assert result.returncode == 1
    assert 'could not compare with origin/main: the tests import wo_sample' in result.stdout
    assert "passes on origin/main's code too" not in result.stdout
    assert 'does not exercise the change' not in result.stdout
    result = cli(root, 'done', '--task', 'TASK-1', '--needs-review')
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count('outside the comparison copy') == 1, result.stdout
    refused = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests')
    assert refused.returncode == 1
    assert refused.stdout.count('outside the comparison copy') == 1, refused.stdout
    result = cli(root, 'check', '--base', 'origin/main', '--run-tests', '--all-tests', '--allow', 'TASK-1')
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize('prefix', [[], ['-W', 'ignore'], ['-X', 'dev'], ['--check-hash-based-pycs', 'default']])
def test_probe_uses_test_interpreters_isolation_flag(local_repo, tmp_path, prefix):
    root = local_repo
    python = editable(root, tmp_path)
    base, command = setup_task(root, python, 'from wo_sample import value\ndef test_value():\n    assert value() == 1\n')
    command[1:1] = [*prefix, '-I']
    code, output = gate.run_ref_test(root, tmp_path / 'isolated', base, command, None, None)
    assert isinstance(code, str) and 'outside the comparison copy' in code, (code, output)


def test_probe_checks_flat_namespace_packages(local_repo, tmp_path):
    root = local_repo
    python = editable(root, tmp_path)
    (root / 'acme/widget').mkdir(parents=True)
    (root / 'acme/widget/__init__.py').write_text('value = 1\n')
    site = subprocess.check_output([python, '-c', 'import sysconfig; print(sysconfig.get_path("purelib"))'], text=True).strip()
    (Path(site) / 'namespace.pth').write_text(str(root) + '\n')
    # Remove src metadata so only the flat namespace needs checking.
    import shutil
    shutil.rmtree(root / 'src')
    base, command = setup_task(root, python, 'from acme.widget import value\ndef test_value():\n    assert value == 1\n')
    command.insert(1, '-I')
    code, output = gate.run_ref_test(root, tmp_path / 'namespace-copy', base, command, None, None)
    assert isinstance(code, str) and 'tests import acme' in code and 'outside the comparison copy' in code, (code, output)


def test_windows_pip_launcher_uses_embedded_interpreter(tmp_path):
    from workorder.python_imports import interpreter
    # The PE header is binary; pip appends a UTF-8 shebang before the zip
    # payload. Exercise that file format without running Windows code on Linux.
    launcher = tmp_path / 'pytest.exe'
    binary = b'MZ\x90\x00\xff' + b'\0' * 128
    launcher.write_bytes(binary + b'#!"C:\\Program Files\\Python\\python.exe"\nPK\x03\x04')
    assert interpreter([str(launcher)], tmp_path, os.environ) == [r'C:\Program Files\Python\python.exe']


@pytest.mark.parametrize('copy_kind', ['ref', 'staged', 'overlay', 'ref-overlay'])
def test_copy_cache_probes_namespace_removed_from_current_tree(local_repo, tmp_path, monkeypatch, copy_kind):
    import shutil
    root = local_repo
    python = editable(root, tmp_path)
    shutil.rmtree(root / 'src')
    (root / 'acme').mkdir()
    (root / 'acme/widget.py').write_text('value = 1\n')
    base, command = setup_task(root, python, 'from acme.widget import value\ndef test_value():\n    assert value == 1\n')
    shutil.rmtree(root / 'acme')
    if copy_kind != 'staged':
        commit_all(root)
    def compare(destination):
        if copy_kind == 'ref':
            return gate.run_ref_test(root, destination, base, command, None, None)
        if copy_kind == 'ref-overlay':
            return gate.run_ref_test(root, destination, store.head(root), command, None, None,
                overlays={'acme/widget.py': (b'value = 1\n', 0o100644)})
        if copy_kind == 'overlay':
            return gate.run_snapshot_test(root, destination, command, None, None,
                overlays={'acme/widget.py': (b'value = 1\n', 0o100644)})
        return gate.run_snapshot_test(root, destination, command, None, None, staged=True)
    monkeypatch.setenv('PYTHONDONTWRITEBYTECODE', '1')
    with store.test_reuse():
        code, output = compare(tmp_path / 'cache-seed')
    assert code == 0, output
    outside = tmp_path / 'outside'
    (outside / 'acme').mkdir(parents=True)
    (outside / 'acme/__init__.py').write_text('')
    (outside / 'acme/widget.py').write_text('value = 1\n')
    startup = tmp_path / 'startup'
    startup.mkdir()
    (startup / 'sitecustomize.py').write_text('import sys\nsys.path.insert(0, ' + repr(str(outside)) + ')\n')
    monkeypatch.setenv('PYTHONPATH', str(startup))
    with store.test_reuse():
        code, output = compare(tmp_path / 'comparison-copy')
    assert isinstance(code, str) and 'tests import acme' in code and 'outside the comparison copy' in code, (code, output)
